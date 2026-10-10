-- SYNTHETIC EXPERIMENT ONLY. Not a Supabase migration or runtime adapter.
BEGIN;
DO $$ BEGIN
  IF current_database() <> 'jsm_v2_fixture'
     OR current_setting('jsm.fixture_scope', true) IS DISTINCT FROM 'C2_SYNTHETIC_ONLY' THEN
    RAISE EXCEPTION 'Disposable JSM test cluster required';
  END IF;
END $$;
CREATE SCHEMA jsm_fixture_v2;
REVOKE ALL ON SCHEMA jsm_fixture_v2 FROM PUBLIC;
CREATE TABLE jsm_fixture_v2.authority (
  scope_id text PRIMARY KEY, collection_date date NOT NULL,
  platform text NOT NULL CHECK(platform IN ('DramaBox','FlexTV','GoodShort','MoboReels','NetShort','ReelShort')),
  source_type text NOT NULL CHECK(source_type IN ('OFFICIAL_WEB','SHORT_DRAMA_APP')),
  target_key text NOT NULL, run_id text NOT NULL,
  snapshot_sha text NOT NULL CHECK(snapshot_sha ~ '^[0-9a-f]{64}$'),
  revision bigint NOT NULL CHECK(revision>=0),
  UNIQUE(collection_date,platform,source_type,target_key)
);
CREATE TABLE jsm_fixture_v2.reviews (
  review_id text PRIMARY KEY, record_id text NOT NULL, canonical_id text NOT NULL,
  platform text NOT NULL, authority_sha text NOT NULL,
  result_sha text CHECK(result_sha ~ '^[0-9a-f]{64}$'),
  valid_until timestamptz NOT NULL, revoked boolean NOT NULL DEFAULT false,
  revision bigint NOT NULL CHECK(revision>=0)
);
CREATE TABLE jsm_fixture_v2.records (
  record_id text PRIMARY KEY, canonical_id text NOT NULL, platform text NOT NULL,
  source_type text NOT NULL, entity_id text NOT NULL, scope_id text NOT NULL REFERENCES jsm_fixture_v2.authority,
  run_id text NOT NULL, review_id text NOT NULL REFERENCES jsm_fixture_v2.reviews,
  UNIQUE(platform,source_type,entity_id)
);
CREATE TABLE jsm_fixture_v2.subjects (
  canonical_id text PRIMARY KEY, research_id text UNIQUE NOT NULL,
  status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','RESEARCHING','DEFERRED_FREE_QUOTA','COMPLETE','REVIEW_REQUIRED','NEEDS_GPT','FAILED')),
  result_sha text CHECK(result_sha ~ '^[0-9a-f]{64}$'),
  CHECK((status='COMPLETE')=(result_sha IS NOT NULL))
);
CREATE TABLE jsm_fixture_v2.observations (
  record_id text PRIMARY KEY REFERENCES jsm_fixture_v2.records,
  canonical_id text NOT NULL REFERENCES jsm_fixture_v2.subjects
);
CREATE TABLE jsm_fixture_v2.overrides (
  record_id text PRIMARY KEY REFERENCES jsm_fixture_v2.records,
  canonical_id text NOT NULL REFERENCES jsm_fixture_v2.subjects, platform text NOT NULL,
  result_sha text NOT NULL, payload jsonb NOT NULL,
  authority_revision bigint NOT NULL, review_revision bigint NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- One lock order for registration/writeback: authority -> review -> record -> subject.
CREATE FUNCTION jsm_fixture_v2.guard_binding(
  p_record text,p_canonical text,p_platform text,p_authority_revision bigint,
  p_review_revision bigint,p_run text,p_snapshot text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,jsm_fixture_v2,pg_temp AS $$
DECLARE r records%ROWTYPE; a authority%ROWTYPE; v reviews%ROWTYPE;
        initial_scope text; initial_review text;
BEGIN
  SELECT scope_id,review_id INTO initial_scope,initial_review FROM records WHERE record_id=p_record;
  IF NOT FOUND THEN RAISE EXCEPTION 'RECORD_NOT_FOUND'; END IF;
  SELECT * INTO a FROM authority WHERE scope_id=initial_scope FOR UPDATE;
  SELECT * INTO v FROM reviews WHERE review_id=initial_review FOR UPDATE;
  SELECT * INTO r FROM records WHERE record_id=p_record FOR UPDATE;
  IF r.scope_id IS DISTINCT FROM initial_scope OR r.review_id IS DISTINCT FROM initial_review
     OR r.canonical_id IS DISTINCT FROM p_canonical OR r.platform IS DISTINCT FROM p_platform
     OR r.platform IS DISTINCT FROM a.platform OR r.source_type IS DISTINCT FROM a.source_type
     OR r.run_id IS DISTINCT FROM a.run_id OR p_run IS DISTINCT FROM a.run_id
     OR p_snapshot IS DISTINCT FROM a.snapshot_sha OR a.revision IS DISTINCT FROM p_authority_revision THEN
    RAISE EXCEPTION 'BINDING_OR_AUTHORITY_CHANGED';
  END IF;
  IF v.revoked OR v.valid_until<=clock_timestamp()
     OR v.revision IS DISTINCT FROM p_review_revision
     OR v.record_id IS DISTINCT FROM r.record_id OR v.canonical_id IS DISTINCT FROM r.canonical_id
     OR v.platform IS DISTINCT FROM r.platform OR v.authority_sha IS DISTINCT FROM a.snapshot_sha THEN
    RAISE EXCEPTION 'PROTECTED_REVIEW_INVALID';
  END IF;
  RETURN v.result_sha;
END $$;

CREATE FUNCTION jsm_fixture_v2.register_subject(
  p_record text,p_canonical text,p_platform text,p_authority_revision bigint,
  p_review_revision bigint,p_run text,p_snapshot text
) RETURNS TABLE(research_id text,created boolean) LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,jsm_fixture_v2,pg_temp AS $$
DECLARE chosen text; was_created boolean;
BEGIN
  PERFORM guard_binding(p_record,p_canonical,p_platform,p_authority_revision,p_review_revision,p_run,p_snapshot);
  INSERT INTO subjects(canonical_id,research_id) VALUES(p_canonical,'research:'||gen_random_uuid()::text)
    ON CONFLICT(canonical_id) DO NOTHING RETURNING subjects.research_id INTO chosen;
  was_created=FOUND;
  IF NOT was_created THEN SELECT s.research_id INTO chosen FROM subjects s WHERE s.canonical_id=p_canonical; END IF;
  INSERT INTO observations VALUES(p_record,p_canonical) ON CONFLICT(record_id) DO NOTHING;
  IF NOT EXISTS(SELECT FROM observations WHERE record_id=p_record AND canonical_id=p_canonical) THEN
    RAISE EXCEPTION 'OBSERVATION_IDENTITY_CONFLICT';
  END IF;
  RETURN QUERY SELECT chosen,was_created;
END $$;

CREATE FUNCTION jsm_fixture_v2.guarded_write(
  p_record text,p_canonical text,p_platform text,p_authority_revision bigint,
  p_review_revision bigint,p_run text,p_snapshot text,p_payload text,p_result_sha text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,jsm_fixture_v2,pg_temp AS $$
DECLARE approved text; s subjects%ROWTYPE; changed integer;
BEGIN
  approved=guard_binding(p_record,p_canonical,p_platform,p_authority_revision,p_review_revision,p_run,p_snapshot);
  IF approved IS DISTINCT FROM p_result_sha OR p_result_sha IS NULL
     OR encode(sha256(convert_to(p_payload,'UTF8')),'hex') IS DISTINCT FROM p_result_sha
     OR jsonb_typeof(p_payload::jsonb) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'RESULT_NOT_REVIEWED_OR_CHANGED';
  END IF;
  SELECT * INTO s FROM subjects WHERE canonical_id=p_canonical FOR UPDATE;
  IF NOT FOUND OR NOT EXISTS(SELECT FROM observations WHERE record_id=p_record AND canonical_id=p_canonical)
     OR s.status NOT IN ('RESEARCHING','COMPLETE')
     OR (s.status='COMPLETE' AND s.result_sha IS DISTINCT FROM p_result_sha) THEN
    RAISE EXCEPTION 'GLOBAL_RESEARCH_NOT_WRITABLE';
  END IF;
  INSERT INTO overrides(record_id,canonical_id,platform,result_sha,payload,authority_revision,review_revision)
    VALUES(p_record,p_canonical,p_platform,p_result_sha,p_payload::jsonb,p_authority_revision,p_review_revision)
    ON CONFLICT(record_id) DO UPDATE SET canonical_id=excluded.canonical_id,platform=excluded.platform,
      result_sha=excluded.result_sha,payload=excluded.payload,authority_revision=excluded.authority_revision,
      review_revision=excluded.review_revision,updated_at=clock_timestamp()
    WHERE (overrides.canonical_id,overrides.platform,overrides.result_sha,overrides.payload,overrides.authority_revision,overrides.review_revision)
      IS DISTINCT FROM (excluded.canonical_id,excluded.platform,excluded.result_sha,excluded.payload,excluded.authority_revision,excluded.review_revision);
  GET DIAGNOSTICS changed=ROW_COUNT;
  UPDATE subjects SET status='COMPLETE',result_sha=p_result_sha WHERE canonical_id=p_canonical AND status='RESEARCHING';
  RETURN CASE WHEN changed=0 THEN 'ALREADY_APPLIED' ELSE 'APPLIED' END;
END $$;

REVOKE ALL ON ALL TABLES IN SCHEMA jsm_fixture_v2 FROM PUBLIC,jsm_fixture_worker;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA jsm_fixture_v2 FROM PUBLIC,jsm_fixture_worker;
GRANT USAGE ON SCHEMA jsm_fixture_v2 TO jsm_fixture_worker;
GRANT EXECUTE ON FUNCTION jsm_fixture_v2.register_subject(text,text,text,bigint,bigint,text,text),
  jsm_fixture_v2.guarded_write(text,text,text,bigint,bigint,text,text,text,text) TO jsm_fixture_worker;
COMMIT;
