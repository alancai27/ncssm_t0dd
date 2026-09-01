# T0dd

A CS tutor chatbot for NCSSM. It answers questions about course material and
explains concepts, but it does not produce solutions to assignments.

Runs on the Python standard library only. No pip install, no node, no build step.

## Running it

```bash
cp .env.example .env      # fill in the values
python3.11 server.py      # http://localhost:8000
```

## Deploying

Routing lives in `dispatch()`, which is transport-agnostic. Two adapters call
it: `H` (BaseHTTPRequestHandler) for local dev and `app` (WSGI) for Vercel.
WSGI is a protocol, not a library, so this costs no dependencies.

Vercel functions have an ephemeral filesystem, so SQLite there would accept a
signature and silently lose it. Set `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`
and `tutor/store.py` switches to Postgres over PostgREST (still stdlib-only,
via urllib). Run `migrations/001_init.sql` in the Supabase SQL editor first.

Required env on the host: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`TODD_SECRET_KEY`, `TODD_REDIRECT_URI` (must exactly match a URI registered on
the OAuth client), `TODD_LIVE=1`, `GEMINI_API_KEY`, plus the two Supabase vars.

## How the guardrails work

The rule is not "no code". A student stuck on `ArrayList` syntax is not cheating,
and refusing that just sends them to ChatGPT. The rule is "no assignment-shaped
solutions", enforced in layers:

1. **System prompt + few-shot** (`tutor/prompts.py`) sets the tutoring behavior and
   includes worked refusals for common pressure tactics.
2. **Deterministic output filter** (`tutor/guards.py`) parses what the model
   actually returned and blocks on *structure*: control flow, function
   definitions, or length past a per-course threshold. Python is checked with a
   real `ast.parse`; C-family syntax falls back to heuristics. This is the only
   layer that does not depend on the model cooperating.
3. **Onboarding gate** (`web/welcome.html`) requires every student to read a
   tutorial and sign an academic integrity agreement before the chat responds.

### Known gap

`guards.scan()` cannot detect a solution written as prose. "Loop through the
array, track the largest, return it" is an answer in English and passes the
filter. Closing that is the job of an intent classifier, which is not built yet.
A clean pass from `scan()` does not mean a response was safe.

## Auth

Google OpenID Connect, authorization-code flow with PKCE, ported from
`ncssm_lost_and_found`. The `@ncssm.edu` restriction is enforced **server-side**
against the verified email Google returns. The `hd` parameter only pre-filters
Google's account chooser; a student can strip it, so nothing relies on it.

## Agreement records

`todd.db` (SQLite, gitignored) records each acceptance with the email, timestamp,
typed signature, and **which version of the agreement text was shown**. Bumping
`AGREEMENT_VERSION` in `tutor/store.py` forces everyone to re-accept, so reworded
terms never inherit consent that was given for different language.

## Layout

```
server.py          dispatch() + local and WSGI adapters
migrations/        Postgres schema for Supabase
tutor/guards.py    deterministic code detector  (tests: tests/test_guards.py)
tutor/auth.py      Google OIDC + PKCE, stdlib port
tutor/store.py     users + agreements; SQLite or Supabase
tutor/courses.py   course catalog + id allowlist
tutor/prompts.py   system prompt + few-shot
tutor/config.py    model backends, one swap point
tutor/llm.py       OpenAI-compatible client + cost meter
web/               login, tutorial, chat UI
```

## Not built yet

- Intent classifier (the prose gap above)
- Transcript logging, though the tutorial tells students their instructor can
  read conversations. **That promise is not yet backed by anything.**
- Course modules. `content/courses.json` has the real NCSSM CS course list, but
  every `modules` array is empty. With no modules loaded, `prompts.build()`
  swaps the "cite module IDs" rule for an explicit do-not-invent instruction,
  so T0dd explains concepts without fabricating citations. Fill the arrays in
  to turn citations back on, per course.

## Credits

Visual design ported from [techinance](https://github.com/alancai27/techinance).
