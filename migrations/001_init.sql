-- T0dd schema for Supabase/Postgres. Mirrors tutor/store.py's SQLite schema.
-- Run once in the Supabase SQL editor.

create table if not exists users (
  email             text primary key,
  tenant            text not null default 'ncssm-durham',
  name              text,
  first_seen        bigint not null,
  onboarded_at      bigint,
  agreement_version text,
  signature         text
);

-- Append-only history, so a re-accept after a wording change stays visible.
create table if not exists agreements (
  id          bigserial primary key,
  email       text not null,
  tenant      text not null default 'ncssm-durham',
  version     text not null,
  signature   text,
  accepted_at bigint not null
);

create index if not exists idx_agreements_email on agreements(email);

-- These tables hold student emails and typed signatures. RLS is enabled with
-- NO policies, which denies every key except the service key the server uses.
-- Without this, anon/publishable keys could read the whole student roster.
alter table users      enable row level security;
alter table agreements enable row level security;
