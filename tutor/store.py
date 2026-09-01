"""SQLite persistence.

Exists first for onboarding + the academic-integrity agreement, because an
agreement nobody can audit is theatre. Every acceptance is recorded with who,
when, what they typed, and WHICH VERSION of the agreement text they saw --
that last one matters: if the wording changes, prior consent doesn't silently
carry over to terms the student never read.

Tenancy is in the schema from the first migration (see the tenant column).
Adding it later is one of the genuinely painful migrations; adding it now is
one column.
"""
import os
import sqlite3
import time

DB_PATH = os.environ.get(
    "TODD_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "todd.db"))

# Bump when the agreement text changes -- students must re-accept.
AGREEMENT_VERSION = "2026-08-31.1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  email             TEXT PRIMARY KEY,
  tenant            TEXT NOT NULL DEFAULT 'ncssm-durham',
  name              TEXT,
  first_seen        INTEGER NOT NULL,
  onboarded_at      INTEGER,
  agreement_version TEXT,
  signature         TEXT
);
CREATE TABLE IF NOT EXISTS agreements (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  email    TEXT NOT NULL,
  tenant   TEXT NOT NULL DEFAULT 'ncssm-durham',
  version  TEXT NOT NULL,
  signature TEXT,
  accepted_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agreements_email ON agreements(email);
"""


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.executescript(SCHEMA)


def touch_user(email, name, tenant="ncssm-durham"):
    """Record first sight of a user; harmless on every login."""
    with _conn() as c:
        c.execute(
            "INSERT INTO users (email, tenant, name, first_seen) VALUES (?,?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET name=excluded.name",
            (email, tenant, name, int(time.time())))


def is_onboarded(email):
    """True only if they accepted the CURRENT agreement version."""
    with _conn() as c:
        row = c.execute(
            "SELECT onboarded_at, agreement_version FROM users WHERE email=?",
            (email,)).fetchone()
    return bool(row and row["onboarded_at"] and
                row["agreement_version"] == AGREEMENT_VERSION)


def record_agreement(email, signature, tenant="ncssm-durham"):
    now = int(time.time())
    with _conn() as c:
        c.execute(
            "UPDATE users SET onboarded_at=?, agreement_version=?, signature=? WHERE email=?",
            (now, AGREEMENT_VERSION, signature, email))
        # Append-only history, so a re-accept after a wording change is visible.
        c.execute(
            "INSERT INTO agreements (email, tenant, version, signature, accepted_at) "
            "VALUES (?,?,?,?,?)",
            (email, tenant, AGREEMENT_VERSION, signature, now))
    return now


def acceptance_log(limit=100):
    """For a future instructor view."""
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT email, version, signature, accepted_at FROM agreements "
            "ORDER BY accepted_at DESC LIMIT ?", (limit,))]
