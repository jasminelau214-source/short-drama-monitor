"""Explicit candidate provider execution. No env-based activation or legacy queue.

The journal is durable on an explicitly selected local disk. Cloud queue/CAS,
protected approval loaders and deployment are separate integration work.
"""
from contextlib import contextmanager
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid

from drama_identity_v2 import IdentityReviewRequired
from research_eligibility_v2 import FreeProviderApproval, ResearchPreflightDecision

V2_PLATFORMS = frozenset({'DramaBox', 'FlexTV', 'GoodShort', 'MoboReels', 'NetShort', 'ReelShort'})
SEARCH_ENDPOINT = 'https://api.tavily.com/search'
MODEL_ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


class ResearchExecutionStopped(RuntimeError):
    def __init__(self, status, reason):
        self.status = status
        super().__init__(reason)


class ProviderFault(RuntimeError):
    def __init__(self, kind, code, *, retry_after=0, rate_limited=False):
        self.kind, self.retry_after, self.rate_limited = kind, retry_after, rate_limited
        super().__init__(code)  # Never retain provider error text/headers/credentials.


@dataclass(frozen=True)
class CallAuthorization:
    """Load from protected code for EVERY attempt, never from task/model JSON.

    account_artifact binds the current account tier/no-billing review, in
    addition to the reviewed public product policy. A key alone is insufficient.
    """
    task_id: str
    preflight: ResearchPreflightDecision
    provider: FreeProviderApproval
    credential_sha256: str
    product_id: str
    account_review_ref: str
    account_artifact_sha256: str
    account_checked_at: datetime
    account_valid_until: datetime
    billing_disabled: bool
    quota_available: bool


def _authorize(value, *, task_id, stage, credential, model, now):
    if not isinstance(value, CallAuthorization) or value.task_id != task_id:
        raise IdentityReviewRequired('PROTECTED_CALL_AUTHORIZATION_REQUIRED')
    pre, provider = value.preflight, value.provider
    if (not isinstance(pre, ResearchPreflightDecision) or pre.authorized is not True
            or pre.eligibility_revalidated is not True or pre.external_call_performed is not False
            or pre.spend_incurred is not False or not pre.authoritative_run_id
            or not isinstance(pre.authority_snapshot_sha256, str)
            or not re.fullmatch('[0-9a-f]{64}', pre.authority_snapshot_sha256)):
        raise IdentityReviewRequired('CURRENT_OBSERVATION_NOT_AUTHORIZED')
    if (not isinstance(provider, FreeProviderApproval) or pre.provider_id != provider.provider_id
            or provider.provider_id != ('tavily' if stage == 'SEARCH' else 'gemini')
            or provider.free_tier_verified is not True or provider.paid_fallback is not False
            or provider.revoked is not False or not provider.review_ref or not provider.approval_id
            or not isinstance(provider.source_url, str) or not provider.source_url.startswith('https://')
            or not isinstance(provider.artifact_sha256, str) or not re.fullmatch('[0-9a-f]{64}', provider.artifact_sha256)
            or not _current(provider.checked_at, provider.valid_until, now)):
        raise IdentityReviewRequired('VERIFIED_FREE_PRODUCT_REQUIRED')
    if (value.billing_disabled is not True or not isinstance(credential, str) or not credential
            or value.credential_sha256 != _sha(credential.encode())
            or value.product_id != ('search-basic' if stage == 'SEARCH' else model)
            or not isinstance(value.account_review_ref, str) or not value.account_review_ref.strip()
            or not isinstance(value.account_artifact_sha256, str)
            or not re.fullmatch('[0-9a-f]{64}', value.account_artifact_sha256)
            or not _current(value.account_checked_at, value.account_valid_until, now)
            or type(value.quota_available) is not bool):
        raise IdentityReviewRequired('CURRENT_FREE_ACCOUNT_AND_EXACT_CREDENTIAL_REQUIRED')
    if not value.quota_available:
        raise ResearchExecutionStopped('DEFERRED_FREE_QUOTA', 'FREE_QUOTA_UNAVAILABLE')
    return _sha(_json([task_id, stage, pre.authoritative_run_id, pre.authority_snapshot_sha256,
                      provider.provider_id, value.credential_sha256, value.product_id]).encode())


def _current(start, end, now):
    return all(isinstance(x, datetime) and x.utcoffset() is not None for x in (start, end, now)) and start <= now < end


class AdaptiveCallLimiter:
    """One shared limiter per candidate Worker; 429 lowers future calls to one."""
    def __init__(self):
        self._condition = threading.Condition()
        self.active, self.limit = 0, 2

    @contextmanager
    def slot(self):
        with self._condition:
            self._condition.wait_for(lambda: self.active < self.limit)
            self.active += 1
        try:
            yield
        finally:
            with self._condition:
                self.active -= 1
                self._condition.notify_all()

    def throttled(self):
        with self._condition:
            self.limit = 1
            self._condition.notify_all()


