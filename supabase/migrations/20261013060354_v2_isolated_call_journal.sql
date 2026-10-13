-- Isolated staging only. No production/legacy queue access and no worker seed.
BEGIN;
DO $$
BEGIN
  IF to_regclass('public.research_tasks') IS NOT NULL
     OR to_regclass('public.analysis_runs') IS NOT NULL THEN
    RAISE EXCEPTION 'JSM_V2_REFUSES_LEGACY_OR_PRODUCTION_DATABASE';
  END IF;
END;
$$;

CREATE SCHEMA jsm_v2_runtime;
REVOKE ALL ON SCHEMA jsm_v2_runtime FROM PUBLIC, anon, authenticated, service_role;
CREATE TABLE jsm_v2_runtime.meta (
  singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
  scope text NOT NULL CHECK(scope='JSM_V2_ISOLATED_STAGING_V1'),
  instance_id uuid NOT NULL DEFAULT gen_random_uuid()
);
INSERT INTO jsm_v2_runtime.meta(scope) VALUES ('JSM_V2_ISOLATED_STAGING_V1');
CREATE TABLE jsm_v2_runtime.workers (
  user_id uuid PRIMARY KEY,
  enabled boolean NOT NULL DEFAULT false,
  valid_until timestamptz NOT NULL
);
CREATE TABLE jsm_v2_runtime.tasks (
  id text PRIMARY KEY CHECK(length(id) BETWEEN 1 AND 200),
  active_seconds double precision NOT NULL DEFAULT 0 CHECK(active_seconds>=0 AND active_seconds<'Infinity'::float8)
);
CREATE TABLE jsm_v2_runtime.calls (
  task_id text NOT NULL REFERENCES jsm_v2_runtime.tasks(id),
  call_id text NOT NULL CHECK(length(call_id) BETWEEN 1 AND 80),
  stage text NOT NULL CHECK(stage IN ('SEARCH','ANALYZE')),
  binding text NOT NULL CHECK(binding ~ '^[0-9a-f]{64}$'),
  attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
  state text NOT NULL CHECK(state IN ('READY','INFLIGHT','RETRY_WAIT','SUCCEEDED','DEFERRED_FREE_QUOTA','FAILED','REVIEW_REQUIRED')),
  owner uuid,
  dispatched_at timestamptz,
  retry_at double precision NOT NULL DEFAULT 0 CHECK(retry_at>=0 AND retry_at<'Infinity'::float8),
  response jsonb,
  outcome_payload jsonb,
  PRIMARY KEY(task_id,call_id),
  CHECK((state='SUCCEEDED') = (response IS NOT NULL)),
  CHECK(response IS NULL OR jsonb_typeof(response)='object')
);
CREATE INDEX calls_task_stage ON jsm_v2_runtime.calls(task_id,stage);
CREATE UNIQUE INDEX calls_one_inflight_per_task ON jsm_v2_runtime.calls(task_id) WHERE state='INFLIGHT';
CREATE TABLE jsm_v2_runtime.wait_events (
  id uuid PRIMARY KEY,
  task_id text NOT NULL REFERENCES jsm_v2_runtime.tasks(id),
  seconds double precision NOT NULL CHECK(seconds>=0 AND seconds<'Infinity'::float8)
);
CREATE INDEX wait_events_task ON jsm_v2_runtime.wait_events(task_id);
ALTER TABLE jsm_v2_runtime.meta ENABLE ROW LEVEL SECURITY;
ALTER TABLE jsm_v2_runtime.workers ENABLE ROW LEVEL SECURITY;
ALTER TABLE jsm_v2_runtime.tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE jsm_v2_runtime.calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE jsm_v2_runtime.wait_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA jsm_v2_runtime FROM PUBLIC, anon, authenticated, service_role;

CREATE FUNCTION jsm_v2_runtime.refuse(p_reason text,p_status text DEFAULT 'REVIEW_REQUIRED')
RETURNS jsonb LANGUAGE sql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
  SELECT jsonb_build_object('ok',false,'status',p_status,'reason',p_reason);
$$;

