-- Separate staging business storage. Never install the synthetic C2 schema.
BEGIN;
DO $$ BEGIN
  IF to_regclass('public.research_tasks') IS NOT NULL OR to_regclass('public.analysis_runs') IS NOT NULL THEN
    RAISE EXCEPTION 'JSM_V2_LEGACY_DATABASE_REFUSED';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM jsm_v2_runtime.meta WHERE singleton AND scope='JSM_V2_ISOLATED_STAGING_V1') THEN
    RAISE EXCEPTION 'JSM_V2_STAGING_MARKER_REQUIRED';
  END IF;
END $$;
CREATE SCHEMA jsm_v2_business;
REVOKE ALL ON SCHEMA jsm_v2_business FROM PUBLIC,anon,authenticated,service_role;
CREATE TABLE jsm_v2_business.authority (
  scope_id text PRIMARY KEY, collection_date date NOT NULL,
  platform text NOT NULL CHECK(platform IN ('DramaBox','FlexTV','GoodShort','MoboReels','NetShort','ReelShort')),
  source_type text NOT NULL CHECK(source_type IN ('OFFICIAL_WEB','SHORT_DRAMA_APP')),
  target_key text NOT NULL, run_id text NOT NULL,
  snapshot_sha text NOT NULL CHECK(snapshot_sha~'^[0-9a-f]{64}$'),
  revision bigint NOT NULL CHECK(revision>=0), valid_until timestamptz NOT NULL,
  UNIQUE(collection_date,platform,source_type,target_key)
);
-- Protected, independently validated snapshots. Workers cannot insert/approve
-- identity, ranking, field evidence or completion hashes through this RPC.
CREATE TABLE jsm_v2_business.records (
  record_id text PRIMARY KEY, canonical_id text NOT NULL,
  scope_id text NOT NULL REFERENCES jsm_v2_business.authority,
  run_id text NOT NULL, authority_sha text NOT NULL CHECK(authority_sha~'^[0-9a-f]{64}$'),
  source_entity_id text NOT NULL, title text NOT NULL,
  rank integer NOT NULL CHECK(rank BETWEEN 1 AND 10),
  context_text text NOT NULL CHECK(octet_length(context_text)<=8388608 AND jsonb_typeof(context_text::jsonb)='object'),
  context_sha text NOT NULL CHECK(context_sha~'^[0-9a-f]{64}$' AND context_sha=encode(sha256(convert_to(context_text,'UTF8')),'hex')),
  revision bigint NOT NULL CHECK(revision>=0), valid_until timestamptz NOT NULL,
  revoked boolean NOT NULL DEFAULT false,
  approved_result_sha text CHECK(approved_result_sha~'^[0-9a-f]{64}$')
);
CREATE INDEX records_scope ON jsm_v2_business.records(scope_id);
CREATE TABLE jsm_v2_business.subjects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), canonical_id text UNIQUE NOT NULL,
  origin_record_id text REFERENCES jsm_v2_business.records,
  status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','RESEARCHING','DEFERRED_FREE_QUOTA','COMPLETE','REVIEW_REQUIRED','NEEDS_GPT','FAILED')),
  owner uuid, worker_id uuid, started_at timestamptz, deadline timestamptz,
  result_sha text, result_text text,
  CHECK((status='COMPLETE')=(result_sha IS NOT NULL AND result_text IS NOT NULL)),
  CHECK(result_sha IS NULL OR result_sha=encode(sha256(convert_to(result_text,'UTF8')),'hex'))
);
CREATE INDEX subjects_pending ON jsm_v2_business.subjects(id) WHERE status='PENDING';
CREATE TABLE jsm_v2_business.observations (
  record_id text PRIMARY KEY REFERENCES jsm_v2_business.records,
  subject_id uuid NOT NULL REFERENCES jsm_v2_business.subjects
);
CREATE INDEX observations_subject ON jsm_v2_business.observations(subject_id);
CREATE TABLE jsm_v2_business.claim_requests (
  request_id uuid PRIMARY KEY, worker_id uuid NOT NULL,
  subject_id uuid REFERENCES jsm_v2_business.subjects, owner uuid,
  CHECK((subject_id IS NULL)=(owner IS NULL))
);
CREATE TABLE jsm_v2_business.publications (
  record_id text PRIMARY KEY REFERENCES jsm_v2_business.records,
  subject_id uuid NOT NULL REFERENCES jsm_v2_business.subjects, canonical_id text NOT NULL,
  platform text NOT NULL, result_sha text NOT NULL, payload_text text NOT NULL,
  authority_revision bigint NOT NULL, context_revision bigint NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK(result_sha=encode(sha256(convert_to(payload_text,'UTF8')),'hex'))
);
CREATE INDEX publications_subject ON jsm_v2_business.publications(subject_id);
ALTER TABLE jsm_v2_business.authority ENABLE ROW LEVEL SECURITY;
ALTER TABLE jsm_v2_business.records ENABLE ROW LEVEL SECURITY;
ALTER TABLE jsm_v2_business.subjects ENABLE ROW LEVEL SECURITY;
ALTER TABLE jsm_v2_business.observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE jsm_v2_business.claim_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE jsm_v2_business.publications ENABLE ROW LEVEL SECURITY;

