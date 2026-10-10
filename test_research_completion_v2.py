"""Synthetic completion + real temporary local writer; no live queue/providers."""
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

# Existing fixtures load the candidate modules in dependency order.
import test_research_preflight_integration_v2 as fixture
import research_completion_v2 as completion
import research_pipeline as pipeline
import research_worker as worker
import runtime_ui_patch
from identity_evidence_v2 import EvidenceApproval

NOW = fixture.NOW


class ResearchCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='jsm-completion-')
        self.addCleanup(self.temp.cleanup)
        # Legacy live_observations import rewrites HTML; redirect that effect
        # as well as the app's data/upload directories to a disposable copy.
        temp_index = Path(self.temp.name) / 'index.html'
        temp_index.write_bytes((Path(__file__).parent/'index.html').read_bytes())
        with patch.dict(os.environ, {'DATA_DIR':self.temp.name}), patch.object(runtime_ui_patch,'INDEX_PATH',temp_index):
            self.app = importlib.import_module('app')
        self.addCleanup(patch.stopall)
        patch.object(self.app, 'DB_PATH', Path(self.temp.name) / 'fixture.sqlite3').start()
        patch.object(self.app.persistence, 'configured', return_value=False).start()
        patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')).start()
        patch.object(socket, 'create_connection', side_effect=AssertionError('network forbidden')).start()
        self.batch = fixture.batch_fixture.BatchAuthorityTests()
        self.batch.setUp()
        self.batch.scope = replace(self.batch.scope, platform='NetShort')
        self.batch.run = self.batch.make_run(name='run:netshort')
        row = self.batch.run.rows[0]
        self.task = dict(id='task:synthetic', status='RESEARCHING', title=row.title,
                         collection_date=self.batch.scope.collection_date.isoformat(),
                         platform='NetShort', source_type='OFFICIAL_WEB', target_key=self.batch.scope.target_key,
                         analysis_run_id=self.batch.run.run_id, source_entity_id=row.source_entity_id,
                         record_id=row.record_id, canonical_drama_id='content:1')
        self.result = {field:'Synthetic reviewed ' + field for field in completion.CORE_FIELDS}
        self.result.update({field:'' for field in completion.OPTIONAL_FIELDS})
        self.result.update(genre='现代都市', audience='女频', canonicalTitle=row.title,
                           newnessResolution='new', confidence='medium', needsGPT=False,
                           missingFields=list(completion.OPTIONAL_FIELDS), auditNotes=[],
                           sourceUrls=['https://netshort.com/episodes/drama-1'])
        self.source = dict(url=self.result['sourceUrls'][0], content='Synthetic source fixture, reviewed manually for this test only.',
                           title=row.title, official=True, score=1.0, safetyNotes=[])
        self.context = completion.CompletionContext(
            self.batch.scope, self.batch.run.run_id, row.source_entity_id, row.record_id, 'content:1',
            (self.batch.run,), {key:value for key,value in self.batch.context.items() if key != 'now'},
            (self.source,), (), {})
        self.bind_fields()
        self.record = dict(id=row.record_id, app='NetShort', title=row.title, canonicalDramaId='content:1',
                           sourceEntityId=row.source_entity_id, sourceType='OFFICIAL_WEB')
        self.records = [self.record]
        patch.object(self.app, 'public_data', side_effect=lambda:{'records':self.records}).start()

    def bind_fields(self, *, fields=None, identity=None, source=None):
        source = source or self.source
        fields = fields if fields is not None else [field for field in completion.TEXT_FIELDS if self.result[field]]
        artifact = source['content'].encode('utf-8')
        claim = dict(schemaVersion=1, canonicalDramaId=identity or self.context.canonical_drama_id,
                     url=source['url'], artifactSha256=hashlib.sha256(artifact).hexdigest(),
                     fieldValueSha256={field:completion.field_value_sha256(self.result[field]) for field in fields},
                     evidenceRef='field-review:synthetic')
        raw = json.dumps(claim, ensure_ascii=False, sort_keys=True).encode('utf-8')
        approval = EvidenceApproval(claim['evidenceRef'], hashlib.sha256(raw).hexdigest(),
                                    'decision:synthetic', NOW-timedelta(hours=1), NOW+timedelta(hours=1))
        self.context = replace(self.context, field_evidence=(completion.FieldEvidence(raw,artifact),),
                               field_approvals={approval.evidence_ref:approval})

    def assess(self, *, result=None, context=None, sources=None, task=None, now=NOW):
        return completion.assess_research_completion(
            self.result if result is None else result, task=self.task if task is None else task,
            sources=[self.source] if sources is None else sources,
            context=self.context if context is None else context, now=now)

    def write(self, *, result=None, context=None, now=NOW):
        return self.app.apply_research_result(self.task, self.result if result is None else result,
                                             validation_context=self.context if context is None else context, now=now)

    def rows(self):
        with closing(self.app.connect()) as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM drama_overrides ORDER BY drama_id')]

    def test_valid_complete_and_contract_schema_agree(self):
        contract = json.loads((Path(__file__).parent/'core_contract_v2.json').read_text(encoding='utf-8'))
        self.assertEqual(completion.CORE_FIELDS, contract['researchComplete']['coreFields'])
        self.assertEqual(completion.OPTIONAL_FIELDS, contract['researchComplete']['optionalFields'])
        self.assertEqual(pipeline._schema(), completion.research_schema())
        decision = self.assess()
        self.assertEqual(decision.status, 'COMPLETE', decision)
        self.assertEqual(decision.authoritative_run_id, self.context.origin_run_id)
        self.assertEqual(len(decision.result_sha256), 64)

    def test_every_core_field_and_placeholder_blocks_complete(self):
        for field in completion.CORE_FIELDS + ['canonicalTitle']:
            for value in ('', 'unknown', '待确认'):
                with self.subTest(field=field, value=value):
                    result = dict(self.result, **{field:value})
                    self.assertNotEqual(self.assess(result=result).status, 'COMPLETE')

    def test_schema_does_not_coerce_or_accept_unknown_fields(self):
        for changes in ({'needsGPT':'false'}, {'needsGPT':0}, {'confidence':[]}, {'confidence':'very-high'},
                        {'synopsis':[]}, {'sourceUrls':'https://netshort.com/x'}, {'auditNotes':[3]},
                        {'missingFields':['bogus']}, {'verified':True}, {'searchMeta':[]}):
            with self.subTest(changes=changes):
                self.assertEqual(self.assess(result=dict(self.result, **changes)).status,'REVIEW_REQUIRED')
        for result in ([], None, {}, {'status':'COMPLETE'}):
            # Call directly because helper's None means default.
            self.assertEqual(completion.assess_research_completion(result,task=self.task,sources=[],context=self.context,now=NOW).status,'REVIEW_REQUIRED')

    def test_low_confidence_insufficient_or_uncertain_results(self):
        for changes in ({'confidence':'low'}, {'needsGPT':True}, {'sourceUrls':[]}):
            self.assertEqual(self.assess(result=dict(self.result, **changes)).status,'NEEDS_GPT')
        self.assertEqual(self.assess(result=dict(self.result,newnessResolution='uncertain')).status,'REVIEW_REQUIRED')

    def test_optional_fields_must_declare_absence_and_support_present_values(self):
        self.assertEqual(self.assess(result=dict(self.result,missingFields=[])).status,'REVIEW_REQUIRED')
        self.result['openingSummary']='Synthetic opening description'
        self.assertEqual(self.assess().status,'REVIEW_REQUIRED')
        self.result['missingFields'].remove('openingSummary')
        self.assertEqual(self.assess().status,'NEEDS_GPT')
        self.bind_fields()
        self.assertEqual(self.assess().status,'COMPLETE')

    def test_missing_or_partial_review_cannot_complete(self):
        self.assertEqual(self.assess(context=replace(self.context,field_evidence=())).status,'NEEDS_GPT')
        self.bind_fields(fields=['canonicalTitle','synopsis'])
        self.assertEqual(self.assess().status,'NEEDS_GPT')
        self.assertEqual(self.assess(context=replace(self.context,field_approvals={})).status,'REVIEW_REQUIRED')

    def test_review_does_not_transfer_to_other_content_or_changed_values(self):
        self.bind_fields(identity='content:other')
        self.assertEqual(self.assess().status,'REVIEW_REQUIRED')
        self.bind_fields()
        self.result['synopsis'] += ' unreviewed event'
        self.assertEqual(self.assess().status,'REVIEW_REQUIRED')

    def test_artifact_and_claim_tampering_rejected(self):
        evidence = self.context.field_evidence[0]
        for changed in (replace(evidence,artifact_bytes=b'changed'),
                        replace(evidence,claim_bytes=evidence.claim_bytes.replace(b'content:1',b'content:2'))):
            self.assertEqual(self.assess(context=replace(self.context,field_evidence=(changed,))).status,'REVIEW_REQUIRED')
        self.assertEqual(self.assess(sources=[dict(self.source,content='substituted')]).status,'REVIEW_REQUIRED')

    def test_expired_revoked_or_future_field_review_rejected(self):
        ref,approval = next(iter(self.context.field_approvals.items()))
        for changed in (replace(approval,revoked=True), replace(approval,valid_until=NOW),
                        replace(approval,approved_at=NOW+timedelta(minutes=1))):
            self.assertEqual(self.assess(context=replace(self.context,field_approvals={ref:changed})).status,'REVIEW_REQUIRED')

    def test_uncaptured_private_authenticated_and_duplicate_citations_rejected(self):
        for url in ('https://netshort.com/other', 'https://127.0.0.1/x', 'https://user:pw@netshort.com/x', 'http://netshort.com/x'):
            self.assertEqual(self.assess(result=dict(self.result,sourceUrls=[url])).status,'REVIEW_REQUIRED')
        self.assertEqual(self.assess(result=dict(self.result,sourceUrls=self.result['sourceUrls']*2)).status,'REVIEW_REQUIRED')

    def test_stale_authority_or_revoked_identity_cannot_complete(self):
        later = self.batch.make_run(name='run:later',at=NOW-timedelta(minutes=1),offset=20)
        self.assertEqual(self.assess(context=replace(self.context,runs=(self.batch.run,later))).status,'REVIEW_REQUIRED')
        ref = next(ref for ref in self.context.authority_context['identity_approvals'] if ref.startswith('NetShort:'))
        ledger = self.context.authority_context['identity_approvals']
        ledger[ref] = replace(ledger[ref],revoked=True)
        self.assertEqual(self.assess().status,'REVIEW_REQUIRED')

    def test_task_binding_state_title_and_time_are_revalidated(self):
        for field in ('record_id','canonical_drama_id','platform','analysis_run_id','source_entity_id','title','status'):
            self.assertEqual(self.assess(task=dict(self.task,**{field:'different'})).status,'REVIEW_REQUIRED')
        self.assertEqual(self.assess(now=NOW.replace(tzinfo=None)).status,'REVIEW_REQUIRED')
        self.assertEqual(completion.assess_research_completion(self.result,task=self.task,sources=[self.source],context=None,now=NOW).status,'REVIEW_REQUIRED')

    def test_pipeline_missing_context_cannot_search_or_use_model(self):
        with patch.object(pipeline,'discover_sources') as search, patch.object(pipeline,'analyze_sources') as model:
            result = pipeline.research_task(self.task,now=NOW)
            self.assertEqual(result['status'],'REVIEW_REQUIRED')
            search.assert_not_called(); model.assert_not_called()

    def test_pipeline_invokes_shared_gate_on_actual_result(self):
        with patch.object(pipeline,'discover_sources',return_value=([self.source],{})), patch.object(pipeline,'analyze_sources',return_value=self.result):
            output = pipeline.research_task(self.task,validation_context=self.context,now=NOW)
            self.assertEqual(output['status'],'COMPLETE',output)
            self.result['synopsis']='changed after review'
            self.assertEqual(pipeline.research_task(self.task,validation_context=self.context,now=NOW)['status'],'REVIEW_REQUIRED')

    def test_model_citation_absence_is_preserved(self):
        raw = dict(self.result, sourceUrls=[])
        with patch.dict(os.environ,{'GEMINI_API_KEY':'synthetic-not-a-key'}), patch.object(pipeline,'gemini_request',return_value={}), patch.object(pipeline,'gemini_response_text',return_value=json.dumps(raw)):
            output = pipeline.analyze_sources(task=self.task,sources=[self.source])
        self.assertEqual(output['sourceUrls'],[])
        self.assertEqual(self.assess(result=output).status,'NEEDS_GPT')

    def test_invalid_or_duplicate_model_json_goes_to_review(self):
        for text in ('not json', '{"synopsis":"first","synopsis":"second"}'):
            with patch.object(pipeline,'discover_sources',return_value=([self.source],{})), patch.dict(os.environ,{'GEMINI_API_KEY':'synthetic-not-a-key'}), patch.object(pipeline,'gemini_request',return_value={}), patch.object(pipeline,'gemini_response_text',return_value=text):
                self.assertEqual(pipeline.research_task(self.task,validation_context=self.context,now=NOW)['status'],'REVIEW_REQUIRED')

    def test_repeated_source_sanitization_requires_review_before_model_call(self):
        with patch.object(pipeline,'discover_sources',return_value=([self.source],{'sanitizedSources':2})), patch.object(pipeline,'analyze_sources') as model:
            self.assertEqual(pipeline.research_task(self.task,validation_context=self.context,now=NOW)['status'],'REVIEW_REQUIRED')
            model.assert_not_called()

    def test_real_writer_only_updates_exact_platform_record_and_is_idempotent(self):
        self.records.append(dict(self.record,id='other-platform-id',app='ReelShort'))
        self.assertTrue(self.write()['saved'])
        first = self.rows()
        self.assertEqual([row['drama_id'] for row in first],['record:1'])
        fields = json.loads(first[0]['fields_json'])
        self.assertEqual(fields['researchCanonicalDramaId'],'content:1')
        self.assertFalse(self.write(now=NOW+timedelta(seconds=10))['saved'])
        self.assertEqual(self.rows(),first)

    def test_writer_refuses_title_history_or_other_platform_fallback(self):
        for records in ([], [dict(self.record,id='other-id')], [dict(self.record,app='ReelShort')],
                        [dict(self.record,app='ReelShort',history=[{'app':'NetShort'}])],
                        [dict(self.record,canonicalDramaId='content:other')],
                        [dict(self.record,sourceEntityId='entity:other')],
                        [dict(self.record,sourceType='SHORT_DRAMA_APP')], [self.record,self.record]):
            with self.subTest(records=records):
                self.records = records
                with self.assertRaises(completion.ResearchWritebackRejected): self.write()
                self.assertEqual(self.rows(),[])

    def test_writer_rejects_invalid_result_before_opening_database(self):
        with patch.object(self.app,'connect') as connect:
            with self.assertRaises(completion.ResearchWritebackRejected): self.write(result=dict(self.result,needsGPT=True))
            connect.assert_not_called()

    def test_remote_writer_is_explicitly_unimplemented_and_never_called(self):
        with patch.object(self.app.persistence,'configured',return_value=True), patch.object(self.app.persistence,'save_override') as remote, patch.object(self.app,'connect') as local:
            with self.assertRaisesRegex(completion.ResearchWritebackRejected,'REMOTE_WRITE_ADAPTER_NOT_IMPLEMENTED'): self.write()
            remote.assert_not_called(); local.assert_not_called()

    def test_revocation_between_precheck_and_transaction_blocks_write(self):
        original = self.app.connect
        def revoked_connect():
            ref,approval = next(iter(self.context.field_approvals.items()))
            self.context.field_approvals[ref] = replace(approval,revoked=True)
            return original()
        with patch.object(self.app,'connect',side_effect=revoked_connect):
            with self.assertRaises(completion.ResearchWritebackRejected): self.write()
        self.assertEqual(self.rows(),[])

    def test_optional_blanks_clear_stale_values_without_losing_manual_fields(self):
        with closing(self.app.connect()) as conn:
            conn.execute('INSERT INTO drama_overrides VALUES(?,?,?)',('record:1',json.dumps({'openingSummary':'stale','manualNote':'preserve'}),'old'))
            conn.commit()
        self.write()
        fields = json.loads(self.rows()[0]['fields_json'])
        self.assertEqual(fields['openingSummary'],'')
        self.assertEqual(fields['manualNote'],'preserve')

    def test_local_sql_failure_rolls_back_and_remains_technical_error(self):
        original = self.app.connect
        class BrokenConnection:
            def __init__(self): self.conn = original()
            def execute(self,sql,*args):
                if sql.startswith('INSERT INTO drama_overrides'): raise sqlite3.OperationalError('synthetic disk failure')
                return self.conn.execute(sql,*args)
            def rollback(self): return self.conn.rollback()
            def close(self): return self.conn.close()
        with patch.object(self.app,'connect',side_effect=BrokenConnection):
            with self.assertRaises(sqlite3.OperationalError): self.write()
        self.assertEqual(self.rows(),[])

    def test_worker_business_refusal_goes_to_review_without_complete(self):
        output = dict(status='COMPLETE',research=self.result,sources=[],confidence='medium',missingFields=[],error='')
        with patch.object(worker.persistence,'claim_research_tasks',return_value=[self.task]), patch.object(worker.persistence,'update_research_task') as update, patch.object(worker,'research_task',return_value=output), patch.object(worker.time,'sleep'), patch('builtins.print'):
            worker._run(apply_research=Mock(side_effect=completion.ResearchWritebackRejected('synthetic refusal')),max_tasks=1)
        self.assertEqual(update.call_count,1)
        self.assertEqual(update.call_args.kwargs['status'],'REVIEW_REQUIRED')


if __name__ == '__main__':
    unittest.main()
