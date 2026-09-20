-- JSM Core Contract v1: canonical database-side drama title identity.
-- Integration-only migration. Do not apply to production without explicit approval.

create or replace function public.jsm_normalize_title_v1(value text)
returns text
language plpgsql
immutable
set search_path = pg_catalog, public
as $$
declare
  text_value text := lower(btrim(coalesce(value, '')));
begin
  if text_value = '' then
    return '';
  end if;

  -- Bracketed release labels at either edge.
  text_value := regexp_replace(
    text_value,
    '^[[:space:]]*[\[(（［【][[:space:]]*(eng(lish)?[[:space:]]+)?dub(bed)?[[:space:]]*[\])）］】][[:space:]]*[:|–—-]*[[:space:]]*',
    '',
    'i'
  );
  text_value := regexp_replace(
    text_value,
    '[[:space:]]*[:|–—-]*[[:space:]]*[\[(（［【][[:space:]]*(eng(lish)?[[:space:]]+)?dub(bed)?[[:space:]]*[\])）］】][[:space:]]*$',
    '',
    'i'
  );

  -- Explicit leading English/ENG dub labels.
  text_value := regexp_replace(
    text_value,
    '^[[:space:]]*((eng(lish)?[[:space:]]+)dub(bed)?[[:space:]]*[:|–—-]+[[:space:]]*|(eng(lish)?[[:space:]]+)dub(bed)?[[:space:]]+)',
    '',
    'i'
  );

  -- Trailing plain dub labels require a separator.
  text_value := regexp_replace(
    text_value,
    '[[:space:]]*[:|–—-]+[[:space:]]*dub(bed)?[[:space:]]*$',
    '',
    'i'
  );

  -- Explicit English/ENG trailing labels may be space-separated.
  text_value := regexp_replace(
    text_value,
    '[[:space:]]+(eng(lish)?[[:space:]]+)dub(bed)?[[:space:]]*$',
    '',
    'i'
  );

  return regexp_replace(text_value, '[^a-z0-9]+', '', 'g');
end;
$$;


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
begin
  if coalesce((new.result_json->>'batchComplete')::boolean, false) is not true then
    return new;
  end if;

  source_type := coalesce(new.result_json->'collector'->>'sourceType', 'SHORT_DRAMA_APP');
  if source_type <> 'SHORT_DRAMA_APP' then
    return new;
  end if;

  for item in select value from jsonb_array_elements(coalesce(new.result_json->'rows','[]'::jsonb))
  loop
    item_rank := nullif(item->>'rank','')::integer;
    item_title := btrim(coalesce(item->>'title',''));
    item_newness := coalesce(item->>'newness','');
    norm_title := public.jsm_normalize_title_v1(item_title);

    -- No identity key = no automatic research task.
    if item_rank is null or item_title = '' or norm_title = '' then
      continue;
    end if;

    meaningful_pending := array(
      select btrim(x)
      from jsonb_array_elements_text(coalesce(item->'pendingChecks','[]'::jsonb)) as t(x)
      where btrim(x) <> ''
        and not (item_newness = 'old' and btrim(x) = '待深度研究')
    );

    if item_newness in ('new','uncertain') or coalesce(cardinality(meaningful_pending),0) > 0 then
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


drop trigger if exists analysis_runs_enqueue_research on public.analysis_runs;
create trigger analysis_runs_enqueue_research
after insert or update of result_json, status on public.analysis_runs
for each row execute function public.enqueue_research_from_analysis_run();
