-- Ephemeral Full Staging Phase B schema.
-- CI-only. Never apply this fixture to production.

drop table if exists public.research_tasks cascade;
drop table if exists public.analysis_runs cascade;
drop table if exists public.drama_overrides cascade;
drop table if exists public.collection_uploads cascade;

create table public.analysis_runs (
  id text primary key,
  collection_date date not null,
  platform text not null,
  upload_ids jsonb not null default '[]'::jsonb,
  status text not null default '',
  result_json jsonb not null default '{}'::jsonb,
  error text not null default '',
  model text not null default '',
  provider text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.research_tasks (
  id bigserial primary key,
  analysis_run_id text not null,
  collection_date date not null,
  platform text not null,
  rank integer not null,
  title text not null,
  normalized_title text not null,
  reason text[] not null default array[]::text[],
  missing_fields text[] not null default array[]::text[],
  status text not null default 'PENDING',
  priority integer not null default 0,
  context_json jsonb not null default '{}'::jsonb,
  research_json jsonb not null default '{}'::jsonb,
  sources jsonb not null default '[]'::jsonb,
  confidence text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  error text not null default ''
);

create unique index research_tasks_collection_platform_title_uq
on public.research_tasks(collection_date, platform, normalized_title);

create table public.drama_overrides (
  drama_id text primary key,
  fields_json jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create table public.collection_uploads (
  id text primary key,
  collection_date date not null,
  platform text not null,
  filename text not null default '',
  mime_type text not null default '',
  storage_path text not null default '',
  sha256 text not null default '',
  status text not null default '',
  created_at timestamptz not null default now()
);