CREATE FUNCTION jsm_v2_business.worker() RETURNS uuid LANGUAGE plpgsql SECURITY INVOKER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor uuid:=auth.uid(); expiry timestamptz;
BEGIN
  SELECT valid_until INTO expiry FROM jsm_v2_runtime.workers
    WHERE user_id=actor AND enabled FOR SHARE;
  IF actor IS NULL OR expiry IS NULL OR expiry<=clock_timestamp() THEN
    RAISE EXCEPTION 'WORKER_NOT_AUTHORIZED';
  END IF;
  RETURN actor;
END $$;

CREATE FUNCTION jsm_v2_business.guard_record(p_record text) RETURNS void LANGUAGE plpgsql SECURITY INVOKER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE r jsm_v2_business.records%ROWTYPE; a jsm_v2_business.authority%ROWTYPE; initial_scope text;
BEGIN
  SELECT scope_id INTO initial_scope FROM jsm_v2_business.records WHERE record_id=p_record;
  IF NOT FOUND THEN RAISE EXCEPTION 'RECORD_NOT_FOUND'; END IF;
  -- One lock order: authority -> protected record -> subject.
  SELECT * INTO a FROM jsm_v2_business.authority WHERE scope_id=initial_scope FOR SHARE;
  SELECT * INTO r FROM jsm_v2_business.records WHERE record_id=p_record FOR SHARE;
  IF r.scope_id IS DISTINCT FROM initial_scope OR r.run_id IS DISTINCT FROM a.run_id OR r.authority_sha IS DISTINCT FROM a.snapshot_sha
     OR a.collection_date IS DISTINCT FROM (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date
     OR a.valid_until<=clock_timestamp() OR r.valid_until<=clock_timestamp() OR r.revoked THEN
    RAISE EXCEPTION 'AUTHORITY_OR_CONTEXT_CHANGED';
  END IF;
END $$;

CREATE FUNCTION jsm_v2_business.dispatch(p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp
SET lock_timeout='5s' SET statement_timeout='10s' AS $$
DECLARE actor uuid; expected text[]; actual text[]; rid text; sid uuid; token uuid;
  request uuid; r jsm_v2_business.records%ROWTYPE; a jsm_v2_business.authority%ROWTYPE;
  s jsm_v2_business.subjects%ROWTYPE; c jsm_v2_business.claim_requests%ROWTYPE;
  chosen uuid; record_choice text; task jsonb; payload text; digest text; state text; created boolean;
BEGIN
  actor:=jsm_v2_business.worker();
  IF jsonb_typeof(p_payload) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'SUBJECT_PAYLOAD_INVALID'; END IF;
  expected:=CASE p_operation WHEN 'info' THEN ARRAY[]::text[]
    WHEN 'register' THEN ARRAY['record_id'] WHEN 'claim' THEN ARRAY['request_id']
    WHEN 'context' THEN ARRAY['subject_id','owner'] WHEN 'inspect' THEN ARRAY['subject_id','owner']
    WHEN 'complete' THEN ARRAY['subject_id','owner','record_id','context_sha','authority_revision','context_revision','payload_text','result_sha']
    WHEN 'status' THEN ARRAY['subject_id','owner','status']
    ELSE NULL END;
  SELECT array_agg(key ORDER BY key) INTO actual FROM jsonb_object_keys(p_payload)key;
  IF expected IS NULL OR coalesce(actual,ARRAY[]::text[]) IS DISTINCT FROM
    ARRAY(SELECT unnest(expected) ORDER BY 1) THEN RAISE EXCEPTION 'SUBJECT_PAYLOAD_INVALID'; END IF;
  IF p_operation='info' THEN
    RETURN jsonb_build_object('ok',true,'data',(SELECT jsonb_build_object('scope',scope,
      'instance_id',instance_id,'protocol',1,'kind','JSM_V2_SUBJECT_QUEUE') FROM jsm_v2_runtime.meta WHERE singleton));
  END IF;
  IF p_operation='register' THEN
    rid:=p_payload->>'record_id'; PERFORM jsm_v2_business.guard_record(rid);
    SELECT * INTO r FROM jsm_v2_business.records WHERE record_id=rid;
    INSERT INTO jsm_v2_business.subjects(canonical_id) VALUES(r.canonical_id)
      ON CONFLICT(canonical_id) DO NOTHING RETURNING id INTO chosen;
    created:=FOUND;
    IF NOT created THEN SELECT id INTO chosen FROM jsm_v2_business.subjects WHERE canonical_id=r.canonical_id; END IF;
    INSERT INTO jsm_v2_business.observations VALUES(rid,chosen) ON CONFLICT(record_id) DO NOTHING;
    IF NOT EXISTS(SELECT 1 FROM jsm_v2_business.observations WHERE record_id=rid AND subject_id=chosen) THEN
      RAISE EXCEPTION 'OBSERVATION_IDENTITY_CHANGED';
    END IF;
    PERFORM jsm_v2_business.worker();
    RETURN jsonb_build_object('ok',true,'data',jsonb_build_object('subject_id',chosen,'created',created));
  END IF;
  IF p_operation='claim' THEN
    request:=(p_payload->>'request_id')::uuid;
    -- Global two-task dispatch capacity; queue length itself is unlimited.
    PERFORM pg_advisory_xact_lock(902371,2);
    SELECT * INTO c FROM jsm_v2_business.claim_requests WHERE request_id=request;
    IF FOUND THEN
      IF c.worker_id<>actor THEN RAISE EXCEPTION 'CLAIM_REQUEST_OWNER_CHANGED'; END IF;
      chosen:=c.subject_id; token:=c.owner;
    ELSE
      IF (SELECT count(*) FROM jsm_v2_business.subjects WHERE status='RESEARCHING')<2 THEN
        SELECT sub.id,obs.record_id INTO chosen,record_choice FROM jsm_v2_business.subjects sub
          JOIN jsm_v2_business.observations obs ON obs.subject_id=sub.id
          JOIN jsm_v2_business.records rec ON rec.record_id=obs.record_id
          JOIN jsm_v2_business.authority aut ON aut.scope_id=rec.scope_id
          WHERE sub.status='PENDING' AND rec.canonical_id=sub.canonical_id AND NOT rec.revoked AND rec.valid_until>clock_timestamp()
            AND aut.valid_until>clock_timestamp() AND rec.run_id=aut.run_id AND rec.authority_sha=aut.snapshot_sha
          ORDER BY sub.id,obs.record_id LIMIT 1;
        IF chosen IS NOT NULL THEN
          PERFORM jsm_v2_business.guard_record(record_choice);
          SELECT * INTO s FROM jsm_v2_business.subjects WHERE id=chosen FOR UPDATE;
          IF s.status<>'PENDING' THEN RAISE EXCEPTION 'CLAIM_SUBJECT_CHANGED'; END IF;
          token:=gen_random_uuid();
          UPDATE jsm_v2_business.subjects SET status='RESEARCHING',owner=token,worker_id=actor,
            origin_record_id=record_choice,started_at=clock_timestamp(),deadline=clock_timestamp()+interval '300 seconds'
            WHERE id=chosen;
        END IF;
      END IF;
      INSERT INTO jsm_v2_business.claim_requests VALUES(request,actor,chosen,token);
    END IF;
    IF chosen IS NULL THEN RETURN jsonb_build_object('ok',true,'data',jsonb_build_object('task',NULL,'owner',NULL,'request_id',request)); END IF;
    SELECT * INTO s FROM jsm_v2_business.subjects WHERE id=chosen;
    IF s.status<>'RESEARCHING' OR s.owner IS DISTINCT FROM token OR s.worker_id<>actor THEN
      RAISE EXCEPTION 'CLAIM_REQUIRES_RECONCILIATION';
    END IF;
  ELSE
    chosen:=(p_payload->>'subject_id')::uuid; token:=(p_payload->>'owner')::uuid;
    SELECT * INTO s FROM jsm_v2_business.subjects WHERE id=chosen;
    IF NOT FOUND OR s.worker_id IS DISTINCT FROM actor OR s.owner IS DISTINCT FROM token THEN
      RAISE EXCEPTION 'SUBJECT_CLAIM_OWNER_CHANGED';
    END IF;
  END IF;
  PERFORM jsm_v2_business.guard_record(s.origin_record_id);
  SELECT * INTO r FROM jsm_v2_business.records WHERE record_id=s.origin_record_id;
  SELECT * INTO a FROM jsm_v2_business.authority WHERE scope_id=r.scope_id;
  SELECT * INTO s FROM jsm_v2_business.subjects WHERE id=chosen FOR UPDATE;
  IF s.worker_id IS DISTINCT FROM actor OR s.owner IS DISTINCT FROM token OR r.canonical_id<>s.canonical_id THEN
    RAISE EXCEPTION 'SUBJECT_CLAIM_OR_IDENTITY_CHANGED';
  END IF;
  IF p_operation='inspect' THEN
    PERFORM jsm_v2_business.guard_record(r.record_id);
    PERFORM jsm_v2_business.worker();
    RETURN jsonb_build_object('ok',true,'data',jsonb_build_object('status',s.status,'result_sha',s.result_sha));
  END IF;
  IF s.status<>'COMPLETE' AND (s.status<>'RESEARCHING' OR s.deadline<=clock_timestamp()) THEN
    RAISE EXCEPTION 'SUBJECT_NOT_ACTIVE_OR_DEADLINE_EXCEEDED';
  END IF;
  task:=jsonb_build_object('id',s.id::text,'status','RESEARCHING','collection_date',a.collection_date::text,
    'platform',a.platform,'source_type',a.source_type,'target_key',a.target_key,
    'analysis_run_id',r.run_id,'source_entity_id',r.source_entity_id,'record_id',r.record_id,
    'canonical_drama_id',r.canonical_id,'title',r.title,'rank',r.rank);
  IF p_operation='claim' THEN
    PERFORM jsm_v2_business.worker();
    RETURN jsonb_build_object('ok',true,'data',jsonb_build_object('task',task,'owner',token,'request_id',request));
  END IF;
  IF p_operation='context' THEN
    PERFORM jsm_v2_business.guard_record(r.record_id);
    PERFORM jsm_v2_business.worker();
    RETURN jsonb_build_object('ok',true,'data',jsonb_build_object('context_text',r.context_text,
      'context_sha',r.context_sha,'authority_revision',a.revision,'context_revision',r.revision,'authority_sha',a.snapshot_sha));
  END IF;
  IF p_operation='status' THEN
    IF s.status='COMPLETE' THEN RAISE EXCEPTION 'COMPLETE_CANNOT_BE_DOWNGRADED'; END IF;
    state:=p_payload->>'status';
    IF state NOT IN ('DEFERRED_FREE_QUOTA','REVIEW_REQUIRED','NEEDS_GPT','FAILED') THEN RAISE EXCEPTION 'SUBJECT_TRANSITION_REFUSED'; END IF;
    UPDATE jsm_v2_business.subjects SET status=state WHERE id=chosen;
    PERFORM jsm_v2_business.worker();
    RETURN jsonb_build_object('ok',true,'data',jsonb_build_object('status',state));
  END IF;
  payload:=p_payload->>'payload_text'; digest:=p_payload->>'result_sha';
  IF r.record_id IS DISTINCT FROM p_payload->>'record_id' OR r.context_sha IS DISTINCT FROM p_payload->>'context_sha'
     OR a.revision IS DISTINCT FROM (p_payload->>'authority_revision')::bigint
     OR r.revision IS DISTINCT FROM (p_payload->>'context_revision')::bigint
     OR r.approved_result_sha IS DISTINCT FROM digest OR digest IS NULL
     OR octet_length(payload)>8388608 OR jsonb_typeof(payload::jsonb) IS DISTINCT FROM 'object'
     OR digest IS DISTINCT FROM encode(sha256(convert_to(payload,'UTF8')),'hex') THEN
    RAISE EXCEPTION 'COMPLETION_BINDING_OR_REVIEW_CHANGED';
  END IF;
  IF s.status='COMPLETE' AND (s.result_sha IS DISTINCT FROM digest OR s.result_text IS DISTINCT FROM payload) THEN
    RAISE EXCEPTION 'COMPLETE_RESULT_CONFLICT';
  END IF;
  -- Completion and its same-platform publication commit or roll back together.
  INSERT INTO jsm_v2_business.publications(record_id,subject_id,canonical_id,platform,result_sha,payload_text,authority_revision,context_revision)
    VALUES(r.record_id,s.id,s.canonical_id,a.platform,digest,payload,a.revision,r.revision)
    ON CONFLICT(record_id) DO NOTHING;
  PERFORM 1 FROM jsm_v2_business.publications WHERE record_id=r.record_id FOR UPDATE;
  IF NOT EXISTS(SELECT 1 FROM jsm_v2_business.publications p WHERE p.record_id=r.record_id
      AND (p.subject_id,p.canonical_id,p.platform,p.result_sha,p.payload_text,p.authority_revision,p.context_revision)
        IS NOT DISTINCT FROM (s.id,s.canonical_id,a.platform,digest,payload,a.revision,r.revision)) THEN
    RAISE EXCEPTION 'PUBLICATION_PROVENANCE_CONFLICT';
  END IF;
  UPDATE jsm_v2_business.subjects SET status='COMPLETE',result_sha=digest,result_text=payload WHERE id=chosen AND status='RESEARCHING';
  PERFORM jsm_v2_business.guard_record(r.record_id);
  IF s.status='RESEARCHING' AND s.deadline<=clock_timestamp() THEN RAISE EXCEPTION 'SUBJECT_DEADLINE_EXCEEDED'; END IF;
  PERFORM jsm_v2_business.worker();
  RETURN jsonb_build_object('ok',true,'data',jsonb_build_object('status','COMPLETE','result_sha',digest));
EXCEPTION WHEN OTHERS THEN
  RETURN jsm_v2_runtime.refuse(CASE WHEN SQLERRM~'^[A-Z0-9_]{1,120}$' THEN SQLERRM ELSE 'SUBJECT_OPERATION_REFUSED' END);
END $$;

CREATE FUNCTION public.jsm_v2_subjects(p_operation text,p_payload jsonb) RETURNS jsonb
LANGUAGE sql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
  SELECT jsm_v2_business.dispatch(p_operation,p_payload);
$$;
REVOKE ALL ON ALL TABLES IN SCHEMA jsm_v2_business FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA jsm_v2_business FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.jsm_v2_subjects(text,jsonb) FROM PUBLIC,anon,authenticated,service_role;
GRANT USAGE ON SCHEMA jsm_v2_business TO authenticated;
GRANT EXECUTE ON FUNCTION jsm_v2_business.dispatch(text,jsonb),public.jsm_v2_subjects(text,jsonb) TO authenticated;
COMMIT;
