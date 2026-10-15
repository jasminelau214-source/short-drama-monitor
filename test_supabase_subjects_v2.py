"""Synthetic protected snapshots and codec/adapter checks; no live provider or cloud."""
from dataclasses import replace
from datetime import datetime,timezone,timedelta
import hashlib,json,unittest,uuid
from unittest.mock import Mock,patch
import test_research_completion_v2 as fixture
import protected_context_codec_v2 as codec
import supabase_subjects_v2 as subjects
from research_completion_v2 import validate_completion_context,ResearchWritebackRejected
from drama_identity_v2 import IdentityReviewRequired
from supabase_journal_v2 import SupabaseJournalConfig,SCOPE

def fresh_fixture():
    now=datetime.now(timezone.utc)
    f=fixture.ResearchCompletionTests()
    with patch.object(fixture,'NOW',now),patch.object(fixture.fixture.batch_fixture,'NOW',now):
        f.setUp()
        f.batch.scope=replace(f.batch.scope,collection_date=now.astimezone(timezone(timedelta(hours=8))).date())
        f.batch.run=f.batch.make_run(name='run:subject-current')
        row=f.batch.run.rows[0]
        f.task.update(collection_date=f.batch.scope.collection_date.isoformat(),analysis_run_id=f.batch.run.run_id,
                      source_entity_id=row.source_entity_id,record_id=row.record_id,title=row.title,rank=row.rank)
        f.context=replace(f.context,scope=f.batch.scope,origin_run_id=f.batch.run.run_id,runs=(f.batch.run,),
            authority_context={k:v for k,v in f.batch.context.items() if k!='now'})
        f.bind_fields()
    return f,now

def snapshot(f,now):
    raw=codec.encode_context(f.context)
    decision=validate_completion_context(f.task,f.context,now=now)
    return dict(context_text=raw,context_sha=hashlib.sha256(raw.encode()).hexdigest(),
        authority_sha=decision.snapshot_sha256,authority_revision=0,context_revision=0)

class CodecTests(unittest.TestCase):
    def setUp(self):
        self.f,self.now=fresh_fixture();self.addCleanup(self.f.doCleanups)

    def test_roundtrip_preserves_real_contract_and_binary_evidence(self):
        ctx=codec.decode_context(codec.encode_context(self.f.context))
        self.assertEqual(codec.encode_context(ctx),codec.encode_context(self.f.context))
        self.assertEqual(self.f.assess(context=ctx,now=self.now).status,'COMPLETE')
        self.assertIsInstance(ctx.field_evidence[0].artifact_bytes,bytes)

    def test_arbitrary_tags_extra_fields_and_duplicate_json_are_refused(self):
        for text in ('{"version":1,"context":{"type":"module","value":"os"}}',
                     '{"version":1,"version":1,"context":null}',
                     '{"version":true,"context":null}',
                     '{"version":1,"context":null,"approved":true}'):
            with self.subTest(text=text),self.assertRaises(IdentityReviewRequired):codec.decode_context(text)

    def test_mapping_duplicates_aware_time_and_unknown_object_refused(self):
        raw=json.loads(codec.encode_context(self.f.context))
        fields=raw['context']['fields']
        mapping=fields['field_approvals']
        mapping['items'].append(mapping['items'][0])
        with self.assertRaises(IdentityReviewRequired):codec.decode_context(json.dumps(raw))
        with self.assertRaises(ValueError):codec._convert(datetime(2026,1,1))
        with self.assertRaises(ValueError):codec._convert(object())

    def test_decode_does_not_authorize_expired_or_changed_snapshots(self):
        ctx=codec.decode_context(codec.encode_context(replace(self.f.context,origin_run_id='wrong')))
        with self.assertRaises(IdentityReviewRequired):validate_completion_context(self.f.task,ctx,now=self.now)

class SubjectAdapterTests(unittest.TestCase):
    def setUp(self):
        self.f,self.now=fresh_fixture();self.addCleanup(self.f.doCleanups)
        self.sid=str(uuid.uuid4());self.owner=str(uuid.uuid4())
        self.config=SupabaseJournalConfig('s'*20,str(uuid.uuid4()),'sb_publishable_synthetic','synthetic.worker.jwt')
        self.task=dict(self.f.task,id=self.sid)
        self.snap=snapshot(self.f,self.now)
        self.calls=[]
        def post(config,op,payload):
            self.calls.append((op,payload))
            if op=='info':data=dict(scope=SCOPE,instance_id=self.config.instance_id,protocol=1,kind='JSM_V2_SUBJECT_QUEUE')
            elif op=='claim':data=dict(task=self.task,owner=self.owner,request_id=payload['request_id'])
            elif op=='context':data=dict(self.snap)
            elif op=='complete':data=dict(status='COMPLETE',result_sha=payload['result_sha'])
            elif op=='inspect':data=dict(status='COMPLETE',result_sha='a'*64)
            elif op=='status':data=dict(status=payload['status'])
            elif op=='register':data=dict(subject_id=self.sid,created=True)
            return dict(ok=True,data=data)
        self.transport=Mock();self.transport.post.side_effect=post
        self.store=subjects.SupabaseSubjectStore(self.config,transport=self.transport,now=lambda:self.now)

    def test_claim_capability_stays_outside_task_json(self):
        task=self.store.claim()[0]
        self.assertNotIn('owner',task);self.assertEqual(task,self.task)
        self.assertEqual(self.store.reconcile_claim(self.store.last_claim_request),[self.task])

    def test_fresh_context_and_valid_completion_are_required(self):
        task=self.store.claim()[0]
        ctx=self.store.context(task,'BEFORE_WRITEBACK')
        self.assertEqual(self.store.apply_research(task,self.f.result,validation_context=ctx)['status'],'COMPLETE')
        self.assertEqual(self.store.update(task['id'],status='COMPLETE')['status'],'COMPLETE')
        with self.assertRaises(ResearchWritebackRejected):
            self.store.apply_research(task,self.f.result,validation_context=codec.decode_context(codec.encode_context(ctx)))

    def test_context_digest_and_authority_digest_tampering_refused(self):
        task=self.store.claim()[0]
        for key in ('context_sha','authority_sha'):
            old=self.snap[key];self.snap[key]='b'*64
            with self.subTest(key=key),self.assertRaises(IdentityReviewRequired):self.store.context(task,'BEFORE_RESEARCH')
            self.snap[key]=old

    def test_unclaimed_or_task_binding_changes_refused(self):
        with self.assertRaises(IdentityReviewRequired):self.store.context(self.task,'BEFORE_RESEARCH')
        task=self.store.claim()[0]
        with self.assertRaises(IdentityReviewRequired):self.store.context(dict(task,platform='DramaBox'),'BEFORE_RESEARCH')

    def test_result_mutation_and_direct_complete_cannot_bypass_checks(self):
        task=self.store.claim()[0];ctx=self.store.context(task,'BEFORE_WRITEBACK')
        with self.assertRaises(ResearchWritebackRejected):self.store.apply_research(task,dict(self.f.result,synopsis='unreviewed'),validation_context=ctx)
        self.assertFalse(any(op=='complete' for op,_ in self.calls))
        with self.assertRaises(IdentityReviewRequired):self.store.update(task['id'],status='PENDING')

if __name__=='__main__':unittest.main()
