"""Explicit isolated Supabase call-journal adapter; never activates from env.

Uses a publishable API key plus a narrowly allowlisted Auth worker session,
not service_role. Secrets stay in request headers and are never persisted.
"""
from dataclasses import dataclass
import json
import math
import re
import ssl
import urllib.error
import urllib.request
import uuid

from research_execution_v2 import ResearchCallJournal, ResearchExecutionStopped, IdentityReviewRequired, _json

PRODUCTION_PROJECT = 'zxxuuhpqcvnohtelqgzp'
SCOPE = 'JSM_V2_ISOLATED_STAGING_V1'
STATES = {'READY','INFLIGHT','RETRY_WAIT','SUCCEEDED','DEFERRED_FREE_QUOTA','FAILED','REVIEW_REQUIRED'}


@dataclass(frozen=True)
class SupabaseJournalConfig:
    project_ref: str
    instance_id: str
    publishable_key: str
    worker_access_token: str


def _config(config):
    if (not isinstance(config, SupabaseJournalConfig) or not isinstance(config.project_ref,str)
            or not re.fullmatch('[a-z0-9]{20}',config.project_ref) or config.project_ref == PRODUCTION_PROJECT
            or not isinstance(config.publishable_key,str) or not config.publishable_key.startswith('sb_publishable_')
            or not isinstance(config.worker_access_token,str) or len(config.worker_access_token.split('.')) != 3
            or any(c.isspace() for c in config.publishable_key+config.worker_access_token)):
        raise IdentityReviewRequired('EXPLICIT_NONPRODUCTION_SUPABASE_WORKER_CONFIG_REQUIRED')
    try:
        if str(uuid.UUID(config.instance_id)) != config.instance_id or uuid.UUID(config.instance_id).int == 0:
            raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise IdentityReviewRequired('PINNED_STAGING_INSTANCE_ID_REQUIRED') from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


class SupabaseRpcTransport:
    def post(self, config, operation, payload):
        _config(config)
        url = f'https://{config.project_ref}.supabase.co/rest/v1/rpc/jsm_v2_journal'
        raw = _json({'p_operation':operation,'p_payload':payload}).encode()
        if len(raw) > 9*1024*1024:
            raise ResearchExecutionStopped('REVIEW_REQUIRED','SUPABASE_REQUEST_TOO_LARGE')
        req = urllib.request.Request(url,data=raw,method='POST',headers={
            'Content-Type':'application/json','apikey':config.publishable_key,
            'Authorization':'Bearer '+config.worker_access_token})
        tls = ssl.create_default_context()
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            # One request only. An uncertain committed mutation is not retried.
            opener = urllib.request.build_opener(_NoRedirect(),urllib.request.HTTPSHandler(context=tls))
            with opener.open(req,timeout=20) as response:
                content = response.read(9*1024*1024+1)
            if len(content) > 9*1024*1024: raise ValueError()
            return json.loads(content)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise ResearchExecutionStopped('REVIEW_REQUIRED',f'SUPABASE_RPC_HTTP_{code}') from None
        except (urllib.error.URLError,TimeoutError,ConnectionError,OSError):
            raise ResearchExecutionStopped('REVIEW_REQUIRED','SUPABASE_RPC_OUTCOME_UNCERTAIN') from None
        except (ValueError,UnicodeError):
            raise ResearchExecutionStopped('REVIEW_REQUIRED','SUPABASE_RPC_RESPONSE_INVALID') from None


