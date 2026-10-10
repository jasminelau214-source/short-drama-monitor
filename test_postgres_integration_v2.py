"""Explicit local PostgreSQL experiment; never reads a database DSN or secrets.

Run directly with --bin-dir; omitted binaries are an error, never a PASS/skip.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

import test_research_completion_v2 as fixture
import research_completion_v2 as completion
from postgres_test_harness_v2 import LocalPostgresFixture

BIN_DIR = None
ROOT = Path(__file__).resolve().parent
SQL = (ROOT/'tests'/'fixtures'/'postgres_v2.sql').read_text(encoding='utf-8')


def literal(value):
    if type(value) is int: return str(value)
    if type(value) is not str or '\x00' in value: raise ValueError('Test literal only')
    return "'" + value.replace("'","''") + "'"


class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if BIN_DIR is None: raise RuntimeError('Run explicitly with --bin-dir; no external DB accepted')
        cls.pg = LocalPostgresFixture(BIN_DIR)
        cls.addClassCleanup(cls.pg.close)
        cls.pg.start()
        cls.pg.sql(SQL)

    def setUp(self):
        self.fixture = fixture.ResearchCompletionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.pg.sql('TRUNCATE jsm_fixture_v2.overrides,jsm_fixture_v2.observations,jsm_fixture_v2.subjects,jsm_fixture_v2.records,jsm_fixture_v2.reviews,jsm_fixture_v2.authority;')
        self.result = self.fixture.result
        self.decision = self.fixture.assess()
        self.assertEqual(self.decision.status,'COMPLETE')
        self.payload = json.dumps({key:self.result[key] for key in sorted(completion.RESULT_FIELDS)},ensure_ascii=False,sort_keys=True,separators=(',',':'))
        self.assertEqual(hashlib.sha256(self.payload.encode()).hexdigest(),self.decision.result_sha256)
        self.bindings = {}
        self.seed('record:1','NetShort')
        self.seed('record:2','ReelShort')

    def seed(self, record, platform, canonical='content:1'):
        context=self.fixture.context
        task=dict(self.fixture.task)
        if platform != context.scope.platform:
            run=self.fixture.batch.make_run(name='run:'+platform,scope=replace(context.scope,platform=platform))
            rows=tuple(replace(row,record_id=record if index==0 else record+':rank:'+str(index+1)) for index,row in enumerate(run.rows))
            run=self.fixture.batch.review(replace(run,rows=rows))
            context=replace(context,scope=run.scope,origin_run_id=run.run_id,record_id=run.rows[0].record_id,
                            source_entity_id=run.rows[0].source_entity_id,runs=(run,))
            task.update(platform=platform,analysis_run_id=run.run_id,record_id=context.record_id,source_entity_id=context.source_entity_id)
        proof=self.fixture.assess(context=context,task=task)
        self.assertEqual(proof.status,'COMPLETE')
        sha=proof.authority_snapshot_sha256
        scope='scope:'+record
        review='review:'+record
        self.bindings[record]=[record,canonical,platform,0,0,context.origin_run_id,sha]
        values=[scope,'2026-10-09',platform,'OFFICIAL_WEB','fixture_top10_v2',context.origin_run_id,sha,0]
        self.pg.sql('INSERT INTO jsm_fixture_v2.authority VALUES('+','.join(map(literal,values))+');')
        values=[review,record,canonical,platform,sha,proof.result_sha256]
        self.pg.sql('INSERT INTO jsm_fixture_v2.reviews VALUES('+','.join(map(literal,values))+",clock_timestamp()+interval '1 hour',false,0);")
        values=[record,canonical,platform,'OFFICIAL_WEB',context.source_entity_id,scope,context.origin_run_id,review]
        self.pg.sql('INSERT INTO jsm_fixture_v2.records VALUES('+','.join(map(literal,values))+');')

    def register(self, record='record:1', *, check=True, binding=None):
        args=self.bindings[record] if binding is None else binding
        return self.pg.sql('SELECT * FROM jsm_fixture_v2.register_subject('+','.join(map(literal,args))+');',worker=True,check=check)

    def prepare_write(self):
        self.register()
        # Synthetic state transition only; no provider, worker or live task.
        self.pg.sql("UPDATE jsm_fixture_v2.subjects SET status='RESEARCHING';")

    def write_sql(self, record='record:1', *, binding=None, payload=None, digest=None):
        args=list(self.bindings[record] if binding is None else binding)
        args.extend([self.payload if payload is None else payload,self.decision.result_sha256 if digest is None else digest])
        return 'SELECT jsm_fixture_v2.guarded_write('+','.join(map(literal,args))+');'

    def write(self, record='record:1', *, check=True, **kwargs):
        return self.pg.sql(self.write_sql(record,**kwargs),worker=True,check=check)

    def count(self, table):
        assert table in {'subjects','observations','overrides'}
        return int(self.pg.sql('SELECT count(*) FROM jsm_fixture_v2.'+table+';'))

    def assertRefused(self, result, reason):
        self.assertNotEqual(result.returncode,0)
        self.assertIn(reason,result.stderr)

    def test_real_engine_and_isolation_ignore_external_environment(self):
        self.assertTrue(self.pg.server_version.startswith('17.'))
        self.assertTrue(self.pg.cluster.is_relative_to(self.pg.root))
        self.assertEqual(self.pg.sql('SHOW listen_addresses;'),'127.0.0.1')
        with patch.dict(os.environ,{'PGHOST':'production.invalid','PGDATABASE':'production','PGSERVICE':'production','PGOPTIONS':'-c search_path=public','DATABASE_URL':'postgres://production.invalid'}):
            self.assertEqual(self.pg.sql('SELECT current_database();'),'jsm_v2_fixture')

    def test_sql_fixture_refuses_a_cluster_without_scope_marker(self):
        result=self.pg.sql("SET jsm.fixture_scope='NO';"+SQL.split('CREATE SCHEMA')[0],check=False)
        self.assertRefused(result,'Disposable JSM test cluster required')

    def test_worker_cannot_self_approve_or_mutate_protected_inputs(self):
        for statement in ("UPDATE jsm_fixture_v2.reviews SET revoked=false;", "UPDATE jsm_fixture_v2.authority SET revision=9;",
                          "INSERT INTO jsm_fixture_v2.subjects VALUES('forged','forged','PENDING',NULL);",
                          "DELETE FROM jsm_fixture_v2.overrides;", "SELECT jsm_fixture_v2.guard_binding('a','b','c',0,0,'d','e');"):
            self.assertRefused(self.pg.sql(statement,worker=True,check=False),'permission denied')

    def test_worker_temporary_table_cannot_shadow_protected_review(self):
        self.prepare_write()
        self.pg.sql("UPDATE jsm_fixture_v2.reviews SET revoked=true WHERE platform='NetShort';")
        # Temp tables are normally allowed by PostgreSQL. A definer must
        # resolve trusted tables before pg_temp, even in an attacker session.
        fake="CREATE TEMP TABLE reviews(review_id text,record_id text,canonical_id text,platform text,authority_sha text,result_sha text,valid_until timestamptz,revoked boolean,revision bigint);"
        values=['review:record:1','record:1','content:1','NetShort',self.bindings['record:1'][-1],self.decision.result_sha256]
        fake+='INSERT INTO reviews VALUES('+','.join(map(literal,values))+",clock_timestamp()+interval '1 hour',false,0);"
        self.assertRefused(self.pg.sql(fake+self.write_sql(),worker=True,check=False),'PROTECTED_REVIEW_INVALID')
        self.assertEqual(self.count('overrides'),0)

    def test_concurrent_cross_platform_discovery_creates_one_subject(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(self.register,['record:1','record:2']*4))
        self.assertEqual(sum(result.endswith('|t') for result in results),1)
        self.assertEqual(len({result.split('|')[0] for result in results}),1)
        self.assertEqual((self.count('subjects'),self.count('observations')),(1,2))

    def test_distinct_canonical_identity_does_not_merge_by_title(self):
        # An independently reviewed distinct identity; no title columns exist.
        self.pg.sql("UPDATE jsm_fixture_v2.records SET canonical_id='content:2' WHERE record_id='record:2'; UPDATE jsm_fixture_v2.reviews SET canonical_id='content:2' WHERE record_id='record:2';")
        self.bindings['record:2'][1]='content:2'
        self.register(); self.register('record:2')
        self.assertEqual(self.count('subjects'),2)

    def test_unregistered_pending_or_wrong_platform_cannot_write(self):
        self.assertRefused(self.write(check=False),'GLOBAL_RESEARCH_NOT_WRITABLE')
        self.register()
        self.assertRefused(self.write(check=False),'GLOBAL_RESEARCH_NOT_WRITABLE')
        self.prepare_write()
        changed=list(self.bindings['record:1']); changed[2]='ReelShort'
        self.assertRefused(self.write(binding=changed,check=False),'BINDING_OR_AUTHORITY_CHANGED')
        self.assertEqual(self.count('overrides'),0)

    def test_complete_validator_result_writes_only_its_bound_platform(self):
        self.prepare_write()
        self.assertEqual(self.write(),'APPLIED')
        self.assertEqual(self.pg.sql('SELECT record_id,platform FROM jsm_fixture_v2.overrides;'),'record:1|NetShort')
        self.assertEqual(self.pg.sql('SELECT status FROM jsm_fixture_v2.subjects;'),'COMPLETE')

    def test_citation_or_payload_changed_after_review_is_rejected(self):
        self.prepare_write()
        changed=self.payload.replace('Synthetic reviewed synopsis','unreviewed replacement')
        self.assertRefused(self.write(payload=changed,check=False),'RESULT_NOT_REVIEWED_OR_CHANGED')
        digest=hashlib.sha256(changed.encode()).hexdigest()
        self.assertRefused(self.write(payload=changed,digest=digest,check=False),'RESULT_NOT_REVIEWED_OR_CHANGED')
        self.assertEqual(self.count('overrides'),0)

    def test_authority_or_review_revision_change_rejects_stale_write(self):
        self.prepare_write()
        self.pg.sql("UPDATE jsm_fixture_v2.authority SET revision=1 WHERE platform='NetShort';")
        self.assertRefused(self.write(check=False),'BINDING_OR_AUTHORITY_CHANGED')
        self.pg.sql("UPDATE jsm_fixture_v2.authority SET revision=0 WHERE platform='NetShort'; UPDATE jsm_fixture_v2.reviews SET revision=1 WHERE platform='NetShort';")
        self.assertRefused(self.write(check=False),'PROTECTED_REVIEW_INVALID')
        self.assertEqual(self.count('overrides'),0)

    def test_superseding_run_cannot_accept_old_record_even_without_revision_bump(self):
        self.prepare_write()
        self.pg.sql("UPDATE jsm_fixture_v2.authority SET run_id='run:newer' WHERE platform='NetShort';")
        self.assertRefused(self.write(check=False),'BINDING_OR_AUTHORITY_CHANGED')
        self.assertEqual(self.count('overrides'),0)

    def test_one_source_observation_cannot_bind_two_different_content_ids(self):
        result=self.pg.sql("UPDATE jsm_fixture_v2.records SET platform='NetShort',canonical_id='content:other' WHERE record_id='record:2';",check=False)
        self.assertRefused(result,'duplicate key')
        self.assertEqual(self.count('subjects'),0)

    def test_expired_revoked_or_identity_conflicting_review_blocks_write(self):
        self.prepare_write()
        for change in ("revoked=true", "valid_until=clock_timestamp()-interval '1 second'", "canonical_id='content:other'"):
            self.pg.sql('UPDATE jsm_fixture_v2.reviews SET '+change+" WHERE platform='NetShort';")
            self.assertRefused(self.write(check=False),'PROTECTED_REVIEW_INVALID')
            self.pg.sql("UPDATE jsm_fixture_v2.reviews SET revoked=false,valid_until=clock_timestamp()+interval '1 hour',canonical_id='content:1' WHERE platform='NetShort';")
        self.assertEqual(self.count('overrides'),0)

    def test_duplicate_concurrent_writes_are_one_change_and_one_noop(self):
        self.prepare_write()
        with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(lambda _:self.write(),range(4)))
        self.assertEqual(results.count('APPLIED'),1)
        self.assertEqual(results.count('ALREADY_APPLIED'),3)
        previous=self.pg.sql('SELECT updated_at FROM jsm_fixture_v2.overrides;')
        self.assertEqual(self.write(),'ALREADY_APPLIED')
        self.assertEqual(self.pg.sql('SELECT updated_at FROM jsm_fixture_v2.overrides;'),previous)

    def test_global_reuse_still_requires_second_platform_record_and_review(self):
        self.prepare_write(); self.write()
        self.register('record:2')
        self.assertEqual(self.write('record:2'),'APPLIED')
        self.assertEqual(self.count('subjects'),1)
        self.assertEqual(self.count('overrides'),2)

    def test_rollback_discards_write_and_terminal_state_together(self):
        self.prepare_write()
        self.pg.sql('BEGIN;'+self.write_sql()+'ROLLBACK;',worker=True)
        self.assertEqual(self.count('overrides'),0)
        self.assertEqual(self.pg.sql('SELECT status FROM jsm_fixture_v2.subjects;'),'RESEARCHING')
        self.assertEqual(self.write(),'APPLIED')

    def test_revocation_committing_while_writer_waits_blocks_write(self):
        self.prepare_write()
        with ThreadPoolExecutor(max_workers=2) as pool:
            revoker=pool.submit(self.pg.sql,"BEGIN; UPDATE jsm_fixture_v2.reviews SET revoked=true,revision=1 WHERE platform='NetShort'; SELECT pg_sleep(3); COMMIT;")
            # Observe the actual row-locking backend before starting the writer.
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                waiting=self.pg.sql("SELECT count(*) FROM pg_stat_activity WHERE pid<>pg_backend_pid() AND usename='jsm_fixture_admin' AND wait_event='PgSleep';")
                if waiting=='1': break
            else: self.fail('Revocation transaction did not reach its synchronization point')
            writer=pool.submit(self.write,check=False)
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                blocked=self.pg.sql("SELECT count(*) FROM pg_stat_activity WHERE usename='jsm_fixture_worker' AND wait_event_type='Lock';")
                if blocked=='1': break
            else: self.fail('Writer was not observed waiting for the protected review lock')
            revoker.result(); result=writer.result()
        self.assertRefused(result,'PROTECTED_REVIEW_INVALID')
        self.assertEqual(self.count('overrides'),0)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--bin-dir',required=True)
    args,remaining=parser.parse_known_args()
    BIN_DIR=Path(args.bin_dir)
    unittest.main(argv=[sys.argv[0],*remaining])
