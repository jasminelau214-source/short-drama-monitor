"""Real PostgreSQL migration/RPC-adapter parity; Auth/PostgREST are stubs.

Only an owned disposable loopback cluster is accepted. No cloud DSN or env
target input. SQL role/JWT claims here are synthetic, not live JWT validation.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import argparse
import json
from pathlib import Path
import subprocess
import time
import unittest
from urllib.parse import quote
import uuid

import test_supabase_journal_v2 as client_fixture
import supabase_journal_v2 as supabase
import research_execution_v2 as execution
from postgres_test_harness_v2 import LocalPostgresFixture

BIN_DIR=None
ADVISOR_OUTPUT=None
ROOT=Path(__file__).resolve().parent
MIGRATION=ROOT/'supabase'/'migrations'/'20261013060354_v2_isolated_call_journal.sql'
WORKER='33333333-3333-4333-8333-333333333333'
OTHER='44444444-4444-4444-8444-444444444444'


def literal(value): return "'"+value.replace("'","''")+"'"


class PostgresSupabaseJournalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if BIN_DIR is None:raise RuntimeError('Explicit fixture PostgreSQL binaries required; no skip')
        cls.pg=LocalPostgresFixture(BIN_DIR).start()
        try:
            cls.pg.sql('''CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN;
              GRANT authenticated TO jsm_fixture_worker;
              CREATE SCHEMA auth;
              CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
                SELECT (nullif(current_setting('request.jwt.claims',true),'')::jsonb->>'sub')::uuid;
              $$;
              GRANT USAGE ON SCHEMA auth TO authenticated;
              GRANT EXECUTE ON FUNCTION auth.uid() TO authenticated;''')
            cls.pg.sql(MIGRATION.read_text(encoding='utf-8'))
            cls.instance=cls.pg.sql('SELECT instance_id FROM jsm_v2_runtime.meta;')
            cls.advisors={'status':'NOT_RUN'}
        except BaseException:
            cls.pg.close();raise

    @classmethod
    def tearDownClass(cls):
        try:
            if ADVISOR_OUTPUT is not None:cls.run_advisors()
        finally:cls.pg.close()

    def setUp(self):
        self.pg.sql('''TRUNCATE jsm_v2_runtime.calls,jsm_v2_runtime.wait_events,jsm_v2_runtime.tasks;
          DELETE FROM jsm_v2_runtime.workers;
          INSERT INTO jsm_v2_runtime.workers VALUES ('''+literal(WORKER)+''',true,clock_timestamp()+interval '1 hour');''')
        case=self
        class SqlRpcBridge:
            def post(self,config,operation,payload):return case.rpc(operation,payload)
        self.bridge=SqlRpcBridge()
        self.config=supabase.SupabaseJournalConfig('s'*20,self.instance,'sb_publishable_synthetic','synthetic.worker.jwt')
        self.journal=supabase.SupabaseCallJournal(self.config,transport=self.bridge)

    def rpc(self,operation,payload,*,user=WORKER,role='authenticated'):
        assert role in {'authenticated','anon','service_role'}
        sql='BEGIN; SET ROLE '+role+'; SET request.jwt.claims='+literal(json.dumps({'sub':user}))+'; '
        sql+='SELECT public.jsm_v2_journal('+literal(operation)+','+literal(json.dumps(payload,ensure_ascii=False,allow_nan=False))+'::jsonb); COMMIT;'
        return json.loads(self.pg.sql(sql,worker=role=='authenticated'))

    def start(self,call_id='search:1',stage='SEARCH',task='task:1',binding='a'*64):
        return self.journal.start(task,call_id,stage,binding,time.time())

    def finish(self,owner,*,kind='success',response=None,elapsed=0,delay=0,call_id='search:1',task='task:1'):
        if response is None and kind=='success':response={}
        return self.journal.finish(task,call_id,owner,elapsed=elapsed,kind=kind,now=time.time(),response=response,delay=delay)

    def test_real_migration_and_client_cache_survive_new_adapter(self):
        _,owner,_=self.start()
        self.assertEqual(self.finish(owner,response={'results':[{'title':'Synthetic drama'}]}),'SUCCEEDED')
        reopened=supabase.SupabaseCallJournal(self.config,transport=self.bridge)
        cached,next_owner,remaining=reopened.start('task:1','search:1','SEARCH','a'*64,time.time())
        self.assertEqual(cached,{'results':[{'title':'Synthetic drama'}]})
        self.assertIsNone(next_owner);self.assertEqual(remaining,0)
        self.assertEqual(reopened.inspect('task:1','search:1')['attempts'],1)

    def test_actual_execution_session_uses_postgres_and_replays_no_provider_call(self):
        fixture=client_fixture.execution_fixture.ResearchExecutionTests();fixture.setUp()
        try:
            fixture.clock.base=datetime.now(timezone.utc)
            options=dict(journal=self.journal,now=lambda:datetime.now(timezone.utc),monotonic=time.monotonic,sleep=time.sleep)
            result=fixture.make_session(**options).call('SEARCH','search:1',client_fixture.execution_fixture.SEARCH)
            self.assertEqual(result,{'results':[]})
            self.assertEqual(fixture.make_session(**options).call('SEARCH','search:1',client_fixture.execution_fixture.SEARCH),result)
            self.assertEqual(fixture.transport.once.call_count,1)
        finally:fixture.doCleanups()

    def test_lost_finish_reply_is_reconciled_without_repeating_provider_call(self):
        bridge=self.bridge
        class LostFinishReply:
            def post(self,config,operation,payload):
                result=bridge.post(config,operation,payload)
                if operation=='finish':raise execution.ResearchExecutionStopped('REVIEW_REQUIRED','SUPABASE_RPC_OUTCOME_UNCERTAIN')
                return result
        journal=supabase.SupabaseCallJournal(self.config,transport=LostFinishReply())
        fixture=client_fixture.execution_fixture.ResearchExecutionTests();fixture.setUp()
        try:
            fixture.clock.base=datetime.now(timezone.utc)
            options=dict(now=lambda:datetime.now(timezone.utc),monotonic=time.monotonic,sleep=time.sleep)
            with self.assertRaises(execution.ResearchExecutionStopped):
                fixture.make_session(journal=journal,**options).call('SEARCH','search:1',client_fixture.execution_fixture.SEARCH)
            self.assertEqual(self.journal.inspect('task:1','search:1')['state'],'SUCCEEDED')
            self.assertEqual(fixture.make_session(journal=self.journal,**options).call('SEARCH','search:1',client_fixture.execution_fixture.SEARCH),{'results':[]})
            self.assertEqual(fixture.transport.once.call_count,1)
        finally:fixture.doCleanups()

    def test_concurrent_duplicate_reservations_only_one_can_dispatch(self):
        def reserve():
            try:return self.start()[1]
            except execution.ResearchExecutionStopped:return None
        with ThreadPoolExecutor(max_workers=2) as pool:owners=list(pool.map(lambda _:reserve(),range(2)))
        self.assertEqual(sum(owner is not None for owner in owners),1)
        self.assertEqual(self.journal.inspect('task:1','search:1')['attempts'],1)
        self.assertEqual(self.pg.sql("SELECT count(*) FROM jsm_v2_runtime.calls WHERE state='INFLIGHT';"),'1')

    def test_inflight_reopen_is_held_instead_of_reissued(self):
        self.start()
        reopened=supabase.SupabaseCallJournal(self.config,transport=self.bridge)
        with self.assertRaises(execution.ResearchExecutionStopped) as exc:reopened.start('task:1','search:1','SEARCH','a'*64,time.time())
        self.assertEqual(exc.exception.status,'REVIEW_REQUIRED')
        self.assertNotIn('owner',reopened.inspect('task:1','search:1'))

    def test_finish_is_idempotent_and_conflict_cannot_overwrite(self):
        _,owner,_=self.start()
        self.finish(owner,response={'result':'one'},elapsed=2)
        active=self.pg.sql("SELECT active_seconds FROM jsm_v2_runtime.tasks WHERE id='task:1';")
        self.finish(owner,response={'result':'one'},elapsed=2)
        self.assertEqual(self.pg.sql("SELECT active_seconds FROM jsm_v2_runtime.tasks WHERE id='task:1';"),active)
        with self.assertRaisesRegex(execution.ResearchExecutionStopped,'OUTCOME_CONFLICT'):self.finish(owner,response={'result':'two'},elapsed=2)
        self.assertEqual(self.journal.inspect('task:1','search:1')['response'],{'result':'one'})

    def test_wrong_owner_and_changed_binding_never_finish_or_reuse(self):
        _,owner,_=self.start()
        with self.assertRaisesRegex(execution.ResearchExecutionStopped,'CALL_OWNER_CHANGED'):self.finish(str(uuid.uuid4()))
        self.finish(owner)
        with self.assertRaisesRegex(execution.ResearchExecutionStopped,'CALL_BINDING_CHANGED'):self.start(binding='b'*64)

    def test_server_clock_prevents_client_backoff_bypass_and_bounds_three_attempts(self):
        for number in range(1,4):
            _,owner,_=self.start()
            state=self.finish(owner,kind='transient',delay=2)
            self.assertEqual(state,'FAILED' if number==3 else 'RETRY_WAIT')
            if number<3:
                with self.assertRaisesRegex(execution.ResearchExecutionStopped,'CALL_ATTEMPT_OR_BACKOFF_BOUNDARY'):
                    self.journal.start('task:1','search:1','SEARCH','a'*64,10**15)
                # Explicit fixture-only time advancement, no production mutation.
                self.pg.sql("UPDATE jsm_v2_runtime.calls SET retry_at=0 WHERE task_id='task:1';")
        with self.assertRaises(execution.ResearchExecutionStopped) as exc:self.start()
        self.assertEqual(exc.exception.status,'FAILED')
        self.assertEqual(self.journal.inspect('task:1','search:1')['attempts'],3)

    def test_server_dispatch_time_enforces_budget_even_if_client_underreports(self):
        _,owner,_=self.start()
        self.pg.sql("UPDATE jsm_v2_runtime.calls SET dispatched_at=clock_timestamp()-interval '301 seconds';")
        self.assertEqual(self.finish(owner,elapsed=0),'FAILED')
        with self.assertRaises(execution.ResearchExecutionStopped) as exc:self.start(call_id='search:2')
        self.assertEqual(exc.exception.status,'FAILED')

    def test_quota_preserves_attempt_and_requires_explicit_resume(self):
        _,owner,_=self.start()
        self.assertEqual(self.finish(owner,kind='quota'),'DEFERRED_FREE_QUOTA')
        with self.assertRaises(execution.ResearchExecutionStopped) as exc:self.start()
        self.assertEqual(exc.exception.status,'DEFERRED_FREE_QUOTA')
        self.journal.resume('task:1','search:1','a'*64)
        _,owner,_=self.start();self.finish(owner)
        self.assertEqual(self.journal.inspect('task:1','search:1')['attempts'],2)

    def test_wait_charging_is_idempotent_and_cannot_change_event(self):
        _,owner,_=self.start();self.finish(owner,kind='transient',delay=2)
        payload=dict(task_id='task:1',seconds=2,event_id=str(uuid.uuid4()))
        self.assertTrue(self.rpc('charge_wait',payload)['ok'])
        active=self.pg.sql("SELECT active_seconds FROM jsm_v2_runtime.tasks WHERE id='task:1';")
        self.assertTrue(self.rpc('charge_wait',payload)['ok'])
        self.assertEqual(self.pg.sql("SELECT active_seconds FROM jsm_v2_runtime.tasks WHERE id='task:1';"),active)
        self.assertEqual(self.rpc('charge_wait',dict(payload,seconds=3))['reason'],'WAIT_EVENT_CONFLICT')

    def test_search_round_cap_and_same_task_cross_stage_exclusion(self):
        _,owner,_=self.start()
        with self.assertRaisesRegex(execution.ResearchExecutionStopped,'TASK_ALREADY_HAS_CALL_INFLIGHT'):self.start(call_id='analyze:1',stage='ANALYZE')
        self.finish(owner)
        for number in (2,3):
            _,owner,_=self.start(call_id=f'search:{number}');self.finish(owner,call_id=f'search:{number}')
        with self.assertRaisesRegex(execution.ResearchExecutionStopped,'LOGICAL_CALL_LIMIT'):self.start(call_id='search:4')

    def test_anonymous_and_service_role_cannot_execute_rpc(self):
        for role in ('anon','service_role'):
            with self.assertRaisesRegex(RuntimeError,'permission denied'):self.rpc('info',{},role=role)
        self.assertEqual(self.rpc('info',{},user=OTHER)['reason'],'WORKER_NOT_AUTHORIZED')

    def test_worker_cannot_read_raw_tables_or_self_approve_or_disable_rls(self):
        for sql in ('SELECT * FROM jsm_v2_runtime.calls;',
                    'UPDATE jsm_v2_runtime.workers SET enabled=true;',
                    'ALTER TABLE jsm_v2_runtime.calls DISABLE ROW LEVEL SECURITY;'):
            result=self.pg.sql('SET ROLE authenticated; '+sql,worker=True,check=False)
            self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.pg.sql("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='jsm_v2_runtime' AND c.relkind='r' AND c.relrowsecurity;"),'5')

    def test_revoked_or_expired_worker_blocks_every_rpc_immediately(self):
        self.pg.sql('UPDATE jsm_v2_runtime.workers SET enabled=false;')
        with self.assertRaisesRegex(execution.ResearchExecutionStopped,'WORKER_NOT_AUTHORIZED'):self.journal.inspect('t','c')
        self.pg.sql("UPDATE jsm_v2_runtime.workers SET enabled=true,valid_until=clock_timestamp()-interval '1 second';")
        with self.assertRaisesRegex(execution.ResearchExecutionStopped,'WORKER_NOT_AUTHORIZED'):self.start()

    def test_temp_schema_shadowing_cannot_replace_protected_tables(self):
        claims=literal(json.dumps({'sub':WORKER}))
        sql="BEGIN; SET ROLE authenticated; SET request.jwt.claims="+claims+"; CREATE TEMP TABLE workers (enabled boolean); SELECT public.jsm_v2_journal('info','{}'); COMMIT;"
        self.assertEqual(json.loads(self.pg.sql(sql,worker=True))['data']['instance_id'],self.instance)

    def test_migration_refuses_legacy_database_before_any_new_ddl(self):
        self.pg.sql('CREATE TABLE public.research_tasks(id integer);')
        try:
            result=self.pg.sql(MIGRATION.read_text(encoding='utf-8'),check=False)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('JSM_V2_REFUSES_LEGACY_OR_PRODUCTION_DATABASE',result.stderr)
            self.assertEqual(self.pg.sql('SELECT instance_id FROM jsm_v2_runtime.meta;'),self.instance)
        finally:self.pg.sql('DROP TABLE public.research_tasks;')

    def test_invalid_rpc_payloads_cannot_mutate_store(self):
        for payload in (dict(task_id='t',call_id='c',stage='SEARCH',binding='a'*64,approved=True),
                        dict(task_id='t',call_id='c',stage='SEARCH',binding=None),
                        dict(task_id='',call_id='c',stage='SEARCH',binding='a'*64)):
            self.assertFalse(self.rpc('start',payload)['ok'])
        self.assertEqual(self.pg.sql('SELECT count(*) FROM jsm_v2_runtime.calls;'),'0')

    @classmethod
    def run_advisors(cls):
        cli=ROOT.parent/'tools'/'supabase-cli-2.120.0'/'supabase.exe'
        password=cls.pg.passwords[cls.pg.admin]
        # Plaintext is confined to this owned disposable loopback cluster.
        # The actual Supabase HTTP adapter always verifies modern HTTPS.
        url=f'postgresql://{cls.pg.admin}:{quote(password)}@127.0.0.1:{cls.pg.port}/{cls.pg.database}?sslmode=disable'
        env={k:v for k,v in cls.pg._env().items() if not k.upper().startswith('SUPABASE_')}
        result=subprocess.run([str(cli),'db','advisors','--db-url',url,'--type','all','--output','json',
            '--workdir',str(cls.pg.root)],cwd=cls.pg.root,capture_output=True,text=True,encoding='utf-8',errors='replace',env=env,timeout=45)
        output=(result.stdout+'\n'+result.stderr).replace(password,'[fixture password]').replace(url,'[owned loopback fixture URL]')
        path=Path(ADVISOR_OUTPUT)
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(output,encoding='utf-8')
        cls.advisors={'exitCode':result.returncode,'status':'CHECK_OUTPUT' if result.returncode==0 else 'UNAVAILABLE_ON_PARTIAL_SUPABASE_FIXTURE'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--bin-dir',required=True);parser.add_argument('--advisors-output')
    args,remaining=parser.parse_known_args();BIN_DIR=Path(args.bin_dir);ADVISOR_OUTPUT=args.advisors_output
    unittest.main(argv=['test_postgres_supabase_journal_v2.py',*remaining])