class SupabaseCallJournal(ResearchCallJournal):
    def __init__(self, config, *, transport=None):
        _config(config)
        self._config = config
        self._transport = transport or SupabaseRpcTransport()
        info = self._rpc('info',{})
        if (type(info) is not dict or info.get('scope') != SCOPE or info.get('protocol') != 1
                or type(info.get('protocol')) is not int or info.get('instance_id') != config.instance_id):
            raise IdentityReviewRequired('SUPABASE_STAGING_HANDSHAKE_MISMATCH')

    def _rpc(self, operation, payload):
        result = self._transport.post(self._config,operation,payload)
        if type(result) is not dict or type(result.get('ok')) is not bool:
            raise ResearchExecutionStopped('REVIEW_REQUIRED','SUPABASE_RPC_PROTOCOL_INVALID')
        if not result['ok']:
            reason, status = result.get('reason'), result.get('status')
            if not isinstance(status,str) or status not in {'FAILED','REVIEW_REQUIRED','DEFERRED_FREE_QUOTA'} or not isinstance(reason,str) or not re.fullmatch('[A-Z0-9_]{1,120}',reason):
                raise ResearchExecutionStopped('REVIEW_REQUIRED','SUPABASE_RPC_REFUSAL_INVALID')
            raise ResearchExecutionStopped(status,reason)
        if set(result) != {'ok','data'}:
            raise ResearchExecutionStopped('REVIEW_REQUIRED','SUPABASE_RPC_PROTOCOL_INVALID')
        return result['data']

    @staticmethod
    def _number(value, *, minimum=0, maximum=None):
        return type(value) in (int,float) and math.isfinite(value) and value >= minimum and (maximum is None or value <= maximum)

    def inspect(self, task_id, call_id):
        row = self._rpc('inspect',dict(task_id=task_id,call_id=call_id))
        if row is None: return None
        if (type(row) is not dict or set(row) != {'task_id','call_id','stage','binding','attempts','state','retry_at','response'}
                or row['task_id'] != task_id or row['call_id'] != call_id or not isinstance(row['stage'],str) or row['stage'] not in {'SEARCH','ANALYZE'}
                or type(row['attempts']) is not int or not 0 <= row['attempts'] <= 3 or not isinstance(row['state'],str) or row['state'] not in STATES
                or not isinstance(row['binding'],str) or not re.fullmatch('[0-9a-f]{64}',row['binding'])
                or not self._number(row['retry_at']) or (row['response'] is not None and type(row['response']) is not dict)):
            raise ResearchExecutionStopped('REVIEW_REQUIRED','SUPABASE_CHECKPOINT_INVALID')
        return row

    def start(self, task_id, call_id, stage, binding, now):
        data = self._rpc('start',dict(task_id=task_id,call_id=call_id,stage=stage,binding=binding))
        if type(data) is not dict or set(data) != {'cached','owner','remaining'}:
            raise ResearchExecutionStopped('REVIEW_REQUIRED','SUPABASE_RESERVATION_INVALID')
        if data['owner'] is None:
            if type(data['cached']) is not dict or not self._number(data['remaining'],maximum=0):
                raise ResearchExecutionStopped('REVIEW_REQUIRED','SUPABASE_CACHED_RESPONSE_INVALID')
        else:
            try: valid_owner = str(uuid.UUID(data['owner'])) == data['owner']
            except (ValueError,TypeError,AttributeError): valid_owner = False
            if not valid_owner or data['cached'] is not None or not self._number(data['remaining'],minimum=0.001,maximum=300):
                raise ResearchExecutionStopped('REVIEW_REQUIRED','SUPABASE_RESERVATION_INVALID')
        return data['cached'],data['owner'],data['remaining']

    def finish(self, task_id, call_id, owner, *, elapsed, kind, now, response=None, delay=0):
        data = self._rpc('finish',dict(task_id=task_id,call_id=call_id,owner=owner,
            elapsed=elapsed,kind=kind,response=response,delay=delay))
        if type(data) is not dict or set(data) != {'state'} or not isinstance(data['state'],str) or data['state'] not in STATES-{'READY','INFLIGHT'}:
            raise ResearchExecutionStopped('REVIEW_REQUIRED','SUPABASE_OUTCOME_INVALID')
        return data['state']

    def charge_wait(self, task_id, seconds):
        data = self._rpc('charge_wait',dict(task_id=task_id,seconds=seconds,event_id=str(uuid.uuid4())))
        if type(data) is not dict or set(data) != {'charged'} or data['charged'] is not True:
            raise ResearchExecutionStopped('REVIEW_REQUIRED','SUPABASE_WAIT_CHECKPOINT_INVALID')

    def resume(self, task_id, call_id, binding):
        data = self._rpc('resume',dict(task_id=task_id,call_id=call_id,binding=binding))
        if type(data) is not dict or set(data) != {'resumed'} or data['resumed'] is not True:
            raise ResearchExecutionStopped('REVIEW_REQUIRED','SUPABASE_QUOTA_RESUME_INVALID')
