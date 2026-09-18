-- DRAFT ONLY — audit/backfill-2026-09-16-17
-- Do not apply to production without explicit promotion approval.
--
-- P0: make research enqueue authoritative-latest-run aware.
-- Goals:
--   1) OFFICIAL_WEB never auto-enqueues research.
--   2) Only the latest complete same-day App run for the same target may enqueue.
--   3) Pending/researching tasks whose titles disappear from a later run are
--      marked REVIEW_REQUIRED with SUPERSEDED_BY_LATER_RUN.
--   4) COMPLETE research may remain as a reusable research asset, but the daily
--      ranking publication layer decides whether that title is in the current TopN.

create or replace function public.enqueue_research_from_analysis_run()
returns trigger
language plpgsql
security definer
set search_path to 'public'
as $function$
declare
  item jsonb;
  reasons text[];
  missing text[];
  meaningful_pending text[];
  item_rank integer;
  item_title text;
  item_newness text;
  norm_title text;
  source_type text;
  target_key text;
begin
  if coalesce((new.result_json->>'batchComplete')::boolean, false) is not true then
    return new;
  end if;

  source_type := coalesce(new.result_json->'collector'->>'sourceType', 'SHORT_DRAMA_APP');
  target_key := coalesce(new.result_json->'collector'->>'targetKey', 'daily_top_all');

  if source_type <> 'SHORT_DRAMA_APP' then
    return new;
  end if;

  -- Ignore replay/update of an older run when a newer complete run already exists
  -- in the same semantic scope. This makes the trigger order-independent.
  if exists (
    select 1
    from public.analysis_runs newer
    where newer.id <> new.id
      and newer.collection_date = new.collection_date
      and newer.platform = new.platform
      and coalesce((newer.result_json->>'batchComplete')::boolean, false) is true
      and coalesce(newer.result_json->'collector'->>'sourceType', 'SHORT_DRAMA_APP') = source_type
      and coalesce(newer.result_json->'collector'->>'targetKey', 'daily_top_all') = target_key
      and (newer.updated_at, newer.id) > (new.updated_at, new.id)
  ) then
    return new;
  end if;

  -- A later authoritative run invalidates unfinished work for titles that have
  -- dropped out. Keep COMPLETE tasks intact as reusable assets.
  update public.research_tasks rt
     set status = 'REVIEW_REQUIRED',
         error = 'SUPERSEDED_BY_LATER_RUN',
         context_json = coalesce(rt.context_json, '{}'::jsonb)
           || jsonb_build_object(
                '_supersededByRunId', new.id,
                '_supersededAt', now(),
                '_supersededTargetKey', target_key
              ),
         updated_at = now()
    from public.analysis_runs origin
   where origin.id = rt.analysis_run_id
     and rt.collection_date = new.collection_date
     and rt.platform = new.platform
     and rt.analysis_run_id <> new.id
     and rt.status in ('PENDING','RESEARCHING','NEEDS_GPT','FAILED')
     and coalesce(origin.result_json->'collector'->>'sourceType', 'SHORT_DRAMA_APP') = source_type
     and coalesce(origin.result_json->'collector'->>'targetKey', 'daily_top_all') = target_key
     and not exists (
       select 1
       from jsonb_array_elements(coalesce(new.result_json->'rows','[]'::jsonb)) current_item
       where regexp_replace(
               lower(btrim(coalesce(current_item->>'title',''))),
               '[^a-z0-9]+','','g'
             ) = rt.normalized_title
     );

  for item in
    select value
    from jsonb_array_elements(coalesce(new.result_json->'rows','[]'::jsonb))
  loop
    item_rank := nullif(item->>'rank','')::integer;
    item_title := btrim(coalesce(item->>'title',''));
    item_newness := coalesce(item->>'newness','');
    norm_title := regexp_replace(lower(item_title),'[^a-z0-9]+','','g');

    if item_rank is null or item_title = '' then
      continue;
    end if;

    meaningful_pending := array(
      select btrim(x)
      from jsonb_array_elements_text(coalesce(item->'pendingChecks','[]'::jsonb)) as t(x)
      where btrim(x) <> ''
        and not (item_newness = 'old' and btrim(x) = '待深度研究')
    );

    if item_newness in ('new','uncertain')
       or coalesce(cardinality(meaningful_pending),0) > 0 then

      reasons := array[]::text[];
      if item_newness in ('new','uncertain') then
        reasons := array_append(reasons, 'newness:' || item_newness);
        reasons := reasons || array(
          select btrim(x)
          from jsonb_array_elements_text(coalesce(item->'pendingChecks','[]'::jsonb)) as t(x)
          where btrim(x) <> ''
        );
      else
        reasons := reasons || meaningful_pending;
      end if;

      missing := array[]::text[];
      if btrim(coalesce(item->>'synopsis','')) = '' then missing := array_append(missing,'synopsis'); end if;
      if btrim(coalesce(item->>'genre','')) = '' then missing := array_append(missing,'genre'); end if;
      if btrim(coalesce(item->>'lane','')) = '' then missing := array_append(missing,'lane'); end if;
      if btrim(coalesce(item->>'audience','')) = '' then missing := array_append(missing,'audience'); end if;
      if btrim(coalesce(item->>'storyCore','')) = '' then missing := array_append(missing,'storyCore'); end if;
      if btrim(coalesce(item->>'storySkin','')) = '' then missing := array_append(missing,'storySkin'); end if;
      if btrim(coalesce(item->>'conflict','')) = '' then missing := array_append(missing,'conflict'); end if;
      if btrim(coalesce(item->>'payoff','')) = '' then missing := array_append(missing,'payoff'); end if;
      if btrim(coalesce(item->>'localizationLevel','')) = '' then missing := array_append(missing,'localizationLevel'); end if;
      if btrim(coalesce(item->>'localizationJudgment','')) = '' then missing := array_append(missing,'localizationJudgment'); end if;
      if btrim(coalesce(item->>'mismatch','')) = '' then missing := array_append(missing,'mismatch'); end if;

      insert into public.research_tasks(
        analysis_run_id, collection_date, platform, rank, title, normalized_title,
        reason, missing_fields, status, priority, context_json, updated_at
      ) values (
        new.id, new.collection_date, new.platform, item_rank, item_title, norm_title,
        reasons, missing, 'PENDING',
        case when item_newness='new' then 80 when item_newness='uncertain' then 90 else 60 end,
        item, now()
      )
      on conflict (collection_date, platform, normalized_title) do update set
        analysis_run_id = excluded.analysis_run_id,
        rank = excluded.rank,
        title = excluded.title,
        reason = excluded.reason,
        missing_fields = excluded.missing_fields,
        priority = excluded.priority,
        context_json = excluded.context_json,
        updated_at = now(),
        status = case when research_tasks.status='COMPLETE' then 'COMPLETE' else 'PENDING' end,
        error = case when research_tasks.status='COMPLETE' then research_tasks.error else '' end;
    end if;
  end loop;

  return new;
end;
$function$;

-- P1, intentionally not included in the P0 migration:
-- If one App platform later has multiple simultaneously researched targets,
-- add source_type + target_key columns to research_tasks and include them in the
-- uniqueness key. The current unique key (date, platform, normalized_title)
-- is safe only while one research-bearing App target is active per platform.