CREATE FUNCTION jsm_v2_runtime.dispatch(p_operation text,p_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,jsm_v2_runtime,pg_temp
SET statement_timeout='10s'
SET lock_timeout='5s'
AS $$
DECLARE
  v_uid uuid := auth.uid();
  v_allowed boolean;
  v_until timestamptz;
  v_expected text[];
  v_task text;
  v_call text;
  v_stage text;
  v_binding text;
  v_owner uuid;
  v_event uuid;
  v_clock timestamptz := clock_timestamp();
  v_now double precision;
  v_elapsed double precision;
  v_delay double precision;
  v_active double precision;
  v_kind text;
  v_state text;
  v_row jsm_v2_runtime.calls%ROWTYPE;
  v_outcome jsonb;
  v_prior jsm_v2_runtime.wait_events%ROWTYPE;
  v_count integer;
  v_data jsonb;
BEGIN
  SELECT enabled,valid_until INTO v_allowed,v_until
    FROM jsm_v2_runtime.workers WHERE user_id=v_uid FOR SHARE;
  v_clock:=clock_timestamp();
  IF v_uid IS NULL OR v_allowed IS DISTINCT FROM true OR v_until<=v_clock THEN
    RETURN jsm_v2_runtime.refuse('WORKER_NOT_AUTHORIZED');
  END IF;
  IF p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>9*1024*1024 THEN
    RETURN jsm_v2_runtime.refuse('RPC_PAYLOAD_INVALID');
  END IF;
  CASE p_operation
    WHEN 'info' THEN v_expected:=ARRAY[]::text[];
    WHEN 'inspect' THEN v_expected:=ARRAY['task_id','call_id'];
    WHEN 'start' THEN v_expected:=ARRAY['task_id','call_id','stage','binding'];
    WHEN 'finish' THEN v_expected:=ARRAY['task_id','call_id','owner','elapsed','kind','response','delay'];
    WHEN 'charge_wait' THEN v_expected:=ARRAY['task_id','seconds','event_id'];
    WHEN 'resume' THEN v_expected:=ARRAY['task_id','call_id','binding'];
    ELSE RETURN jsm_v2_runtime.refuse('RPC_OPERATION_INVALID');
  END CASE;
  IF (p_payload-v_expected)<>'{}'::jsonb OR (SELECT count(*) FROM jsonb_object_keys(p_payload))<>cardinality(v_expected) THEN
    RETURN jsm_v2_runtime.refuse('RPC_PAYLOAD_INVALID');
  END IF;
  IF p_operation='info' THEN
    SELECT jsonb_build_object('scope',scope,'protocol',1,'instance_id',instance_id) INTO v_data FROM jsm_v2_runtime.meta;
    RETURN jsonb_build_object('ok',true,'data',v_data);
  END IF;
  v_task:=p_payload->>'task_id'; v_call:=p_payload->>'call_id';
  IF jsonb_typeof(p_payload->'task_id')<>'string' OR length(v_task) NOT BETWEEN 1 AND 200
     OR v_task<>btrim(v_task) OR v_task ~ '[[:cntrl:]]' THEN
    RETURN jsm_v2_runtime.refuse('TASK_ID_INVALID');
  END IF;
  IF p_operation<>'charge_wait' AND (jsonb_typeof(p_payload->'call_id')<>'string'
     OR length(v_call) NOT BETWEEN 1 AND 80 OR v_call<>btrim(v_call) OR v_call ~ '[[:cntrl:]]') THEN
    RETURN jsm_v2_runtime.refuse('CALL_ID_INVALID');
  END IF;
  IF p_operation='inspect' THEN
    SELECT jsonb_build_object('task_id',task_id,'call_id',call_id,'stage',stage,'binding',binding,
      'attempts',attempts,'state',state,'retry_at',retry_at,'response',response)
      INTO v_data FROM jsm_v2_runtime.calls WHERE task_id=v_task AND call_id=v_call;
    RETURN jsonb_build_object('ok',true,'data',v_data);
  END IF;
  IF p_operation IN ('start','resume') THEN
    v_binding:=p_payload->>'binding';
    IF jsonb_typeof(p_payload->'binding')<>'string' OR v_binding !~ '^[0-9a-f]{64}$' THEN
      RETURN jsm_v2_runtime.refuse('CALL_BINDING_INVALID');
    END IF;
  END IF;
  IF p_operation='start' THEN
    v_stage:=p_payload->>'stage';
    IF v_stage NOT IN ('SEARCH','ANALYZE') OR jsonb_typeof(p_payload->'stage')<>'string' THEN
      RETURN jsm_v2_runtime.refuse('CALL_STAGE_INVALID');
    END IF;
    INSERT INTO jsm_v2_runtime.tasks(id) VALUES (v_task) ON CONFLICT DO NOTHING;
  END IF;
  -- Consistent task-first lock order serializes per-task reservations/outcomes.
  SELECT active_seconds INTO v_active FROM jsm_v2_runtime.tasks WHERE id=v_task FOR UPDATE;
  IF NOT FOUND THEN RETURN jsm_v2_runtime.refuse('TASK_NOT_FOUND'); END IF;
  v_clock:=clock_timestamp(); v_now:=extract(epoch FROM v_clock);
  IF v_until<=v_clock THEN RETURN jsm_v2_runtime.refuse('WORKER_NOT_AUTHORIZED'); END IF;
  IF p_operation='charge_wait' THEN
    IF jsonb_typeof(p_payload->'seconds')<>'number' OR jsonb_typeof(p_payload->'event_id')<>'string' THEN
      RETURN jsm_v2_runtime.refuse('WAIT_EVENT_INVALID');
    END IF;
    v_elapsed:=(p_payload->>'seconds')::double precision; v_event:=(p_payload->>'event_id')::uuid;
    IF NOT (v_elapsed>=0 AND v_elapsed<=86400) THEN RETURN jsm_v2_runtime.refuse('WAIT_EVENT_INVALID'); END IF;
    SELECT * INTO v_prior FROM jsm_v2_runtime.wait_events WHERE id=v_event;
    IF FOUND THEN
      IF v_prior.task_id<>v_task OR v_prior.seconds<>v_elapsed THEN RETURN jsm_v2_runtime.refuse('WAIT_EVENT_CONFLICT'); END IF;
    ELSE
      IF NOT EXISTS (SELECT 1 FROM jsm_v2_runtime.calls WHERE task_id=v_task AND state='RETRY_WAIT') THEN
        RETURN jsm_v2_runtime.refuse('NO_RETRY_WAIT_TO_CHARGE');
      END IF;
      INSERT INTO jsm_v2_runtime.wait_events VALUES(v_event,v_task,v_elapsed);
      UPDATE jsm_v2_runtime.tasks SET active_seconds=active_seconds+v_elapsed WHERE id=v_task;
    END IF;
    RETURN jsonb_build_object('ok',true,'data',jsonb_build_object('charged',true));
  END IF;
  SELECT * INTO v_row FROM jsm_v2_runtime.calls WHERE task_id=v_task AND call_id=v_call FOR UPDATE;
  IF p_operation='start' THEN
    IF FOUND AND (v_row.binding<>v_binding OR v_row.stage<>v_stage) THEN RETURN jsm_v2_runtime.refuse('CALL_BINDING_CHANGED'); END IF;
    IF FOUND AND v_row.state='SUCCEEDED' THEN
      RETURN jsonb_build_object('ok',true,'data',jsonb_build_object('cached',v_row.response,'owner',NULL,'remaining',0));
    END IF;
    IF FOUND AND v_row.state NOT IN ('READY','RETRY_WAIT') THEN
      RETURN jsm_v2_runtime.refuse('CALL_NOT_READY_OR_OUTCOME_UNCERTAIN',
        CASE WHEN v_row.state IN ('DEFERRED_FREE_QUOTA','FAILED') THEN v_row.state ELSE 'REVIEW_REQUIRED' END);
    END IF;
    IF FOUND AND (v_row.attempts>=3 OR v_row.retry_at>v_now) THEN
      RETURN jsm_v2_runtime.refuse('CALL_ATTEMPT_OR_BACKOFF_BOUNDARY',CASE WHEN v_row.attempts>=3 THEN 'FAILED' ELSE 'REVIEW_REQUIRED' END);
    END IF;
    IF v_active>=300 THEN RETURN jsm_v2_runtime.refuse('TASK_ACTIVE_TIMEOUT','FAILED'); END IF;
    IF EXISTS (SELECT 1 FROM jsm_v2_runtime.calls WHERE task_id=v_task AND state='INFLIGHT') THEN
      RETURN jsm_v2_runtime.refuse('TASK_ALREADY_HAS_CALL_INFLIGHT');
    END IF;
    IF v_row.task_id IS NULL THEN
      SELECT count(*) INTO v_count FROM jsm_v2_runtime.calls WHERE task_id=v_task AND stage=v_stage;
      IF v_count>=(CASE WHEN v_stage='SEARCH' THEN 3 ELSE 1 END) THEN RETURN jsm_v2_runtime.refuse('LOGICAL_CALL_LIMIT'); END IF;
      INSERT INTO jsm_v2_runtime.calls(task_id,call_id,stage,binding,state) VALUES (v_task,v_call,v_stage,v_binding,'READY');
    END IF;
    v_owner:=gen_random_uuid();
    UPDATE jsm_v2_runtime.calls SET attempts=attempts+1,state='INFLIGHT',owner=v_owner,dispatched_at=v_clock,
      outcome_payload=NULL WHERE task_id=v_task AND call_id=v_call;
    RETURN jsonb_build_object('ok',true,'data',jsonb_build_object('cached',NULL,'owner',v_owner,'remaining',300-v_active));
  END IF;
  IF v_row.task_id IS NULL THEN RETURN jsm_v2_runtime.refuse('CALL_NOT_FOUND'); END IF;
  IF p_operation='resume' THEN
    IF v_row.state<>'DEFERRED_FREE_QUOTA' OR v_row.binding<>v_binding OR v_row.attempts>=3 THEN
      RETURN jsm_v2_runtime.refuse('DEFERRED_RESUME_NOT_AUTHORIZED');
    END IF;
    UPDATE jsm_v2_runtime.calls SET state='READY',retry_at=0 WHERE task_id=v_task AND call_id=v_call;
    RETURN jsonb_build_object('ok',true,'data',jsonb_build_object('resumed',true));
  END IF;
  IF jsonb_typeof(p_payload->'owner')<>'string' OR jsonb_typeof(p_payload->'elapsed')<>'number'
     OR jsonb_typeof(p_payload->'delay')<>'number' OR jsonb_typeof(p_payload->'kind')<>'string' THEN
    RETURN jsm_v2_runtime.refuse('OUTCOME_PAYLOAD_INVALID');
  END IF;
  v_owner:=(p_payload->>'owner')::uuid; v_elapsed:=(p_payload->>'elapsed')::double precision;
  v_delay:=(p_payload->>'delay')::double precision; v_kind:=p_payload->>'kind';
  IF v_owner IS DISTINCT FROM v_row.owner THEN RETURN jsm_v2_runtime.refuse('CALL_OWNER_CHANGED'); END IF;
  IF NOT (v_elapsed>=0 AND v_elapsed<=86400 AND v_delay>=0 AND v_delay<=60)
     OR v_kind NOT IN ('success','transient','quota','business')
     OR (v_kind='success' AND jsonb_typeof(p_payload->'response')<>'object')
     OR (v_kind<>'success' AND jsonb_typeof(p_payload->'response')<>'null')
     OR (v_kind<>'transient' AND v_delay<>0) THEN RETURN jsm_v2_runtime.refuse('OUTCOME_PAYLOAD_INVALID'); END IF;
  v_outcome:=p_payload-ARRAY['task_id','call_id'];
  IF v_row.outcome_payload IS NOT NULL THEN
    IF v_row.outcome_payload<>v_outcome THEN RETURN jsm_v2_runtime.refuse('OUTCOME_CONFLICT'); END IF;
    RETURN jsonb_build_object('ok',true,'data',jsonb_build_object('state',v_row.state));
  END IF;
  IF v_row.state<>'INFLIGHT' THEN RETURN jsm_v2_runtime.refuse('OUTCOME_NOT_INFLIGHT'); END IF;
  -- Charge no less than actual server time since the persisted dispatch.
  v_elapsed:=greatest(v_elapsed,extract(epoch FROM (v_clock-v_row.dispatched_at)));
  v_active:=v_active+v_elapsed;
  v_state:=CASE v_kind WHEN 'success' THEN 'SUCCEEDED' WHEN 'transient' THEN 'RETRY_WAIT'
    WHEN 'quota' THEN 'DEFERRED_FREE_QUOTA' ELSE 'REVIEW_REQUIRED' END;
  IF v_active>=300 OR (v_kind='transient' AND v_row.attempts>=3) THEN v_state:='FAILED'; END IF;
  UPDATE jsm_v2_runtime.tasks SET active_seconds=v_active WHERE id=v_task;
  UPDATE jsm_v2_runtime.calls SET state=v_state,retry_at=v_now+v_delay,outcome_payload=v_outcome,
    response=CASE WHEN v_state='SUCCEEDED' THEN p_payload->'response' ELSE NULL END
    WHERE task_id=v_task AND call_id=v_call;
  RETURN jsonb_build_object('ok',true,'data',jsonb_build_object('state',v_state));
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
  RETURN jsm_v2_runtime.refuse('RPC_VALUE_INVALID');
END;
$$;

-- The exposed wrapper has no elevated privileges; the private dispatcher
-- explicitly checks auth.uid() against an independently managed worker ledger.
CREATE FUNCTION public.jsm_v2_journal(p_operation text,p_payload jsonb)
RETURNS jsonb LANGUAGE sql SECURITY INVOKER
SET search_path=pg_catalog,jsm_v2_runtime,pg_temp AS $$
  SELECT jsm_v2_runtime.dispatch(p_operation,p_payload);
$$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA jsm_v2_runtime FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.jsm_v2_journal(text,jsonb) FROM PUBLIC, anon, authenticated, service_role;
GRANT USAGE ON SCHEMA jsm_v2_runtime TO authenticated;
GRANT EXECUTE ON FUNCTION jsm_v2_runtime.dispatch(text,jsonb) TO authenticated;
GRANT EXECUTE ON FUNCTION public.jsm_v2_journal(text,jsonb) TO authenticated;
NOTIFY pgrst,'reload schema';
COMMIT;
