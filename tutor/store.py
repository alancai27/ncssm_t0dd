"""Persistence: users and academic-integrity agreements.

Two backends behind one API:

  * SQLite      -- local development, zero setup.
  * Supabase    -- anywhere the filesystem is ephemeral. Vercel functions do
                   not persist writes between invocations, so SQLite there
                   would accept a signature and silently lose it. Since these
                   rows ARE the auditable record of who agreed to what, losing
                   them quietly is the worst failure this app has.

Supabase is reached over PostgREST with urllib, so the stdlib-only property
survives -- no psycopg2, no supabase-py.

Every acceptance records WHICH VERSION of the agreement text was shown. If the
wording changes, AGREEMENT_VERSION moves and prior consent does not carry over
to terms the student never read.
"""
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

AGREEMENT_VERSION = "2026-08-31.1"
DEFAULT_TENANT = "ncssm-durham"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)

DB_PATH = os.environ.get("TODD_DB", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "todd.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  email TEXT PRIMARY KEY, tenant TEXT NOT NULL DEFAULT 'ncssm-durham',
  name TEXT, first_seen INTEGER NOT NULL, onboarded_at INTEGER,
  agreement_version TEXT, signature TEXT);
CREATE TABLE IF NOT EXISTS agreements (
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL,
  tenant TEXT NOT NULL DEFAULT 'ncssm-durham', version TEXT NOT NULL,
  signature TEXT, accepted_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_agreements_email ON agreements(email);
"""


# ----------------------------------------------------------------- supabase
def _rest(method, table, params="", body=None, prefer=None, timeout=15):
    url = f"{SUPABASE_URL}/rest/v1/{table}" + (f"?{params}" if params else "")
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else []
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"supabase {method} {table}: {e.code} "
                           f"{e.read().decode()[:200]}") from None


def _q(v):
    return urllib.parse.quote(str(v), safe="")


# --------------------------------------------------------------------- api
def init():
    """SQLite needs its tables made; Supabase's come from migrations/001_init.sql."""
    if USE_SUPABASE:
        return
    with sqlite3.connect(DB_PATH, timeout=10) as c:
        c.executescript(SCHEMA)


def touch_user(email, name, tenant=DEFAULT_TENANT):
    now = int(time.time())
    if USE_SUPABASE:
        # Insert only if absent: a plain upsert would reset first_seen on
        # every login.
        rows = _rest("GET", "users", f"email=eq.{_q(email)}&select=email")
        if not rows:
            _rest("POST", "users", body={
                "email": email, "tenant": tenant, "name": name, "first_seen": now},
                prefer="resolution=ignore-duplicates,return=minimal")
        return
    with sqlite3.connect(DB_PATH, timeout=10) as c:
        c.execute("INSERT INTO users (email, tenant, name, first_seen) VALUES (?,?,?,?) "
                  "ON CONFLICT(email) DO UPDATE SET name=excluded.name",
                  (email, tenant, name, now))


def is_onboarded(email):
    """True only if they accepted the CURRENT agreement version."""
    if USE_SUPABASE:
        rows = _rest("GET", "users",
                     f"email=eq.{_q(email)}&select=onboarded_at,agreement_version")
        row = rows[0] if rows else None
        return bool(row and row.get("onboarded_at")
                    and row.get("agreement_version") == AGREEMENT_VERSION)
    with sqlite3.connect(DB_PATH, timeout=10) as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT onboarded_at, agreement_version FROM users "
                        "WHERE email=?", (email,)).fetchone()
    return bool(row and row["onboarded_at"]
                and row["agreement_version"] == AGREEMENT_VERSION)


def record_agreement(email, signature, tenant=DEFAULT_TENANT):
    now = int(time.time())
    if USE_SUPABASE:
        _rest("PATCH", "users", f"email=eq.{_q(email)}", body={
            "onboarded_at": now, "agreement_version": AGREEMENT_VERSION,
            "signature": signature}, prefer="return=minimal")
        _rest("POST", "agreements", body={
            "email": email, "tenant": tenant, "version": AGREEMENT_VERSION,
            "signature": signature, "accepted_at": now}, prefer="return=minimal")
        return now
    with sqlite3.connect(DB_PATH, timeout=10) as c:
        c.execute("UPDATE users SET onboarded_at=?, agreement_version=?, signature=? "
                  "WHERE email=?", (now, AGREEMENT_VERSION, signature, email))
        c.execute("INSERT INTO agreements (email, tenant, version, signature, accepted_at) "
                  "VALUES (?,?,?,?,?)", (email, tenant, AGREEMENT_VERSION, signature, now))
    return now


def acceptance_log(limit=100):
    """For a future instructor view."""
    if USE_SUPABASE:
        return _rest("GET", "agreements",
                     f"select=email,version,signature,accepted_at"
                     f"&order=accepted_at.desc&limit={int(limit)}")
    with sqlite3.connect(DB_PATH, timeout=10) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(
            "SELECT email, version, signature, accepted_at FROM agreements "
            "ORDER BY accepted_at DESC LIMIT ?", (limit,))]


def backend():
    return "supabase" if USE_SUPABASE else f"sqlite:{DB_PATH}"