class ResearchCallJournal(ABC):
    """Trusted code adapter contract; request/model JSON cannot implement it."""
    @abstractmethod
    def inspect(self, task_id, call_id): pass
    @abstractmethod
    def start(self, task_id, call_id, stage, binding, now): pass
    @abstractmethod
    def finish(self, task_id, call_id, owner, *, elapsed, kind, now, response=None, delay=0): pass
    @abstractmethod
    def charge_wait(self, task_id, seconds): pass
    @abstractmethod
    def resume(self, task_id, call_id, binding): pass


class CandidateCallJournal(ResearchCallJournal):
    """Candidate local disk journal with exclusive per-task dispatch and replay.

    Reopening an INFLIGHT record never reissues an ambiguous external request.
    This is NOT a cloud store nor proof of Supabase transaction parity.
    """
    def __init__(self, directory, *, candidate_scope):
        root = Path(directory)
        if not root.is_absolute() or candidate_scope != 'JSM_V2_ISOLATED_CANDIDATE':
            raise IdentityReviewRequired('EXPLICIT_CANDIDATE_DIRECTORY_REQUIRED')
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'research-calls-v2.sqlite3'
        with self._db() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and tables != {'scope', 'tasks', 'calls'}:
                raise IdentityReviewRequired('UNRECOGNIZED_EXECUTION_JOURNAL')
            if not tables:
                db.execute('CREATE TABLE scope (value TEXT PRIMARY KEY)')
                db.execute('INSERT INTO scope VALUES (?)', (candidate_scope,))
                db.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY, active_seconds REAL NOT NULL DEFAULT 0 CHECK(active_seconds>=0))')
                db.execute('''CREATE TABLE calls (task_id TEXT NOT NULL REFERENCES tasks(id), call_id TEXT NOT NULL,
                    stage TEXT NOT NULL CHECK(stage IN ('SEARCH','ANALYZE')), binding TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
                    state TEXT NOT NULL, owner TEXT, retry_at REAL NOT NULL DEFAULT 0, response TEXT,
                    PRIMARY KEY(task_id,call_id))''')
            if [r[0] for r in db.execute('SELECT value FROM scope')] != [candidate_scope]:
                raise IdentityReviewRequired('EXECUTION_JOURNAL_SCOPE_MISMATCH')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def inspect(self, task_id, call_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM calls WHERE task_id=? AND call_id=?', (task_id, call_id)).fetchone()
            return dict(row) if row else None

    def start(self, task_id, call_id, stage, binding, now):
        with self._db() as db:
            db.execute('INSERT INTO tasks(id) VALUES (?) ON CONFLICT DO NOTHING', (task_id,))
            row = db.execute('SELECT * FROM calls WHERE task_id=? AND call_id=?', (task_id, call_id)).fetchone()
            if row and row['binding'] != binding:
                raise ResearchExecutionStopped('REVIEW_REQUIRED', 'CALL_BINDING_CHANGED')
            if row and row['state'] == 'SUCCEEDED':
                return json.loads(row['response']), None, 0
            if row and row['state'] not in ('READY', 'RETRY_WAIT'):
                status = row['state'] if row['state'] in ('DEFERRED_FREE_QUOTA', 'FAILED') else 'REVIEW_REQUIRED'
                raise ResearchExecutionStopped(status, 'CALL_NOT_READY_OR_OUTCOME_UNCERTAIN')
            if row and (row['attempts'] >= 3 or row['retry_at'] > now):
                raise ResearchExecutionStopped('FAILED' if row['attempts'] >= 3 else 'REVIEW_REQUIRED', 'CALL_ATTEMPT_OR_BACKOFF_BOUNDARY')
            if db.execute("SELECT 1 FROM calls WHERE task_id=? AND state='INFLIGHT'", (task_id,)).fetchone():
                raise ResearchExecutionStopped('REVIEW_REQUIRED', 'TASK_ALREADY_HAS_CALL_INFLIGHT')
            remaining = 300 - db.execute('SELECT active_seconds FROM tasks WHERE id=?', (task_id,)).fetchone()[0]
            if remaining <= 0:
                raise ResearchExecutionStopped('FAILED', 'TASK_ACTIVE_TIMEOUT')
            if row is None:
                count = db.execute('SELECT count(*) FROM calls WHERE task_id=? AND stage=?', (task_id, stage)).fetchone()[0]
                if count >= (3 if stage == 'SEARCH' else 1):
                    raise ResearchExecutionStopped('REVIEW_REQUIRED', 'LOGICAL_CALL_LIMIT')
                db.execute('INSERT INTO calls(task_id,call_id,stage,binding,state) VALUES (?,?,?,?,?)', (task_id, call_id, stage, binding, 'READY'))
            owner = uuid.uuid4().hex
            db.execute("UPDATE calls SET attempts=attempts+1,state='INFLIGHT',owner=? WHERE task_id=? AND call_id=?", (owner, task_id, call_id))
            return None, owner, remaining

    def finish(self, task_id, call_id, owner, *, elapsed, kind, now, response=None, delay=0):
        with self._db() as db:
            row = db.execute('SELECT * FROM calls WHERE task_id=? AND call_id=?', (task_id, call_id)).fetchone()
            if row is None or row['state'] != 'INFLIGHT' or row['owner'] != owner:
                raise ResearchExecutionStopped('REVIEW_REQUIRED', 'CALL_OWNER_CHANGED')
            db.execute('UPDATE tasks SET active_seconds=active_seconds+? WHERE id=?', (max(0, elapsed), task_id))
            active = db.execute('SELECT active_seconds FROM tasks WHERE id=?', (task_id,)).fetchone()[0]
            state = {'success':'SUCCEEDED', 'transient':'RETRY_WAIT', 'quota':'DEFERRED_FREE_QUOTA', 'business':'REVIEW_REQUIRED'}.get(kind, 'REVIEW_REQUIRED')
            if active >= 300 or (kind == 'transient' and row['attempts'] >= 3):
                state = 'FAILED'
            db.execute('UPDATE calls SET state=?,retry_at=?,response=? WHERE task_id=? AND call_id=?',
                       (state, now + delay, _json(response) if state == 'SUCCEEDED' else None, task_id, call_id))
            return state

    def charge_wait(self, task_id, seconds):
        with self._db() as db:
            db.execute('UPDATE tasks SET active_seconds=active_seconds+? WHERE id=?', (max(0, seconds), task_id))

    def resume(self, task_id, call_id, binding):
        with self._db() as db:
            row = db.execute('SELECT * FROM calls WHERE task_id=? AND call_id=?', (task_id, call_id)).fetchone()
            if row is None or row['state'] != 'DEFERRED_FREE_QUOTA' or row['binding'] != binding or row['attempts'] >= 3:
                raise ResearchExecutionStopped('REVIEW_REQUIRED', 'DEFERRED_RESUME_NOT_AUTHORIZED')
            db.execute("UPDATE calls SET state='READY',retry_at=0 WHERE task_id=? AND call_id=?", (task_id, call_id))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Do not forward a credential to a redirected endpoint.


class HttpsJsonTransport:
    """Exactly one HTTPS request, with fixed endpoints and no hidden retry."""
    def once(self, *, stage, model, credential, body, timeout):
        url = SEARCH_ENDPOINT if stage == 'SEARCH' else MODEL_ENDPOINT.format(model=model)
        headers = {'Content-Type':'application/json'}
        headers['Authorization' if stage == 'SEARCH' else 'x-goog-api-key'] = ('Bearer ' if stage == 'SEARCH' else '') + credential
        req = urllib.request.Request(url, data=_json(body).encode(), headers=headers, method='POST')
        try:
            with urllib.request.build_opener(_NoRedirect()).open(req, timeout=timeout) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ProviderFault('business', 'PROVIDER_RESPONSE_TOO_LARGE')
            result = json.loads(raw)
            if type(result) is not dict:
                raise ProviderFault('business', 'PROVIDER_RESPONSE_NOT_OBJECT')
            return result
        except urllib.error.HTTPError as exc:
            code = exc.code
            try:
                retry_after = float(exc.headers.get('Retry-After', '0'))
                retry_after = retry_after if math.isfinite(retry_after) and 0 <= retry_after <= 60 else 0
            except (ValueError, TypeError):
                retry_after = 0
            # Gemini 429 without a bounded retry hint cannot distinguish daily
            # quota from a rate limit: defer conservatively, never enable billing.
            kind = ('quota' if code in (432, 433) or (stage == 'ANALYZE' and code == 429 and not retry_after)
                    else 'transient' if code == 429 or 500 <= code <= 599 else 'business')
            exc.close()
            raise ProviderFault(kind, f'PROVIDER_HTTP_{code}', retry_after=retry_after, rate_limited=code == 429) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            raise ProviderFault('transient', 'PROVIDER_CONNECTION_FAILURE') from None
        except (ValueError, UnicodeError):
            raise ProviderFault('business', 'PROVIDER_RESPONSE_INVALID_JSON') from None


class ResearchExecutionSession:
    def __init__(self, *, task_id, journal, authorize, credentials, model, limiter,
                 transport=None, now=None, monotonic=None, sleep=None):
        if (not isinstance(task_id, str) or not task_id.strip() or not isinstance(journal, ResearchCallJournal)
                or not callable(authorize) or not isinstance(limiter, AdaptiveCallLimiter)
                or not isinstance(model, str) or not re.fullmatch('gemini-[a-z0-9.-]+', model)):
            raise IdentityReviewRequired('EXPLICIT_CANDIDATE_EXECUTION_SESSION_REQUIRED')
        self.task_id, self.journal, self.authorize = task_id, journal, authorize
        self._credentials, self.model, self.limiter = dict(credentials), model, limiter
        self.transport = transport or HttpsJsonTransport()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.monotonic, self.sleep = monotonic or time.monotonic, sleep or time.sleep

    def _grant(self, stage, call_id, attempt):
        credential = self._credentials.get(stage, '')
        grant = self.authorize(self.task_id, stage, call_id, attempt)
        binding = _authorize(grant, task_id=self.task_id, stage=stage, credential=credential, model=self.model, now=self.now())
        return binding, credential

    def call(self, stage, call_id, body):
        if stage not in ('SEARCH', 'ANALYZE') or not isinstance(call_id, str) or not call_id or type(body) is not dict:
            raise IdentityReviewRequired('EXACT_LOGICAL_PROVIDER_CALL_REQUIRED')
        if stage == 'SEARCH' and (body.get('search_depth') != 'basic' or body.get('auto_parameters', False) is not False
                or set(body) - {'query','topic','search_depth','max_results','include_answer','include_raw_content','include_images','include_domains'}):
            raise IdentityReviewRequired('ONLY_REVIEWED_BASIC_SEARCH_ALLOWED')
        config = body.get('generationConfig', {})
        if stage == 'ANALYZE' and (set(body) - {'contents', 'generationConfig'} or type(config) is not dict
                or set(config) - {'temperature','responseMimeType','responseJsonSchema'}
                or config.get('responseMimeType') != 'application/json'):
            raise IdentityReviewRequired('MODEL_TOOLS_AND_PAID_FEATURES_FORBIDDEN')
        request_sha = _sha(_json(body).encode())
        while True:
            row = self.journal.inspect(self.task_id, call_id)
            attempt = min(3, (row['attempts'] if row else 0) + 1)
            binding, credential = self._grant(stage, call_id, attempt)
            binding = _sha((binding + request_sha).encode())
            if row and row['state'] == 'RETRY_WAIT':
                wait = max(0, row['retry_at'] - self.now().timestamp())
                if wait:
                    started = self.monotonic()
                    self.sleep(wait)
                    self.journal.charge_wait(self.task_id, self.monotonic() - started)
            with self.limiter.slot():
                # Revalidate after waiting for a concurrency slot/backoff.
                fresh, credential = self._grant(stage, call_id, attempt)
                if _sha((fresh + request_sha).encode()) != binding:
                    raise IdentityReviewRequired('AUTHORITY_CHANGED_BEFORE_DISPATCH')
                cached, owner, remaining = self.journal.start(self.task_id, call_id, stage, binding, self.now().timestamp())
                if owner is None:
                    return cached
                started = self.monotonic()
                response, fault = None, None
                try:
                    response = self.transport.once(stage=stage, model=self.model, credential=credential, body=body,
                        timeout=max(0.001, min(75 if stage == 'SEARCH' else 180, remaining)))
                    if type(response) is not dict:
                        raise ProviderFault('business', 'PROVIDER_RESPONSE_NOT_OBJECT')
                    kind, delay = 'success', 0
                except ProviderFault as exc:
                    fault, kind = exc, exc.kind
                    delay = max(2 ** attempt, exc.retry_after) if kind == 'transient' else 0
                    if exc.rate_limited:
                        self.limiter.throttled()
                except Exception:
                    # Unknown transport outcome may have consumed quota. Never
                    # mechanically retry or expose raw exception text.
                    kind, delay = 'business', 0
                    fault = ProviderFault('business', 'PROVIDER_OUTCOME_UNCERTAIN')
                state = self.journal.finish(self.task_id, call_id, owner, elapsed=self.monotonic()-started,
                    kind=kind, now=self.now().timestamp(), response=response, delay=delay)
            if state == 'SUCCEEDED':
                return response
            if state != 'RETRY_WAIT':
                raise ResearchExecutionStopped(state, str(fault) if fault else 'TASK_ACTIVE_TIMEOUT')

    def resume_deferred(self, stage, call_id, body):
        row = self.journal.inspect(self.task_id, call_id)
        binding, _ = self._grant(stage, call_id, (row['attempts'] if row else 0) + 1)
        binding = _sha((binding + _sha(_json(body).encode())).encode())
        self.journal.resume(self.task_id, call_id, binding)
