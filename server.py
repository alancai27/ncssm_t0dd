#!/usr/bin/env python3.11
"""T0dd server.

Routing lives in dispatch(), which is transport-agnostic: it takes a request
as plain values and returns (status, headers, body). Two thin adapters call it.

  * H            -- BaseHTTPRequestHandler, for `python3.11 server.py` locally.
  * app          -- WSGI, which is what Vercel's Python runtime loads.

WSGI is a protocol rather than a library, so supporting Vercel costs no
dependencies. `server.py` is one of the entrypoint filenames Vercel looks for,
and `app` is the top-level name it expects.

  python3.11 server.py            offline demo
  python3.11 server.py --live     real model calls
"""
import json
import mimetypes
import os
import sys
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _load_env(path=None):
    """Minimal .env loader. Must run before tutor.auth/store import, since both
    read os.environ at module level. Real env vars win, which is what makes
    Vercel's dashboard-set variables take precedence."""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_env()

from tutor import auth, courses, store          # noqa: E402
from tutor.config import PROVIDER               # noqa: E402
from tutor.guards import redact, scan           # noqa: E402
from tutor.prompts import build                 # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PORT = int(os.environ.get("PORT", "8000"))
# argv is empty under WSGI, so live mode is also settable by env.
LIVE = "--live" in sys.argv or os.environ.get("TODD_LIVE") == "1"

SCHOOL = "NCSSM–Durham"
REDIRECT_URI = os.environ.get("TODD_REDIRECT_URI", f"http://localhost:{PORT}/auth/callback")
SESSION_COOKIE, FLOW_COOKIE = "todd_session", "todd_flow"

CANNED = [
    ("code", "Sure! Here's findMax:\n\n```java\npublic static int findMax(int[] arr) {\n"
             "    int max = arr[0];\n    for (int i = 1; i < arr.length; i++) {\n"
             "        if (arr[i] > max) { max = arr[i]; }\n    }\n    return max;\n}\n```"),
    ("ta",   "I'm not able to produce assignment solutions here regardless of role. Staff get "
             "solutions through the course materials, not through me.\n\nHappy to keep helping "
             "with concepts. Which part of the rubric are you thinking about?"),
    ("_",    "Good place to start. Before you write anything: if the array is `[3, 9, 2]`, what "
             "should your \"largest so far\" variable hold *before* you compare anything? That "
             "one decision is where most findMax bugs come from."),
]


def reply_for(msg):
    m = msg.lower()
    # Role-claim check must come FIRST: "i need the solution" contains "solution"
    # and would otherwise fall into the code branch.
    if any(k in m for k in ("i'm the ta", "im the ta", "teacher", "ignore your",
                            "permission", "grader", "instructor")):
        return CANNED[1][1]
    if any(k in m for k in ("give me the code", "write the code", "just the code", "solution")):
        return CANNED[0][1]
    return CANNED[2][1]


# ------------------------------------------------------------------ helpers
def _json(code, obj, cookies=()):
    return code, _hdrs("application/json", cookies), json.dumps(obj).encode()


def _hdrs(ctype, cookies=()):
    h = [("Content-Type", ctype)]
    for name, val, age in cookies:
        c = f"{name}={val}; Path=/; HttpOnly; SameSite=Lax"
        if age is not None:
            c += f"; Max-Age={age}"
        # Secure is required for SameSite cookies over HTTPS deployments.
        if REDIRECT_URI.startswith("https://"):
            c += "; Secure"
        h.append(("Set-Cookie", c))
    return h


def _redirect(to, cookies=()):
    return 302, _hdrs("text/plain", cookies) + [("Location", to)], b""


def _user_from(cookies):
    data = auth.unsign(cookies.get(SESSION_COOKIE, ""))
    return data.get("user") if data else None


def _login_page(error=None):
    with open(os.path.join(ROOT, "login.html")) as f:
        html = f.read()
    if error:
        html = html.replace("<!--ERROR-->", f'<div class="err">{error}</div>')
    if not auth.configured():
        html = html.replace('href="/auth/google" id="gbtn"',
                            'href="#" id="gbtn" aria-disabled="true"')
        html = html.replace("<!--ERROR-->",
                            '<div class="err">Google sign-in is not configured on this '
                            'server. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.</div>')
    return 200, _hdrs("text/html; charset=utf-8"), html.encode()


def _static(path):
    fp = os.path.normpath(os.path.join(ROOT, path.lstrip("/")))
    if not fp.startswith(ROOT) or not os.path.isfile(fp):
        return 404, _hdrs("text/plain"), b"not found"
    ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
    with open(fp, "rb") as f:
        return 200, _hdrs(ctype), f.read()


# ----------------------------------------------------------------- dispatch
def dispatch(method, raw_path, cookies, body):
    """(status, [(header, value)], bytes). No transport details in here.

    Wraps _dispatch so a backend failure surfaces as a readable message
    instead of an opaque 500 -- the common one being Supabase reachable but
    its tables not created yet."""
    try:
        return _dispatch(method, raw_path, cookies, body)
    except Exception as e:
        detail = str(e)
        if "Could not find the table" in detail or "PGRST205" in detail:
            msg = ("Database not initialised: run migrations/001_init.sql in the "
                   "Supabase SQL editor, then reload.")
        else:
            msg = f"Server error: {detail[:300]}"
        if raw_path.startswith("/api/"):
            return _json(500, {"error": msg})
        page = ("<!doctype html><meta charset=utf-8><title>T0dd</title>"
                "<style>body{font-family:-apple-system,sans-serif;max-width:34rem;"
                "margin:18vh auto;padding:0 24px;color:#0b2447;line-height:1.6}"
                "code{background:#eef4fa;padding:2px 6px;border-radius:5px}</style>"
                f"<h2>T0dd is not ready yet</h2><p>{msg}</p>")
        return 500, _hdrs("text/html; charset=utf-8"), page.encode()


