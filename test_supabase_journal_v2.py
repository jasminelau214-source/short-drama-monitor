"""Offline RPC client/protocol checks; no cloud traffic or real credentials."""
from dataclasses import replace
from io import BytesIO
import json
import unittest
import urllib.error
from unittest.mock import Mock,MagicMock,patch

import test_research_execution_v2 as execution_fixture
import supabase_journal_v2 as supabase
from research_execution_v2 import ResearchExecutionStopped,IdentityReviewRequired

INSTANCE='11111111-1111-4111-8111-111111111111'
OWNER='22222222-2222-4222-8222-222222222222'


class SupabaseJournalClientTests(unittest.TestCase):
    def setUp(self):
        self.config=supabase.SupabaseJournalConfig('s'*20,INSTANCE,'sb_publishable_synthetic','synthetic.worker.jwt')
        self.transport=Mock()
        self.transport.post.return_value={'ok':True,'data':{'scope':supabase.SCOPE,'protocol':1,'instance_id':INSTANCE}}
        self.journal=supabase.SupabaseCallJournal(self.config,transport=self.transport)
        self.transport.reset_mock()

    def test_explicit_config_refuses_production_and_broad_secret_key_before_network(self):
        for fields in ({'project_ref':supabase.PRODUCTION_PROJECT},{'project_ref':'https://example.test'},
                       {'publishable_key':'sb_secret_synthetic'},{'worker_access_token':''},{'instance_id':'not-a-uuid'}):
            with self.assertRaises(IdentityReviewRequired):
                supabase.SupabaseCallJournal(replace(self.config,**fields),transport=self.transport)
        self.transport.post.assert_not_called()

    def test_handshake_binds_exact_instance_scope_and_protocol(self):
        for change in ({'scope':'PRODUCTION'},{'instance_id':OWNER},{'protocol':True},{'protocol':2}):
            info=dict(scope=supabase.SCOPE,protocol=1,instance_id=INSTANCE);info.update(change)
            self.transport.post.return_value={'ok':True,'data':info}
            with self.assertRaises(IdentityReviewRequired): supabase.SupabaseCallJournal(self.config,transport=self.transport)

    def test_start_and_cached_response_have_exact_protocol(self):
        self.transport.post.return_value={'ok':True,'data':{'cached':None,'owner':OWNER,'remaining':299.5}}
        self.assertEqual(self.journal.start('task:1','search:1','SEARCH','a'*64,0),(None,OWNER,299.5))
        self.assertEqual(self.transport.post.call_args.args[1:],('start',dict(task_id='task:1',call_id='search:1',stage='SEARCH',binding='a'*64)))
        self.transport.post.return_value={'ok':True,'data':{'cached':{'results':[]},'owner':None,'remaining':0}}
        self.assertEqual(self.journal.start('task:1','search:1','SEARCH','a'*64,0),({'results':[]},None,0))

    def test_malformed_server_responses_fail_closed(self):
        cases=[([],lambda:self.journal.inspect('t','c')),
            ({'ok':True,'data':{'owner':OWNER,'cached':None,'remaining':True}},lambda:self.journal.start('t','c','SEARCH','a'*64,0)),
            ({'ok':True,'data':{'owner':None,'cached':{},'remaining':False}},lambda:self.journal.start('t','c','SEARCH','a'*64,0)),
            ({'ok':False,'status':[],'reason':'unsafe'},lambda:self.journal.inspect('t','c')),
            ({'ok':True,'data':{'state':[]}},lambda:self.journal.finish('t','c',OWNER,elapsed=1,kind='success',now=0,response={})),
            ({'ok':True,'data':{'charged':1}},lambda:self.journal.charge_wait('t',1)),
            ({'ok':True,'data':{'resumed':1}},lambda:self.journal.resume('t','c','a'*64))]
        for response,call in cases:
            self.transport.post.return_value=response
            with self.assertRaises(ResearchExecutionStopped):call()

    def test_inspect_never_accepts_or_exposes_owner_token(self):
        row=dict(task_id='t',call_id='c',stage='SEARCH',binding='a'*64,attempts=1,state='INFLIGHT',retry_at=0,response=None)
        self.transport.post.return_value={'ok':True,'data':row}
        self.assertEqual(self.journal.inspect('t','c'),row)
        self.transport.post.return_value={'ok':True,'data':dict(row,owner=OWNER)}
        with self.assertRaises(ResearchExecutionStopped):self.journal.inspect('t','c')

    def test_server_quota_and_business_refusals_preserve_status(self):
        for status in ('DEFERRED_FREE_QUOTA','FAILED','REVIEW_REQUIRED'):
            self.transport.post.return_value={'ok':False,'status':status,'reason':'CALL_NOT_READY_OR_OUTCOME_UNCERTAIN'}
            with self.assertRaises(ResearchExecutionStopped) as exc:self.journal.start('t','c','SEARCH','a'*64,0)
            self.assertEqual(exc.exception.status,status)

    def test_https_transport_uses_fixed_endpoint_headers_tls_and_no_redirect(self):
        opener=MagicMock()
        response=Mock();response.read.return_value=b'{"ok":true,"data":null}'
        opener.open.return_value.__enter__.return_value=response
        with patch.object(supabase.urllib.request,'build_opener',return_value=opener):
            self.assertEqual(supabase.SupabaseRpcTransport().post(self.config,'inspect',{'task_id':'t','call_id':'c'}),{'ok':True,'data':None})
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'https://'+'s'*20+'.supabase.co/rest/v1/rpc/jsm_v2_journal')
        self.assertEqual(request.get_header('Authorization'),'Bearer synthetic.worker.jwt')
        self.assertEqual(request.get_header('Apikey'),'sb_publishable_synthetic')
        self.assertNotIn(b'synthetic.worker.jwt',request.data)
        self.assertIsNone(supabase._NoRedirect().redirect_request(None,None,302,'',{},'https://elsewhere.test'))

    def test_unknown_rpc_commit_is_not_automatically_retried_or_logged(self):
        opener=Mock();opener.open.side_effect=urllib.error.URLError('private-token-must-not-leak')
        with patch.object(supabase.urllib.request,'build_opener',return_value=opener):
            with self.assertRaises(ResearchExecutionStopped) as exc:
                supabase.SupabaseRpcTransport().post(self.config,'start',{})
        self.assertEqual(opener.open.call_count,1)
        self.assertEqual(str(exc.exception),'SUPABASE_RPC_OUTCOME_UNCERTAIN')
        self.assertEqual(exc.exception.status,'REVIEW_REQUIRED')

    def test_http_auth_failure_sanitizes_error_and_does_not_retry(self):
        opener=Mock();opener.open.side_effect=urllib.error.HTTPError('https://example.test',403,'private',{},BytesIO(b'private'))
        with patch.object(supabase.urllib.request,'build_opener',return_value=opener):
            with self.assertRaises(ResearchExecutionStopped) as exc:supabase.SupabaseRpcTransport().post(self.config,'info',{})
        self.assertEqual(str(exc.exception),'SUPABASE_RPC_HTTP_403')
        self.assertEqual(opener.open.call_count,1)

    def test_actual_execution_session_uses_typed_remote_adapter_without_local_fallback(self):
        fixture=execution_fixture.ResearchExecutionTests();fixture.setUp()
        try:
            self.transport.post.side_effect=[{'ok':True,'data':None},ResearchExecutionStopped('REVIEW_REQUIRED','SUPABASE_RPC_OUTCOME_UNCERTAIN')]
            session=fixture.make_session(journal=self.journal)
            with self.assertRaises(ResearchExecutionStopped):session.call('SEARCH','search:1',execution_fixture.SEARCH)
            fixture.transport.once.assert_not_called()
            self.assertEqual(self.transport.post.call_count,2)
        finally:fixture.doCleanups()


if __name__=='__main__':unittest.main()
