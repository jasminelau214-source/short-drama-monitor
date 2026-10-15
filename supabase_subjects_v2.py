"""Explicit protected V2 queue/context/atomic-publication adapter.

Never installed from env or request JSON, never claims the legacy queue.
Provider/account authorization is separate and remains required by the Worker.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import threading
import uuid

from supabase_journal_v2 import SupabaseRpcTransport, _config, SCOPE
from research_execution_v2 import ResearchExecutionStopped
from research_completion_v2 import (CompletionContext, assess_research_completion,
    validate_completion_context, ResearchWritebackRejected, RESULT_FIELDS)
from protected_context_codec_v2 import decode_context
from drama_identity_v2 import IdentityReviewRequired


class SubjectRpcTransport(SupabaseRpcTransport):
    rpc_name = 'jsm_v2_subjects'


class SupabaseSubjectStore:
    def __init__(self, config, *, transport=None, now=None):
        _config(config)
        self.config = config
        self.transport = transport or SubjectRpcTransport()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._owners = {}
        self._contexts = {}
        self.last_claim_request = None
        info = self._rpc('info', {})
        if (type(info) is not dict or set(info) != {'scope','instance_id','protocol','kind'}
            or info['scope'] != SCOPE or info['instance_id'] != config.instance_id
            or type(info['protocol']) is not int or info['protocol'] != 1
            or info['kind'] != 'JSM_V2_SUBJECT_QUEUE'):
            raise IdentityReviewRequired('SUBJECT_STAGING_HANDSHAKE_MISMATCH')

    def _rpc(self, operation, payload):
        value = self.transport.post(self.config, operation, payload)
        if type(value) is not dict or type(value.get('ok')) is not bool:
            raise IdentityReviewRequired('SUBJECT_RPC_PROTOCOL_INVALID')
        if value['ok']:
            if set(value) != {'ok','data'}: raise IdentityReviewRequired('SUBJECT_RPC_PROTOCOL_INVALID')
            return value['data']
        if (type(value.get('status')) is not str or value['status'] not in {'FAILED','REVIEW_REQUIRED','DEFERRED_FREE_QUOTA'}
            or type(value.get('reason')) is not str or not re.fullmatch('[A-Z0-9_]{1,120}',value['reason'])):
            raise IdentityReviewRequired('SUBJECT_RPC_REFUSAL_INVALID')
        raise IdentityReviewRequired(value['reason'])

    @staticmethod
    def _uuid(value):
        try: valid = type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0
        except ValueError: valid = False
        if not valid: raise IdentityReviewRequired('EXACT_SUBJECT_CAPABILITY_REQUIRED')
        return value

    def register(self, record_id):
        data = self._rpc('register', {'record_id':record_id})
        if type(data) is not dict or set(data) != {'subject_id','created'} or type(data['created']) is not bool:
            raise IdentityReviewRequired('SUBJECT_REGISTRATION_INVALID')
        self._uuid(data['subject_id'])
        return data

    def claim(self, count=1):
        if type(count) is not int or count != 1: raise IdentityReviewRequired('SINGLE_SUBJECT_CLAIM_REQUIRED')
        request = str(uuid.uuid4())
        self.last_claim_request = request
        return self.reconcile_claim(request)

    def reconcile_claim(self, request):
        """Explicit known request reconciliation; transport never retries itself."""
        self._uuid(request)
        data = self._rpc('claim', {'request_id':request})
        if type(data) is not dict or set(data) != {'task','owner','request_id'} or data['request_id'] != request:
            raise IdentityReviewRequired('SUBJECT_CLAIM_INVALID')
        if data['task'] is None:
            if data['owner'] is not None: raise IdentityReviewRequired('SUBJECT_CLAIM_INVALID')
            return []
        task = data['task']
        keys = {'id','status','collection_date','platform','source_type','target_key',
            'analysis_run_id','source_entity_id','record_id','canonical_drama_id','title','rank'}
        if (type(task) is not dict or set(task) != keys or task.get('status') != 'RESEARCHING'
            or any(type(task[k]) is not str or not task[k] for k in keys-{'rank'})
            or type(task['rank']) is not int or not 1<=task['rank']<=10):
            raise IdentityReviewRequired('SUBJECT_CLAIM_TASK_INVALID')
        subject, owner = self._uuid(task.get('id')), self._uuid(data['owner'])
        with self._lock: self._owners[subject] = owner
        # The capability stays in trusted adapter memory, outside model/task JSON.
        return [task]

    def _bound(self, subject):
        self._uuid(subject)
        with self._lock: owner = self._owners.get(subject)
        if owner is None: raise IdentityReviewRequired('SUBJECT_NOT_CLAIMED_BY_THIS_ADAPTER')
        return {'subject_id':subject,'owner':owner}

    def context(self, task, stage, outcome=None):
        if stage not in {'BEFORE_RESEARCH','AFTER_RESEARCH','BEFORE_WRITEBACK'}:
            raise IdentityReviewRequired('SUBJECT_CONTEXT_STAGE_REFUSED')
        if type(task) is not dict: raise IdentityReviewRequired('SUBJECT_TASK_INVALID')
        data = self._rpc('context', self._bound(task.get('id')))
        if (type(data) is not dict or set(data) != {'context_text','context_sha','authority_revision','context_revision','authority_sha'}
            or type(data['context_text']) is not str or type(data['context_sha']) is not str
            or hashlib.sha256(data['context_text'].encode()).hexdigest() != data['context_sha']
            or any(type(data[key]) is not int or data[key]<0 for key in ('authority_revision','context_revision'))):
            raise IdentityReviewRequired('PROTECTED_SUBJECT_CONTEXT_INVALID')
        context = decode_context(data['context_text'])
        decision = validate_completion_context(task, context, now=self.now())
        if decision.snapshot_sha256 != data['authority_sha']:
            raise IdentityReviewRequired('SUBJECT_AUTHORITY_SNAPSHOT_MISMATCH')
        with self._lock: self._contexts[task['id']] = (context,dict(data))
        return context

    def apply_research(self, task, result, *, validation_context=None):
        if type(task) is not dict: raise ResearchWritebackRejected('SUBJECT_TASK_INVALID')
        with self._lock: reviewed = self._contexts.get(task.get('id'))
        if reviewed is None or validation_context is not reviewed[0]:
            raise ResearchWritebackRejected('FRESH_ADAPTER_WRITE_CONTEXT_REQUIRED')
        context, snapshot = reviewed
        decision = assess_research_completion(result, task=task, sources=context.captured_sources,
            context=context, now=self.now())
        if decision.status != 'COMPLETE': raise ResearchWritebackRejected(decision.reason)
        payload = json.dumps({key:result[key] for key in sorted(RESULT_FIELDS)},
            ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        request = dict(self._bound(task['id']),record_id=context.record_id,
            context_sha=snapshot['context_sha'],authority_revision=snapshot['authority_revision'],
            context_revision=snapshot['context_revision'],payload_text=payload,result_sha=decision.result_sha256)
        try: data = self._rpc('complete',request)
        except (ResearchExecutionStopped,IdentityReviewRequired):
            raise ResearchWritebackRejected('ATOMIC_PUBLICATION_REFUSED_OR_REQUIRES_RECONCILIATION') from None
        if type(data) is not dict or data != {'status':'COMPLETE','result_sha':decision.result_sha256}:
            raise ResearchWritebackRejected('ATOMIC_PUBLICATION_RESPONSE_INVALID')
        return data

    def update(self, subject, *, status, **unused_outcome):
        # A second Worker update may confirm COMPLETE; it cannot create it.
        if status == 'COMPLETE':
            data = self._rpc('inspect',self._bound(subject))
            if type(data) is not dict or set(data) != {'status','result_sha'} or data['status'] != 'COMPLETE' or not re.fullmatch('[0-9a-f]{64}',str(data['result_sha'])):
                raise IdentityReviewRequired('ATOMIC_COMPLETION_NOT_CONFIRMED')
            return data
        if status not in {'DEFERRED_FREE_QUOTA','REVIEW_REQUIRED','NEEDS_GPT','FAILED'}:
            raise IdentityReviewRequired('SUBJECT_TRANSITION_REFUSED')
        data = self._rpc('status',dict(self._bound(subject),status=status))
        if data != {'status':status}: raise IdentityReviewRequired('SUBJECT_STATUS_RESPONSE_INVALID')
        return data

    def handoff(self, *, execution):
        from research_worker import ResearchHandoff
        if not callable(execution): raise IdentityReviewRequired('PROTECTED_PROVIDER_EXECUTION_REQUIRED')
        return ResearchHandoff(self.claim,self.context,self.update,execution)