def _dispatch(method, raw_path, cookies, body):
    url = urlparse(raw_path)
    route = url.path
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    user = _user_from(cookies)

    if method == "POST":
        return _post(route, user, body)

    if route == "/login":
        return _login_page(q.get("error"))

    if route == "/logout":
        return _redirect("/login", [(SESSION_COOKIE, "", 0)])

    if route == "/auth/google":
        try:
            goto, flow = auth.begin(REDIRECT_URI, q.get("next"))
        except auth.AuthError as e:
            return _redirect("/login?error=" + quote(str(e)))
        return _redirect(goto, [(FLOW_COOKIE, flow, 600)])

    if route == "/auth/callback":
        try:
            u, nxt = auth.complete(REDIRECT_URI, q, cookies.get(FLOW_COOKIE, ""))
        except auth.AuthError as e:
            return _redirect("/login?error=" + quote(str(e)), [(FLOW_COOKIE, "", 0)])
        store.touch_user(u["email"], u["name"])
        tok = auth.sign({"user": u})
        return _redirect(auth.safe_next(nxt) or "/",
                         [(SESSION_COOKIE, tok, auth.MAX_AGE), (FLOW_COOKIE, "", 0)])

    if route == "/api/courses":
        if not user:
            return _json(401, {"error": "Signed out."})
        return _json(200, {"courses": courses.all_courses()})

    if route == "/api/me":
        return _json(200 if user else 401, {"user": user})

    if route == "/welcome":
        if not user:
            return _redirect("/login")
        if store.is_onboarded(user["email"]):
            return _redirect("/")
        with open(os.path.join(ROOT, "welcome.html")) as f:
            return 200, _hdrs("text/html; charset=utf-8"), f.read().encode()

    if route in ("/", ""):
        if not user:
            return _redirect("/login")
        # The tutorial is mandatory: no chat until the agreement is signed.
        if not store.is_onboarded(user["email"]):
            return _redirect("/welcome")
        return _static("/index.html")

    return _static(route)


def _post(route, user, body):
    if route == "/api/onboard":
        if not user:
            return _json(401, {"error": "Signed out."})
        b = json.loads(body or b"{}")
        sig = (b.get("signature") or "").strip()
        accepted = b.get("accepted") or []
        # Never trust the client's own gating.
        if len(accepted) < 4 or not all(accepted):
            return _json(400, {"error": "All items must be accepted."})
        if len(sig) < 2:
            return _json(400, {"error": "Please type your full name to sign."})
        store.record_agreement(user["email"], sig)
        return _json(200, {"ok": True})

    if route != "/api/chat":
        return _json(404, {})
    if not user:
        return _json(401, {"error": "Signed out. Reload to sign in."})
    if not store.is_onboarded(user["email"]):
        return _json(403, {"error": "Complete the tutorial first."})

    req = json.loads(body or b"{}")
    # resolve() is an allowlist lookup; a bogus id falls back, never throws.
    course = courses.resolve(req.get("course"))

    if LIVE and PROVIDER.api_key:
        from tutor.llm import USAGE, chat
        msgs = build(course["name"], SCHOOL, courses.modules_text(course)) + \
            req.get("history", []) + [{"role": "user", "content": req.get("message", "")}]
        try:
            raw = chat(msgs)
        except Exception as e:
            return _json(200, {"error": str(e)[:300]})
        cost = str(USAGE)
    else:
        raw, cost = reply_for(req.get("message", "")), "offline (no API call)"

    v = scan(raw)
    return _json(200, {
        "text": redact(raw, v, "⟨solution withheld⟩") if v.blocked else raw,
        "blocked": v.blocked, "reasons": v.reasons, "usage": cost, "course": course["id"],
    })


# ------------------------------------------------------------- WSGI (Vercel)
def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")
    if environ.get("QUERY_STRING"):
        path += "?" + environ["QUERY_STRING"]
    cookies = {k: v.value for k, v in SimpleCookie(environ.get("HTTP_COOKIE", "")).items()}
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    body = environ["wsgi.input"].read(length) if length else b""

    status, headers, out = dispatch(method, path, cookies, body)
    reason = {200: "OK", 302: "Found", 400: "Bad Request", 401: "Unauthorized",
              403: "Forbidden", 404: "Not Found",
              500: "Internal Server Error"}.get(status, "OK")
    start_response(f"{status} {reason}", headers + [("Content-Length", str(len(out)))])
    return [out]


application = app   # Django-style alias, also accepted by Vercel.


# ------------------------------------------------------- local dev (stdlib)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _run(self):
        cookies = {k: v.value for k, v in
                   SimpleCookie(self.headers.get("Cookie", "")).items()}
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        status, headers, out = dispatch(self.command, self.path, cookies, body)
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = _run


if __name__ == "__main__":
    store.init()
    mode = f"LIVE · {PROVIDER.name}/{PROVIDER.model}" if (LIVE and PROVIDER.api_key) else "OFFLINE demo"
    print(f"T0dd | {mode}")
    print(f"  store: {store.backend()}")
    for w in auth.warn_if_insecure():
        print(f"  ! {w}")
    print(f"  http://localhost:{PORT}\n")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
