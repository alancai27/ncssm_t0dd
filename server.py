#!/usr/bin/env python3.11
"""Local dev server. Stdlib only -- no flask, no node, no build step.

  python3.11 server.py          -> http://localhost:8000
  OPENROUTER_API_KEY=... python3.11 server.py --live
"""
import json, os, sys, mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def _load_env(path=None):
    """Minimal .env loader. Must run before tutor.auth imports, since that
    module reads os.environ at module level. Real env vars win."""
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

from http.cookies import SimpleCookie
from urllib.parse import urlparse, parse_qs, quote

from tutor import auth, store
from tutor.config import PROVIDER
from tutor.guards import scan, redact
from tutor.prompts import build

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
LIVE = "--live" in sys.argv
PORT = int(os.environ.get("PORT", "8000"))

COURSE, SCHOOL = "CS 2100 · Data Structures", "NCSSM–Durham"
REDIRECT_URI = os.environ.get("TODD_REDIRECT_URI", f"http://localhost:{PORT}/auth/callback")
SESSION_COOKIE, FLOW_COOKIE = "todd_session", "todd_flow"
ASSIGNMENT = "PS4 Q1: implement findMax(int[] arr) returning the largest element. No library calls."
MODULES = """[Module 4.2] Loop invariants: what must be true before, during, after a loop.
[Module 4.3] Array traversal patterns: accumulator, running-extremum, early-exit.
[Module 2.1] Java array basics: .length, zero-indexing, bounds."""

# Offline canned replies so the UI is fully explorable with no API key.
CANNED = [
    ("code", "Sure! Here's findMax:\n\n```java\npublic static int findMax(int[] arr) {\n"
             "    int max = arr[0];\n    for (int i = 1; i < arr.length; i++) {\n"
             "        if (arr[i] > max) { max = arr[i]; }\n    }\n    return max;\n}\n```"),
    ("ta",   "I'm not able to produce assignment solutions here regardless of role. Staff get "
             "solutions through the course materials, not through me.\n\nHappy to keep helping "
             "with concepts. Which part of the rubric are you thinking about? [Module 4.2]"),
    ("_",    "Good place to start. Before you write anything: if the array is `[3, 9, 2]`, what "
             "should your \"largest so far\" variable hold *before* you compare anything? That "
             "one decision is where most findMax bugs come from. [Module 4.2]"),
]


