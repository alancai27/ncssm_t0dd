"""Sign in with Google, @ncssm.edu only.

Port of ncssm_lost_and_found/auth.py to the stdlib (no Flask, no requests) so
this app keeps its zero-dependency property. The security properties are the
same ones that file establishes, and they are the ones that matter:

  * OpenID Connect authorization-code flow with PKCE, which is what NCSSM
    Google accounts run on.
  * The domain restriction is enforced SERVER-SIDE against the verified email
    Google returns. The `hd` parameter only pre-filters Google's own account
    chooser -- it is trivially removed by the user and is not a security
    control, so nothing here relies on it.
  * `state` is compared with compare_digest, and post-login redirects are
    restricted to same-site paths.
"""
import base64, hashlib, hmac, json, os, secrets, time
import urllib.parse, urllib.request, urllib.error

ALLOWED_DOMAIN = os.environ.get("ALLOWED_EMAIL_DOMAIN", "ncssm.edu").strip().lower()
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPE = "openid email profile"
TIMEOUT = 20
MAX_AGE = 60 * 60 * 12

# Regenerated each boot unless pinned, so restarts drop sessions in dev.
SECRET = os.environ.get("TODD_SECRET_KEY", secrets.token_hex(32)).encode()


class AuthError(RuntimeError):
    pass


def configured():
    return bool(CLIENT_ID and CLIENT_SECRET)


def is_allowed_email(email):
    email = (email or "").strip().lower()
    if email.count("@") != 1:
        return False
    local, _, domain = email.partition("@")
    return bool(local) and domain == ALLOWED_DOMAIN


def safe_next(target):
    """Only ever redirect back to a path on this site, never an absolute URL."""
    if not target:
        return None
    parsed = urllib.parse.urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/"):
        return None
    return target


# ---------------------------------------------------------------- cookies
def _b64e(b): return base64.urlsafe_b64encode(b).decode().rstrip("=")
def _b64d(s): return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(payload: dict) -> str:
    payload = {**payload, "exp": int(time.time()) + MAX_AGE}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    mac = _b64e(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    return f"{body}.{mac}"


def unsign(token: str):
    if not token or "." not in token:
        return None
    body, _, mac = token.partition(".")
    expect = _b64e(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expect, mac):
        return None
    try:
        data = json.loads(_b64d(body))
    except Exception:
        return None
    return None if data.get("exp", 0) < time.time() else data


# ------------------------------------------------------------------ flow
def _pkce_pair():
    verifier = _b64e(secrets.token_bytes(48))
    challenge = _b64e(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def begin(redirect_uri, next_url=None):
    """Return (google_url, flow_cookie). The cookie carries state+verifier."""
    if not configured():
        raise AuthError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not set.")
    state = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Convenience only -- see module docstring. The real check is on email.
        "hd": ALLOWED_DOMAIN,
        "prompt": "select_account",
    }
    flow = sign({"state": state, "verifier": verifier, "next": safe_next(next_url)})
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}", flow


def _post_json(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def complete(redirect_uri, query: dict, flow_cookie: str):
    """Validate the callback, exchange the code, return the user dict.
    Raises AuthError with a message that is safe to show the user."""
    if query.get("error"):
        raise AuthError(query.get("error_description") or query["error"])

    flow = unsign(flow_cookie)
    if not flow:
        raise AuthError("That sign-in link expired. Try again.")
    if not secrets.compare_digest(flow.get("state", ""), query.get("state", "")):
        raise AuthError("That sign-in link expired or was tampered with. Try again.")

    code = query.get("code")
    if not code:
        raise AuthError("Google did not return an authorization code.")

    try:
        tok = _post_json(TOKEN_URL, {
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": redirect_uri, "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET, "code_verifier": flow.get("verifier", ""),
        })
    except urllib.error.HTTPError as e:
        raise AuthError(
            f"Google rejected the token exchange ({e.code}). Usually the client "
            f"secret is wrong, or the redirect URI here does not exactly match "
            f"the one registered in Google Cloud Console."
        ) from None

    access_token = tok.get("access_token")
    if not access_token:
        raise AuthError("Google did not return an access token.")

    req = urllib.request.Request(USERINFO_URL,
                                 headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            info = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise AuthError(f"Could not read your Google profile ({e.code}).") from None

    email = (info.get("email") or "").strip().lower()
    if not info.get("email_verified", False):
        raise AuthError("That Google account's email address is not verified.")
    if not is_allowed_email(email):
        raise AuthError(
            f"{email or 'That account'} is not an @{ALLOWED_DOMAIN} address. "
            f"Sign in with your school Google account."
        )
    return {"email": email,
            "name": (info.get("name") or email.split("@")[0]).strip()}, flow.get("next")


def warn_if_insecure():
    out = []
    if not configured():
        out.append("Google sign-in is NOT configured "
                   "(set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET).")
    if not os.environ.get("TODD_SECRET_KEY"):
        out.append("TODD_SECRET_KEY unset: sessions drop on restart.")
    return out
