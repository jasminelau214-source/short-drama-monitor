\set ON_ERROR_STOP on

-- 1. SQL normalizer must match Core Contract v1 vectors.
do $$
declare
  actual text;
begin
  actual := public.jsm_normalize_title_v1('The Hidden Tyrant');
  if actual <> 'thehiddentyrant' then raise exception 'plain normalization failed: %', actual; end if;

  actual := public.jsm_normalize_title_v1('THE-HIDDEN: TYRANT!');
  if actual <> 'thehiddentyrant' then raise exception 'punctuation normalization failed: %', actual; end if;

  actual := public.jsm_normalize_title_v1('(DUBBED) Ruling Over All I See');
  if actual <> 'rulingoverallisee' then raise exception 'prefix dubbed failed: %', actual; end if;

  actual := public.jsm_normalize_title_v1('Ruling Over All I See (DUBBED)');
  if actual <> 'rulingoverallisee' then raise exception 'suffix dubbed failed: %', actual; end if;

  actual := public.jsm_normalize_title_v1('Serendipitous Love （DUBBED)');
  if actual <> 'serendipitouslove' then raise exception 'mixed full-width dubbed failed: %', actual; end if;

  actual := public.jsm_normalize_title_v1('【ENG DUB】 Ruling Over All I See');
  if actual <> 'rulingoverallisee' then raise exception 'full-width eng dub failed: %', actual; end if;

  actual := public.jsm_normalize_title_v1('[ENG DUB] Flash Marriage CEO Spoils Me a Lot');
  if actual <> 'flashmarriageceospoilsmealot' then raise exception 'eng dub failed: %', actual; end if;

  actual := public.jsm_normalize_title_v1('English Dubbed: Justice in Blood');
  if actual <> 'justiceinblood' then raise exception 'english dubbed prefix failed: %', actual; end if;

  actual := public.jsm_normalize_title_v1('Justice in Blood - Dubbed');
  if actual <> 'justiceinblood' then raise exception 'plain dubbed suffix failed: %', actual; end if;

  actual := public.jsm_normalize_title_v1('The Dubbed Wife');
  if actual <> 'thedubbedwife' then raise exception 'title content over-stripped: %', actual; end if;

  actual := public.jsm_normalize_title_v1('Dubbed in Blood');
  if actual <> 'dubbedinblood' then raise exception 'leading title word over-stripped: %', actual; end if;

  actual := public.jsm_normalize_title_v1('中文剧名');
  if actual <> '' then raise exception 'non-latin-only title must fail closed: %', actual; end if;
end $$;


-- 2. Two release-label variants in the same App run must collapse to one task.
insert into public.analysis_runs(id, collection_date, platform, status, result_json)
values (
  'run-app-dub',
  '2026-09-20',
  'ReelShort',
  '已识别-待深研',
  jsonb_build_object(
    'batchComplete', true,
    'collector', jsonb_build_object('sourceType','SHORT_DRAMA_APP'),
    'rows', jsonb_build_array(
      jsonb_build_object('rank',1,'title','(DUBBED) Justice in Blood','newness','new','pendingChecks',jsonb_build_array('待深度研究')),
      jsonb_build_object('rank',2,'title','Justice in Blood','newness','new','pendingChecks',jsonb_build_array('待深度研究'))
    )
  )
);

do $$
declare
  task_count integer;
begin
  select count(*) into task_count
  from public.research_tasks
  where collection_date='2026-09-20'
    and platform='ReelShort'
    and normalized_title='justiceinblood';
  if task_count <> 1 then
    raise exception 'dub variants created % tasks, expected 1', task_count;
  end if;
end $$;


-- 3. OFFICIAL_WEB must never enqueue App research tasks.
insert into public.analysis_runs(id, collection_date, platform, status, result_json)
values (
  'run-web',
  '2026-09-21',
  'ReelShort',
  '已采集',
  jsonb_build_object(
    'batchComplete', true,
    'collector', jsonb_build_object('sourceType','OFFICIAL_WEB'),
    'rows', jsonb_build_array(
      jsonb_build_object('rank',1,'title','Web Only Drama','newness','new','pendingChecks',jsonb_build_array('待深度研究'))
    )
  )
);

do $$
begin
  if exists (
    select 1 from public.research_tasks
    where collection_date='2026-09-21' and title='Web Only Drama'
  ) then
    raise exception 'OFFICIAL_WEB incorrectly enqueued research';
  end if;
end $$;


-- 4. Same normalized title on different platforms stays platform-isolated.
insert into public.analysis_runs(id, collection_date, platform, status, result_json)
values
(
  'run-reel-cross',
  '2026-09-22',
  'ReelShort',
  '已识别-待深研',
  jsonb_build_object(
    'batchComplete', true,
    'collector', jsonb_build_object('sourceType','SHORT_DRAMA_APP'),
    'rows', jsonb_build_array(jsonb_build_object('rank',1,'title','Cross Platform Title','newness','new'))
  )
),
(
  'run-net-cross',
  '2026-09-22',
  'NetShort',
  '已识别-待深研',
  jsonb_build_object(
    'batchComplete', true,
    'collector', jsonb_build_object('sourceType','SHORT_DRAMA_APP'),
    'rows', jsonb_build_array(jsonb_build_object('rank',1,'title','Cross Platform Title','newness','new'))
  )
);

do $$
declare
  task_count integer;
begin
  select count(*) into task_count
  from public.research_tasks
  where collection_date='2026-09-22'
    and normalized_title='crossplatformtitle';
  if task_count <> 2 then
    raise exception 'cross-platform isolation failed: % tasks', task_count;
  end if;
end $$;


-- 5. Real title words containing "dubbed" must remain distinct.
insert into public.analysis_runs(id, collection_date, platform, status, result_json)
values (
  'run-real-dubbed-word',
  '2026-09-23',
  'ReelShort',
  '已识别-待深研',
  jsonb_build_object(
    'batchComplete', true,
    'collector', jsonb_build_object('sourceType','SHORT_DRAMA_APP'),
    'rows', jsonb_build_array(
      jsonb_build_object('rank',1,'title','The Dubbed Wife','newness','new'),
      jsonb_build_object('rank',2,'title','Dubbed in Blood','newness','new')
    )
  )
);

do $$
begin
  if not exists (
    select 1 from public.research_tasks
    where collection_date='2026-09-23' and normalized_title='thedubbedwife'
  ) then raise exception 'The Dubbed Wife identity missing'; end if;

  if not exists (
    select 1 from public.research_tasks
    where collection_date='2026-09-23' and normalized_title='dubbedinblood'
  ) then raise exception 'Dubbed in Blood identity missing'; end if;
end $$;


-- 6. Empty identity must fail closed.
insert into public.analysis_runs(id, collection_date, platform, status, result_json)
values (
  'run-nonlatin',
  '2026-09-24',
  'ReelShort',
  '已识别-待深研',
  jsonb_build_object(
    'batchComplete', true,
    'collector', jsonb_build_object('sourceType','SHORT_DRAMA_APP'),
    'rows', jsonb_build_array(jsonb_build_object('rank',1,'title','中文剧名','newness','new'))
  )
);

do $$
begin
  if exists (
    select 1 from public.research_tasks
    where collection_date='2026-09-24'
  ) then
    raise exception 'empty identity incorrectly entered research queue';
  end if;
end $$;

select 'DB_IDENTITY_CONTRACT_PASS' as result;