def reply_for(msg: str) -> str:
    m = msg.lower()
    # Role-claim check must come FIRST: "i need the solution" contains "solution"
    # and would otherwise fall into the code branch.
    if any(k in m for k in ("i'm the ta", "im the ta", "teacher", "ignore your",
                            "permission", "grader", "instructor")):
        return CANNED[1][1]
    if any(k in m for k in ("give me the code", "write the code", "just the code", "solution")):
        return CANNED[0][1]
    return CANNED[2][1]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    # ---------------------------------------------------------- helpers
    def _cookies(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return {k: v.value for k, v in c.items()}

    def _user(self):
        data = auth.unsign(self._cookies().get(SESSION_COOKIE, ""))
        return data.get("user") if data else None

    def _redirect(self, to, cookies=()):
        self.send_response(302)
        self.send_header("Location", to)
        for name, val, age in cookies:
            flag = f"{name}={val}; Path=/; HttpOnly; SameSite=Lax"
            self.send_header("Set-Cookie", flag + (f"; Max-Age={age}" if age is not None else ""))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _login_page(self, error=None):
        with open(os.path.join(ROOT, "login.html")) as f:
            html = f.read()
        if error:
            html = html.replace("<!--ERROR-->", f'<div class="err">{error}</div>')
        if not auth.configured():
            html = html.replace('href="/auth/google" id="gbtn"',
                                'href="#" id="gbtn" aria-disabled="true"')
            html = html.replace("<!--ERROR-->",
                '<div class="err">Google sign-in is not configured on this server. '
                'Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.</div>')
        self._send(200, html, "text/html; charset=utf-8")

    # ------------------------------------------------------------ routes
    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        route = url.path

        if route == "/login":
            return self._login_page(q.get("error"))

        if route == "/logout":
            return self._redirect("/login", [(SESSION_COOKIE, "", 0)])

        if route == "/auth/google":
            try:
                goto, flow = auth.begin(REDIRECT_URI, q.get("next"))
            except auth.AuthError as e:
                return self._redirect("/login?error=" + quote(str(e)))
            return self._redirect(goto, [(FLOW_COOKIE, flow, 600)])

        if route == "/auth/callback":
            try:
                user, nxt = auth.complete(REDIRECT_URI, q, self._cookies().get(FLOW_COOKIE, ""))
            except auth.AuthError as e:
                return self._redirect("/login?error=" + quote(str(e)), [(FLOW_COOKIE, "", 0)])
            store.touch_user(user["email"], user["name"])
            tok = auth.sign({"user": user})
            return self._redirect(auth.safe_next(nxt) or "/",
                                  [(SESSION_COOKIE, tok, auth.MAX_AGE), (FLOW_COOKIE, "", 0)])

        if route == "/welcome":
            if not self._user():
                return self._redirect("/login")
            if store.is_onboarded(self._user()["email"]):
                return self._redirect("/")
            with open(os.path.join(ROOT, "welcome.html")) as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")

        if route == "/api/me":
            u = self._user()
            return self._send(200 if u else 401, json.dumps({"user": u}))

        # Everything else requires a session, except the logo/static assets
        # the login page itself needs.
        if route in ("/", ""):
            u = self._user()
            if not u:
                return self._redirect("/login")
            # The tutorial is mandatory: no chat until the agreement is signed.
            if not store.is_onboarded(u["email"]):
                return self._redirect("/welcome")

        path = "/index.html" if route in ("/", "") else route
        fp = os.path.normpath(os.path.join(ROOT, path.lstrip("/")))
        if not fp.startswith(ROOT) or not os.path.isfile(fp):
            return self._send(404, "not found", "text/plain")
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        with open(fp, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_POST(self):
        user = self._user()

        if self.path == "/api/onboard":
            if not user:
                return self._send(401, json.dumps({"error": "Signed out."}))
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or "{}")
            sig = (body.get("signature") or "").strip()
            accepted = body.get("accepted") or []
            # Never trust the client's own gating.
            if len(accepted) < 4 or not all(accepted):
                return self._send(400, json.dumps({"error": "All items must be accepted."}))
            if len(sig) < 2:
                return self._send(400, json.dumps({"error": "Please type your full name to sign."}))
            store.record_agreement(user["email"], sig)
            return self._send(200, json.dumps({"ok": True}))

        if self.path != "/api/chat":
            return self._send(404, "{}")
        if not user:
            return self._send(401, json.dumps({"error": "Signed out. Reload to sign in."}))
        if not store.is_onboarded(user["email"]):
            return self._send(403, json.dumps({"error": "Complete the tutorial first."}))
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or "{}")
        history = req.get("history", [])
        msg = req.get("message", "")

        if LIVE and PROVIDER.api_key:
            from tutor.llm import chat, USAGE
            msgs = build(COURSE, SCHOOL, ASSIGNMENT, MODULES) + history + \
                   [{"role": "user", "content": msg}]
            try:
                raw = chat(msgs)
            except Exception as e:
                return self._send(200, json.dumps({"error": str(e)[:300]}))
            cost = str(USAGE)
        else:
            raw, cost = reply_for(msg), "offline (no API call)"

        v = scan(raw)
        shown = redact(raw, v, "⟨solution withheld⟩") if v.blocked else raw
        self._send(200, json.dumps({
            "text": shown, "blocked": v.blocked, "reasons": v.reasons, "usage": cost,
        }))


if __name__ == "__main__":
    store.init()
    mode = f"LIVE · {PROVIDER.name}/{PROVIDER.model}" if (LIVE and PROVIDER.api_key) else "OFFLINE demo"
    print(f"T0dd | {mode}")
    for w in auth.warn_if_insecure():
        print(f"  ! {w}")
    print(f"  http://localhost:{PORT}\n")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
