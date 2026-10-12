"""Actual candidate journal/transport/Worker; synthetic provider and approvals."""
from dataclasses import replace
from contextlib import closing
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import Mock, patch

import test_research_completion_v2 as completion_fixture
import research_execution_v2 as execution
import research_pipeline as pipeline
import research_worker as worker
from research_eligibility_v2 import FreeProviderApproval, ResearchPreflightDecision

SEARCH = {'query':'"Synthetic title" NetShort short drama', 'search_depth':'basic'}
MODEL = {'contents':[], 'generationConfig':{'responseMimeType':'application/json'}}


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.base = datetime(2026, 10, 12, tzinfo=timezone.utc)
        self.waits = []
    def now(self): return self.base + timedelta(seconds=self.value)
    def monotonic(self): return self.value
    def sleep(self, seconds):
        self.waits.append(seconds)
        self.value += seconds


class ResearchExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='jsm-execution-')
        self.addCleanup(self.temp.cleanup)
        self.clock = FakeClock()
        self.journal = execution.CandidateCallJournal(Path(self.temp.name), candidate_scope='JSM_V2_ISOLATED_CANDIDATE')
        self.credentials = {'SEARCH':'synthetic-search-key', 'ANALYZE':'synthetic-model-key'}
        self.transport = Mock()
        self.transport.once.return_value = {'results':[]}
        self.limiter = execution.AdaptiveCallLimiter()
        self.authorize = Mock(side_effect=self.grant)
        self.session = self.make_session()

    def grant(self, task_id, stage, call_id, attempt):
        provider = 'tavily' if stage == 'SEARCH' else 'gemini'
        start, end = self.clock.base-timedelta(hours=1), self.clock.base+timedelta(hours=1)
        preflight = ResearchPreflightDecision(True,'verified',provider,'run:1','a'*64,eligibility_revalidated=True)
        approval = FreeProviderApproval(provider,start,end,True,False,review_ref='policy:1',
            source_url='https://docs.tavily.com/' if stage == 'SEARCH' else 'https://ai.google.dev/',
            artifact_sha256='b'*64,approval_id='policy-approval:1')
        return execution.CallAuthorization(task_id,preflight,approval,hashlib.sha256(self.credentials[stage].encode()).hexdigest(),
            'search-basic' if stage == 'SEARCH' else 'gemini-test', 'account-review:1','c'*64,start,end,True,True)

    def make_session(self, task_id='task:1', **kw):
        options = dict(task_id=task_id,journal=self.journal,authorize=self.authorize,credentials=self.credentials,
            model='gemini-test',limiter=self.limiter,transport=self.transport,now=self.clock.now,
            monotonic=self.clock.monotonic,sleep=self.clock.sleep)
        options.update(kw)
        return execution.ResearchExecutionSession(**options)

    def test_exact_three_attempts_and_no_outer_retry_after_reopen(self):
        self.transport.once.side_effect = execution.ProviderFault('transient','synthetic 503')
        for session in (self.session,self.make_session()):
            with self.assertRaises(execution.ResearchExecutionStopped) as exc:
                session.call('SEARCH','search:1',SEARCH)
            self.assertEqual(exc.exception.status,'FAILED')
        self.assertEqual(self.transport.once.call_count,3)
        self.assertEqual(self.clock.waits,[2,4])
        self.assertEqual(self.journal.inspect('task:1','search:1')['attempts'],3)

    def test_success_is_cached_across_reopen_without_spending_again(self):
        self.transport.once.side_effect = [execution.ProviderFault('transient','timeout'), {'results':[{'title':'one'}]}]
        result = self.session.call('SEARCH','search:1',SEARCH)
        self.assertEqual(self.make_session().call('SEARCH','search:1',SEARCH),result)
        self.assertEqual(self.transport.once.call_count,2)
        # Every retry and cached reuse still invokes the protected current gate.
        self.assertGreaterEqual(self.authorize.call_count,6)
        raw = self.journal.path.read_bytes()
        for key in self.credentials.values(): self.assertNotIn(key.encode(),raw)

    def test_quota_defers_and_explicit_resume_keeps_attempt_count(self):
        self.transport.once.side_effect = [execution.ProviderFault('quota','limit'), {'results':[]}]
        with self.assertRaises(execution.ResearchExecutionStopped) as exc:
            self.session.call('SEARCH','search:1',SEARCH)
        self.assertEqual(exc.exception.status,'DEFERRED_FREE_QUOTA')
        self.clock.value += 86400  # Waiting must not consume the 300s active budget.
        self.clock.base += timedelta(days=1)
        self.clock.value -= 86400
        with self.assertRaises(execution.ResearchExecutionStopped): self.make_session().call('SEARCH','search:1',SEARCH)
        self.assertEqual(self.transport.once.call_count,1)
        resumed = self.make_session()
        resumed.resume_deferred('SEARCH','search:1',SEARCH)
        self.assertEqual(resumed.call('SEARCH','search:1',SEARCH),{'results':[]})
        self.assertEqual(self.journal.inspect('task:1','search:1')['attempts'],2)

    def test_no_quota_or_free_account_never_dispatches(self):
        for change in ({'billing_disabled':False}, {'credential_sha256':'d'*64}, {'product_id':'gemini-unreviewed'},
                       {'account_valid_until':self.clock.base}, {'quota_available':False}):
            self.authorize.side_effect = lambda *a,c=change:replace(self.grant(*a),**c)
            with self.assertRaises((execution.IdentityReviewRequired,execution.ResearchExecutionStopped)):
                self.session.call('SEARCH','search:1',SEARCH)
        self.transport.once.assert_not_called()

    def test_authority_revoked_after_backoff_blocks_next_attempt(self):
        self.transport.once.side_effect = execution.ProviderFault('transient','503')
        def gate(*args):
            value = self.grant(*args)
            return replace(value,provider=replace(value.provider,revoked=self.clock.value >= 2))
        self.authorize.side_effect = gate
        with self.assertRaises(execution.IdentityReviewRequired): self.session.call('SEARCH','search:1',SEARCH)
        self.assertEqual(self.transport.once.call_count,1)

    def test_request_or_authority_change_cannot_reuse_cached_answer(self):
        self.session.call('SEARCH','search:1',SEARCH)
        with self.assertRaises(execution.ResearchExecutionStopped):
            self.make_session().call('SEARCH','search:1',dict(SEARCH,query='another title'))
        self.authorize.side_effect = lambda *a:replace(self.grant(*a),preflight=replace(self.grant(*a).preflight,authority_snapshot_sha256='d'*64))
        with self.assertRaises(execution.ResearchExecutionStopped): self.make_session().call('SEARCH','search:1',SEARCH)
        self.assertEqual(self.transport.once.call_count,1)

    def test_inflight_crash_is_not_automatically_reissued(self):
        self.transport.once.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.session.call('SEARCH','search:1',SEARCH)
        self.assertEqual(self.journal.inspect('task:1','search:1')['state'],'INFLIGHT')
        self.transport.once.side_effect = None
        with self.assertRaises(execution.ResearchExecutionStopped) as exc: self.make_session().call('SEARCH','search:1',SEARCH)
        self.assertEqual(exc.exception.status,'REVIEW_REQUIRED')
        self.assertEqual(self.transport.once.call_count,1)

    def test_logical_search_round_cap_and_no_paid_tools(self):
        for i in range(1,4): self.session.call('SEARCH',f'search:{i}',SEARCH)
        with self.assertRaises(execution.ResearchExecutionStopped): self.session.call('SEARCH','search:4',SEARCH)
        for body in (dict(SEARCH,search_depth='advanced'),dict(SEARCH,auto_parameters=True)):
            with self.assertRaises(execution.IdentityReviewRequired): self.session.call('SEARCH','search:new',body)
        for body in (dict(MODEL,tools=[{'googleSearch':{}}]),dict(MODEL,generationConfig={'responseModalities':['IMAGE']})):
            with self.assertRaises(execution.IdentityReviewRequired): self.session.call('ANALYZE','analyze:1',body)
        self.assertEqual(self.transport.once.call_count,3)

    def test_total_active_budget_is_shared_across_search_and_model(self):
        def slow(**kw):
            self.assertLessEqual(kw['timeout'],300-self.clock.value)
            self.clock.value += 160
            return {}
        self.transport.once.side_effect = slow
        self.session.call('SEARCH','search:1',SEARCH)
        with self.assertRaises(execution.ResearchExecutionStopped) as exc: self.session.call('ANALYZE','analyze:1',MODEL)
        self.assertEqual(exc.exception.status,'FAILED')
        with self.assertRaises(execution.ResearchExecutionStopped): self.session.call('SEARCH','search:2',SEARCH)
        self.assertEqual(self.transport.once.call_count,2)

    def test_business_error_and_unknown_outcome_do_not_retry(self):
        for task,error in (('task:business',execution.ProviderFault('business','invalid')),('task:unknown',RuntimeError('credential must not leak'))):
            self.transport.once.side_effect = error
            with self.assertRaises(execution.ResearchExecutionStopped) as exc: self.make_session(task_id=task).call('SEARCH','search:1',SEARCH)
            self.assertEqual(exc.exception.status,'REVIEW_REQUIRED')
            self.assertNotIn('credential',str(exc.exception))
        self.assertEqual(self.transport.once.call_count,2)

    def test_rate_limit_lowers_shared_concurrency(self):
        self.transport.once.side_effect = [execution.ProviderFault('transient','429',rate_limited=True),{}]
        self.session.call('SEARCH','search:1',SEARCH)
        self.assertEqual(self.limiter.limit,1)
        self.assertEqual(self.transport.once.call_count,2)

    def test_simultaneous_duplicate_task_only_dispatches_once(self):
        entered, release = threading.Event(), threading.Event()
        def response(**kw):
            entered.set()
            if not release.wait(5): raise AssertionError('test handoff timeout')
            return {}
        self.transport.once.side_effect = response
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.session.call,'SEARCH','search:1',SEARCH)
            self.assertTrue(entered.wait(5))
            second = pool.submit(self.make_session().call,'SEARCH','search:1',SEARCH)
            try:
                with self.assertRaises(execution.ResearchExecutionStopped): second.result(timeout=5)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5),{})
        self.assertEqual(self.transport.once.call_count,1)

    def test_transport_maps_errors_without_hidden_retry_or_error_leak(self):
        transport = execution.HttpsJsonTransport()
        for stage,code,headers,expected in (('SEARCH',429,{},'transient'),('SEARCH',432,{},'quota'),
                ('SEARCH',433,{},'quota'),('ANALYZE',429,{},'quota'),('ANALYZE',429,{'Retry-After':'3'},'transient'),
                ('SEARCH',503,{},'transient'),('SEARCH',401,{},'business'),('SEARCH',302,{},'business')):
            opener = Mock()
            opener.open.side_effect = urllib.error.HTTPError('https://example.test',code,'unsafe-secret',headers,BytesIO(b'private-error-content'))
            with patch.object(execution.urllib.request,'build_opener',return_value=opener):
                with self.assertRaises(execution.ProviderFault) as exc:
                    transport.once(stage=stage,model='gemini-test',credential='synthetic-key',body=SEARCH,timeout=1)
            self.assertEqual(exc.exception.kind,expected)
            self.assertEqual(str(exc.exception),f'PROVIDER_HTTP_{code}')
            self.assertEqual(opener.open.call_count,1)

    def test_no_session_cannot_reach_old_four_attempt_clients(self):
        with patch.object(pipeline.urllib.request,'urlopen') as old:
            with self.assertRaises(execution.IdentityReviewRequired): pipeline._json_post('https://example.test',{},auth='not-a-key')
            with self.assertRaises(execution.IdentityReviewRequired): pipeline.gemini_request({},'not-a-key')
        old.assert_not_called()
        self.assertFalse(pipeline.configured())

    def test_candidate_platform_scope_does_not_expand_old_production_whitelist(self):
        for platform in execution.V2_PLATFORMS:
            self.assertEqual(pipeline.validate_search_identity('Synthetic title',platform,allowed_platforms=execution.V2_PLATFORMS)[1],platform)
        for platform in ('DramaWave','ShortMax'):
            with self.assertRaises(RuntimeError): pipeline.validate_search_identity('Synthetic title',platform,allowed_platforms=execution.V2_PLATFORMS)

    def test_worker_runs_two_tasks_and_shares_one_limiter(self):
        claims = Mock(side_effect=[[{'id':'task:1'}],[{'id':'task:2'}],[]])
        update, writer = Mock(), Mock()
        barrier = threading.Barrier(2)
        seen = []
        def factory(task,limiter):
            seen.append(limiter)
            return self.make_session(task_id=task['id'],limiter=limiter)
        def research(task,**kw):
            barrier.wait(timeout=5)
            return {'status':'DEFERRED_FREE_QUOTA'}
        handoff = worker.ResearchHandoff(claims,Mock(return_value=None),update,factory)
        self.assertTrue(worker.configured(handoff=handoff))
        with patch.object(worker,'research_task',side_effect=research),patch('builtins.print'):
            worker._run(apply_research=writer,handoff=handoff)
        self.assertEqual(update.call_count,2)
        self.assertIs(seen[0],seen[1])
        writer.assert_not_called()

    def test_pipeline_search_and_model_use_real_session_and_replay_only_calls(self):
        fixture = completion_fixture.ResearchCompletionTests()
        fixture.setUp()
        try:
            session = self.make_session(task_id=fixture.task['id'])
            search = {'results':[dict(fixture.source,raw_content=fixture.source['content'])]}
            model = {'candidates':[{'content':{'parts':[{'text':json.dumps(fixture.result)}]}}]}
            self.transport.once.side_effect = [search,model]
            output = pipeline.research_task(fixture.task,validation_context=fixture.context,
                review_context_provider=Mock(return_value=fixture.context),provider_session=session,now=completion_fixture.NOW)
            self.assertEqual(output['status'],'COMPLETE',output)
            self.assertTrue(fixture.app.apply_research_result(fixture.task,output['research'],validation_context=fixture.context,now=completion_fixture.NOW)['saved'])
            output2 = pipeline.research_task(fixture.task,validation_context=fixture.context,
                review_context_provider=Mock(return_value=fixture.context),provider_session=self.make_session(task_id=fixture.task['id']),now=completion_fixture.NOW)
            self.assertEqual(output2['status'],'COMPLETE',output2)
            self.assertEqual(self.transport.once.call_count,2)
            self.assertEqual(len(fixture.rows()),1)
        finally:
            fixture.doCleanups()

    def test_actual_worker_defers_model_quota_then_resumes_without_search_replay(self):
        fixture = completion_fixture.ResearchCompletionTests()
        fixture.setUp()
        try:
            search = {'results':[dict(fixture.source,raw_content=fixture.source['content'])]}
            model = {'candidates':[{'content':{'parts':[{'text':json.dumps(fixture.result)}]}}]}
            self.transport.once.side_effect = [search,execution.ProviderFault('quota','model-free-quota'),model]
            update = Mock()
            def factory(task,limiter): return self.make_session(task_id=task['id'],limiter=limiter)
            original = pipeline.research_task
            def run():
                handoff = worker.ResearchHandoff(Mock(side_effect=[[fixture.task],[]]),Mock(return_value=fixture.context),update,factory)
                with patch.object(worker,'research_task',side_effect=lambda *a,**kw:original(*a,**kw,now=completion_fixture.NOW)),patch('builtins.print'):
                    worker._run(apply_research=lambda *a,**kw:fixture.app.apply_research_result(*a,**kw,now=completion_fixture.NOW),handoff=handoff)
            run()
            self.assertEqual(update.call_args.kwargs['status'],'DEFERRED_FREE_QUOTA')
            self.assertEqual(fixture.rows(),[])
            exact_body = self.transport.once.call_args_list[1].kwargs['body']
            self.make_session(task_id=fixture.task['id']).resume_deferred('ANALYZE','analyze:1',exact_body)
            run()
            self.assertEqual(update.call_args.kwargs['status'],'COMPLETE')
            self.assertEqual([c.kwargs['stage'] for c in self.transport.once.call_args_list],['SEARCH','ANALYZE','ANALYZE'])
            self.assertEqual(len(fixture.rows()),1)
        finally:
            fixture.doCleanups()

    def test_expired_account_blocks_cached_reuse_without_network(self):
        self.session.call('SEARCH','search:1',SEARCH)
        original = self.grant('task:1','SEARCH','search:1',1)
        self.clock.value += 7200
        self.authorize.side_effect = None
        self.authorize.return_value = original
        with self.assertRaises(execution.IdentityReviewRequired): self.make_session().call('SEARCH','search:1',SEARCH)
        self.assertEqual(self.transport.once.call_count,1)

    def test_unknown_database_is_rejected_without_schema_modification(self):
        import sqlite3
        root = Path(self.temp.name)/'unknown'
        root.mkdir()
        path = root/'research-calls-v2.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE production (id INTEGER)')
            db.commit()
        before = path.read_bytes()
        with self.assertRaises(execution.IdentityReviewRequired):
            execution.CandidateCallJournal(root,candidate_scope='JSM_V2_ISOLATED_CANDIDATE')
        self.assertEqual(path.read_bytes(),before)


if __name__ == '__main__': unittest.main()
