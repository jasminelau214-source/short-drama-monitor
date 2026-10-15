"""C7 real disposable PostgreSQL; synthetic Auth/ledgers; never cloud or a live queue."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import argparse,hashlib,json,uuid,unittest
from pathlib import Path
import test_supabase_subjects_v2 as fxt
from postgres_test_harness_v2 import LocalPostgresFixture
from supabase_journal_v2 import SupabaseJournalConfig
from supabase_subjects_v2 import SupabaseSubjectStore
from research_completion_v2 import RESULT_FIELDS,ResearchWritebackRejected
from drama_identity_v2 import IdentityReviewRequired
ROOT=Path(__file__).resolve().parent
BIN_DIR=None
WORKER='33333333-3333-4333-8333-333333333333'
OTHER='44444444-4444-4444-8444-444444444444'
def lit(v):return "'"+str(v).replace("'","''")+"'"
def js(v):return lit(json.dumps(v,ensure_ascii=False,allow_nan=False))+'::jsonb'
class PostgresSubjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if BIN_DIR is None:raise RuntimeError('Explicit disposable fixture PostgreSQL binaries required')
        cls.pg=LocalPostgresFixture(BIN_DIR).start()
        try:
            cls.pg.sql("""CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN;
            GRANT authenticated TO jsm_fixture_worker;
            CREATE SCHEMA auth;
            CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
              SELECT (nullif(current_setting('request.jwt.claims',true),'')::jsonb->>'sub')::uuid;
            $$;
            GRANT USAGE ON SCHEMA auth TO authenticated;
            GRANT EXECUTE ON FUNCTION auth.uid() TO authenticated;""")
            for file in ('20261013060354_v2_isolated_call_journal.sql','20261014100219_v2_staging_default_helper_permissions.sql','20261015054817_v2_isolated_subject_queue.sql'):
                cls.pg.sql((ROOT/'supabase'/'migrations'/file).read_text(encoding='utf8'))
            cls.instance=cls.pg.sql('SELECT instance_id FROM jsm_v2_runtime.meta;')
        except BaseException:cls.pg.close();raise
    @classmethod
    def tearDownClass(cls):cls.pg.close()
    def setUp(self):
        self.pg.sql("""TRUNCATE jsm_v2_business.publications,jsm_v2_business.claim_requests,
          jsm_v2_business.observations,jsm_v2_business.subjects,jsm_v2_business.records,jsm_v2_business.authority;
          DELETE FROM jsm_v2_runtime.workers;
          INSERT INTO jsm_v2_runtime.workers VALUES ("""+lit(WORKER)+""",true,clock_timestamp()+interval '1 hour');""")
        self.f,self.now=fxt.fresh_fixture();self.addCleanup(self.f.doCleanups)
        self.snap=fxt.snapshot(self.f,self.now)
        self.payload=json.dumps({k:self.f.result[k] for k in sorted(RESULT_FIELDS)},ensure_ascii=False,sort_keys=True,separators=(',',':'))
        self.digest=hashlib.sha256(self.payload.encode()).hexdigest()
        self.seed()
        case=self
        class Bridge:
            def post(self,config,operation,payload):return case.rpc(operation,payload)
        self.bridge=Bridge()
        self.config=SupabaseJournalConfig('s'*20,self.instance,'sb_publishable_synthetic','synthetic.worker.jwt')
        self.store=SupabaseSubjectStore(self.config,transport=self.bridge,now=lambda:self.now)
    def rpc(self,op,payload,*,user=WORKER,role='authenticated'):
        assert role in {'anon','authenticated','service_role'}
        q='BEGIN; SET ROLE '+role+'; SET request.jwt.claims='+js({'sub':user}).removesuffix('::jsonb')+'; '
        q+='SELECT public.jsm_v2_subjects('+lit(op)+','+js(payload)+'); COMMIT;'
        return json.loads(self.pg.sql(q,worker=role=='authenticated'))
    def seed(self,record='record:1',canonical='content:1',scope='scope:1',platform='NetShort'):
        # Additional rows in queue-only tests exercise protected SQL metadata,
        # not a claim that their synthetic completion snapshots are approved.
        a=self.f.context.scope
        self.pg.sql("INSERT INTO jsm_v2_business.authority VALUES ("+','.join(map(lit,[scope,a.collection_date.isoformat(),platform,a.source_type,a.target_key,self.f.context.origin_run_id,self.snap['authority_sha']]))+",0,clock_timestamp()+interval '1 hour') ON CONFLICT(scope_id) DO NOTHING;"+
        "INSERT INTO jsm_v2_business.records(record_id,canonical_id,scope_id,run_id,authority_sha,source_entity_id,title,rank,context_text,context_sha,revision,valid_until,approved_result_sha) VALUES ("+
        ','.join(map(lit,[record,canonical,scope,self.f.context.origin_run_id,self.snap['authority_sha'],self.f.context.source_entity_id,self.f.task['title']]))+",1,"+
        ','.join(map(lit,[self.snap['context_text'],self.snap['context_sha']]))+",0,clock_timestamp()+interval '1 hour',"+lit(self.digest)+");")
    def claim(self):
        self.store.register('record:1');return self.store.claim()[0]
    def complete_payload(self,task):
        return dict(subject_id=task['id'],owner=self.store._owners[task['id']],record_id=task['record_id'],
          context_sha=self.snap['context_sha'],authority_revision=0,context_revision=0,
          payload_text=self.payload,result_sha=self.digest)
    def count(self,table):
        assert table in {'subjects','observations','publications','claim_requests'}
        return int(self.pg.sql('SELECT count(*) FROM jsm_v2_business.'+table+';'))
    def test_migration_adapter_and_atomic_complete(self):
        task=self.claim();ctx=self.store.context(task,'BEFORE_WRITEBACK')
        self.assertEqual(self.store.apply_research(task,self.f.result,validation_context=ctx)['status'],'COMPLETE')
        self.assertEqual(self.store.update(task['id'],status='COMPLETE')['status'],'COMPLETE')
        self.assertEqual(self.count('publications'),1)
        self.assertEqual(self.pg.sql("SELECT platform FROM jsm_v2_business.publications;"),'NetShort')
    def test_concurrent_same_content_registration_is_global_once(self):
        self.seed('record:other','content:1','scope:other','DramaBox')
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies=list(pool.map(lambda r:self.rpc('register',{'record_id':r}),['record:1','record:other']))
        self.assertTrue(all(x['ok'] for x in replies),replies)
        self.assertEqual(len({x['data']['subject_id'] for x in replies}),1)
        self.assertEqual(self.count('subjects'),1);self.assertEqual(self.count('observations'),2)
    def test_concurrent_claim_capacity_is_two_and_queue_is_retained(self):
        for n in (2,3):self.seed('record:'+str(n),'content:'+str(n))
        for n in (1,2,3):self.store.register('record:'+str(n))
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies=list(pool.map(lambda _:self.rpc('claim',{'request_id':str(uuid.uuid4())}),range(4)))
        self.assertTrue(all(x['ok'] for x in replies),replies)
        self.assertEqual(sum(x['data']['task'] is not None for x in replies),2)
        self.assertEqual(self.count('subjects'),3)
    def test_claim_lost_reply_reconciles_exact_request(self):
        self.store.register('record:1');request=str(uuid.uuid4())
        first=self.rpc('claim',{'request_id':request})
        self.assertEqual(first,self.rpc('claim',{'request_id':request}))
        self.assertEqual(self.count('claim_requests'),1)
        self.assertEqual(self.rpc('claim',{'request_id':request},user=OTHER)['reason'],'WORKER_NOT_AUTHORIZED')
    def test_wrong_capability_cannot_load_or_publish(self):
        task=self.claim();p=self.complete_payload(task);p['owner']=str(uuid.uuid4())
        self.assertFalse(self.rpc('complete',p)['ok'])
        self.assertEqual(self.count('publications'),0)
    def test_current_date_and_authority_digest_are_server_guards(self):
        task=self.claim();bound=self.store._bound(task['id'])
        self.pg.sql("UPDATE jsm_v2_business.authority SET collection_date=collection_date-1;")
        self.assertEqual(self.rpc('context',bound)['reason'],'AUTHORITY_OR_CONTEXT_CHANGED')
        self.pg.sql("UPDATE jsm_v2_business.authority SET collection_date=collection_date+1,snapshot_sha="+lit('b'*64)+";")
        self.assertEqual(self.rpc('complete',self.complete_payload(task))['reason'],'AUTHORITY_OR_CONTEXT_CHANGED')
        self.assertEqual(self.count('publications'),0)
    def test_changed_revision_or_unreviewed_hash_cannot_complete(self):
        task=self.claim();p=self.complete_payload(task)
        for changes in ({'authority_revision':1},{'context_revision':1},{'context_sha':'b'*64},{'record_id':'another'},{'result_sha':'b'*64}):
            self.assertFalse(self.rpc('complete',dict(p,**changes))['ok'])
        self.pg.sql('UPDATE jsm_v2_business.records SET approved_result_sha=NULL;')
        self.assertFalse(self.rpc('complete',p)['ok']);self.assertEqual(self.count('publications'),0)
    def test_failed_publication_rolls_back_complete(self):
        task=self.claim()
        self.pg.sql("""CREATE FUNCTION jsm_v2_business.fixture_fail() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN RAISE EXCEPTION 'FIXTURE_PUBLICATION_FAILED'; END $$;
          CREATE TRIGGER fixture_fail BEFORE INSERT ON jsm_v2_business.publications
          FOR EACH ROW EXECUTE FUNCTION jsm_v2_business.fixture_fail();""")
        try:
            self.assertEqual(self.rpc('complete',self.complete_payload(task))['reason'],'FIXTURE_PUBLICATION_FAILED')
            self.assertEqual(self.count('publications'),0)
            self.assertEqual(self.pg.sql('SELECT status FROM jsm_v2_business.subjects;'),'RESEARCHING')
        finally:self.pg.sql('DROP TRIGGER fixture_fail ON jsm_v2_business.publications; DROP FUNCTION jsm_v2_business.fixture_fail();')
    def test_lost_complete_reply_uses_exact_idempotent_publication(self):
        task=self.claim();p=self.complete_payload(task)
        first=self.rpc('complete',p)
        self.assertTrue(first['ok'],first)
        before=self.pg.sql('SELECT updated_at::text FROM jsm_v2_business.publications;')
        self.assertEqual(first,self.rpc('complete',p))
        self.assertEqual(before,self.pg.sql('SELECT updated_at::text FROM jsm_v2_business.publications;'))
        self.assertEqual(self.count('publications'),1)
    def test_complete_provenance_cannot_be_rebound_by_revision_change(self):
        task=self.claim();p=self.complete_payload(task);self.assertTrue(self.rpc('complete',p)['ok'])
        self.pg.sql('UPDATE jsm_v2_business.authority SET revision=1;')
        self.assertEqual(self.rpc('complete',dict(p,authority_revision=1))['reason'],'PUBLICATION_PROVENANCE_CONFLICT')
        self.assertEqual(self.pg.sql('SELECT authority_revision FROM jsm_v2_business.publications;'),'0')
    def test_expired_revoked_context_and_task_deadline_refuse_publication(self):
        task=self.claim();p=self.complete_payload(task)
        self.pg.sql('UPDATE jsm_v2_business.records SET revoked=true;')
        self.assertFalse(self.rpc('complete',p)['ok'])
        self.pg.sql("UPDATE jsm_v2_business.records SET revoked=false; UPDATE jsm_v2_business.subjects SET deadline=clock_timestamp()-interval '1 second';")
        self.assertFalse(self.rpc('complete',p)['ok']);self.assertEqual(self.count('publications'),0)
    def test_complete_cannot_be_downgraded(self):
        task=self.claim();self.assertTrue(self.rpc('complete',self.complete_payload(task))['ok'])
        for state in ('FAILED','NEEDS_GPT','REVIEW_REQUIRED','DEFERRED_FREE_QUOTA'):
            self.assertEqual(self.rpc('status',dict(self.store._bound(task['id']),status=state))['reason'],'COMPLETE_CANNOT_BE_DOWNGRADED')
    def test_quota_state_retains_subject_without_publishing(self):
        task=self.claim();self.store.update(task['id'],status='DEFERRED_FREE_QUOTA')
        self.assertEqual(self.count('subjects'),1);self.assertEqual(self.count('publications'),0)
        self.assertEqual(self.store.claim(),[])
    def test_anonymous_service_role_and_unlisted_worker_denied(self):
        for role in ('anon','service_role'):
            with self.assertRaisesRegex(RuntimeError,'permission denied'):self.rpc('info',{},role=role)
        self.assertEqual(self.rpc('info',{},user=OTHER)['reason'],'WORKER_NOT_AUTHORIZED')
        self.pg.sql('UPDATE jsm_v2_runtime.workers SET enabled=false;')
        self.assertEqual(self.rpc('info',{})['reason'],'WORKER_NOT_AUTHORIZED')
    def test_worker_cannot_self_approve_or_read_context_tables(self):
        for sql in ('SELECT * FROM jsm_v2_business.records;','UPDATE jsm_v2_business.records SET approved_result_sha='+lit(self.digest)+';','ALTER TABLE jsm_v2_business.records DISABLE ROW LEVEL SECURITY;'):
            self.assertNotEqual(self.pg.sql('SET ROLE authenticated; '+sql,worker=True,check=False).returncode,0)
        self.assertEqual(self.pg.sql("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='jsm_v2_business' AND c.relkind='r' AND c.relrowsecurity;"),'6')
    def test_request_json_cannot_add_approval_or_operation(self):
        self.assertEqual(self.rpc('register',{'record_id':'record:1','approved':True})['reason'],'SUBJECT_PAYLOAD_INVALID')
        self.assertEqual(self.rpc('approve',{})['reason'],'SUBJECT_PAYLOAD_INVALID')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--postgres-bin',required=True)
    args,rest=parser.parse_known_args();BIN_DIR=args.postgres_bin
    unittest.main(argv=[__file__]+rest)
