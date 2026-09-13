-- Keep Watchdog's tables out of Supabase's Data API.
--
-- Supabase publishes every table in the `public` schema through PostgREST at a
-- URL anyone can reach with the anon key, and that key is meant to be public.
-- Watchdog's tables hold buyer names, screening evidence and a colleague's
-- decisions, and nothing in them should ever be readable that way.
--
-- Run this in the Supabase SQL editor AFTER the first `alembic upgrade head`,
-- and again after any migration that adds a table.
--
-- It takes privileges away from the two API roles only. It creates nothing,
-- drops nothing and changes no data. Watchdog connects as `postgres`, which owns
-- the tables and is unaffected.

-- 1. The API roles lose the schema and everything currently in it.
revoke usage on schema public from anon, authenticated;

revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke all on all functions in schema public from anon, authenticated;

-- 2. And everything added to it later, by whoever adds it.
alter default privileges in schema public revoke all on tables from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;
alter default privileges in schema public revoke all on functions from anon, authenticated;

alter default privileges for role postgres in schema public
  revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke all on sequences from anon, authenticated;

-- 3. Row level security as a second answer to the same question. A table owner
--    is not subject to it unless FORCE is used, so Watchdog reads and writes
--    exactly as before; anything arriving as anon or authenticated sees nothing
--    even if a privilege is granted back by accident later.
alter table if exists public.tender             enable row level security;
alter table if exists public.tender_change      enable row level security;
alter table if exists public.tender_detail      enable row level security;
alter table if exists public.screening_result   enable row level security;
alter table if exists public.review             enable row level security;
alter table if exists public.run                enable row level security;
alter table if exists public.job_lock           enable row level security;
alter table if exists public.watermark          enable row level security;
alter table if exists public.quarantine         enable row level security;
alter table if exists public.alembic_version    enable row level security;

-- 4. Check it. Every row of this must read false, false.
select
    c.relname                                            as table_name,
    has_table_privilege('anon',          c.oid, 'SELECT') as anon_can_read,
    has_table_privilege('authenticated', c.oid, 'SELECT') as authenticated_can_read
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relkind = 'r'
order by c.relname;
