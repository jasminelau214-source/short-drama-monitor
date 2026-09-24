-- Minimal ephemeral PostgreSQL schema for JSM Integration Contract tests.
-- This file is used only by GitHub Actions service-container Postgres.

create schema if not exists public;

create table if not exists public.analysis_runs (
  id text primary key,
  collection_date date not null,
  platform text not null,
  status text not null default '',
  result_json jsonb not null default '{}'::jsonb
);

create table if not exists public.research_tasks (
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
  updated_at timestamptz not null default now(),
  error text not null default ''
);

create unique index if not exists research_tasks_collection_platform_title_uq
on public.research_tasks(collection_date, platform, normalized_title);
