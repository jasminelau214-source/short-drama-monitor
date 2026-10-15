"""Explicit operator-run HTTPS journal probe; no providers or queue execution.

Credentials arrive once on stdin and never enter files, arguments or output.
Only the separately created, pinned JSM staging project is accepted.
Successful synthetic checkpoints are retained for administrator reconciliation.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback
import uuid

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from supabase_journal_v2 import (SupabaseJournalConfig, SupabaseCallJournal,
    SupabaseRpcTransport, IdentityReviewRequired, ResearchExecutionStopped)

PROJECT = 'adxlwjpqifejdcfozpuo'
INSTANCE = '6ccc4bc4-b802-4f89-9e3a-af6af0658d9f'
ADAPTER_SHA256 = '1e0850ac16098a98854cbcfe8a2db7bf18d14791d9fbcc8f14d97c6b2f1cb6cb'
ACTIVE_CHECK = 'configuration_and_handshake'
PROGRESS = []


def main():
    if hashlib.sha256((ROOT/'supabase_journal_v2.py').read_bytes()).hexdigest() != ADAPTER_SHA256:
        raise RuntimeError('REVIEWED_ADAPTER_SOURCE_CHANGED')
    raw = sys.stdin.buffer.read(20000)
    credentials = json.loads(raw)
    if set(credentials) != {'publishable_key', 'worker_access_token'}:
        raise RuntimeError('EXPLICIT_STAGING_SESSION_REQUIRED')
    config = SupabaseJournalConfig(PROJECT, INSTANCE, **credentials)
    del raw, credentials
    journal = SupabaseCallJournal(config)
    prefix = 'c6:' + str(uuid.uuid4())
    checks = []
    global PROGRESS
    PROGRESS = checks
    tasks = []

    def task(name):
        value = prefix + ':' + name
        tasks.append(value)
        return value

    def start(t, call='search:1', binding='a'*64, stage='SEARCH', j=journal):
        return j.start(t, call, stage, binding, time.time())

    def finish(t, owner, call='search:1', j=journal, **kwargs):
        values = dict(elapsed=0, kind='success', now=time.time(), response={'synthetic': True}, delay=0)
        values.update(kwargs)
        return j.finish(t, call, owner, **values)

    def refusal(call, reason=None, status=None):
        try: call()
        except ResearchExecutionStopped as exc:
            assert reason is None or reason == str(exc)
            assert status is None or status == exc.status
            return
        raise AssertionError('EXPECTED_REFUSAL')

    def run(name, check):
        global ACTIVE_CHECK
        ACTIVE_CHECK = name
        before = time.monotonic()
        check()
        checks.append(dict(name=name, passed=True, elapsedSeconds=round(time.monotonic()-before, 3)))

    def handshake():
        assert journal.inspect(prefix, 'missing') is None
        try: SupabaseCallJournal(replace(config, instance_id=str(uuid.uuid4())))
        except IdentityReviewRequired: pass
        else: raise AssertionError('INSTANCE_MISMATCH_ACCEPTED')
    run('real_auth_postgrest_handshake_and_instance_pin', handshake)

    def persistence():
        t = task('persistent')
        _, owner, _ = start(t)
        assert finish(t, owner) == 'SUCCEEDED'
        reopened = SupabaseCallJournal(config)
        assert start(t, j=reopened) == ({'synthetic': True}, None, 0)
        assert reopened.inspect(t, 'search:1')['attempts'] == 1
    run('success_persists_across_new_adapter', persistence)

    def uncertain_finish():
        real = SupabaseRpcTransport()
        class LostReply:
            def post(self, cfg, operation, payload):
                value = real.post(cfg, operation, payload)
                if operation == 'finish':
                    raise ResearchExecutionStopped('REVIEW_REQUIRED', 'SUPABASE_RPC_OUTCOME_UNCERTAIN')
                return value
        t = task('lost-finish')
        wrapped = SupabaseCallJournal(config, transport=LostReply())
        _, owner, _ = start(t, j=wrapped)
        refusal(lambda: finish(t, owner, j=wrapped), 'SUPABASE_RPC_OUTCOME_UNCERTAIN')
        assert journal.inspect(t, 'search:1')['state'] == 'SUCCEEDED'
        assert start(t) == ({'synthetic': True}, None, 0)
    run('lost_finish_reply_reconciles_without_reissue', uncertain_finish)

    def concurrent_reservation():
        t = task('concurrent')
        def reserve(_):
            try: return start(t)[1]
            except ResearchExecutionStopped as exc:
                assert exc.status == 'REVIEW_REQUIRED'
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            owners = list(pool.map(reserve, range(2)))
        assert sum(x is not None for x in owners) == 1
        assert journal.inspect(t, 'search:1')['attempts'] == 1
        assert finish(t, next(x for x in owners if x)) == 'SUCCEEDED'
    run('concurrent_duplicate_has_one_owner', concurrent_reservation)

    def owners_and_binding():
        t = task('binding')
        _, owner, _ = start(t)
        refusal(lambda: start(t), status='REVIEW_REQUIRED')
        assert 'owner' not in journal.inspect(t, 'search:1')
        refusal(lambda: finish(t, str(uuid.uuid4())), 'CALL_OWNER_CHANGED')
        assert finish(t, owner) == 'SUCCEEDED'
        refusal(lambda: start(t, binding='b'*64), 'CALL_BINDING_CHANGED')
    run('unknown_inflight_held_owner_and_binding_protected', owners_and_binding)

    def idempotency():
        t = task('idempotent')
        _, owner, _ = start(t)
        assert finish(t, owner) == finish(t, owner) == 'SUCCEEDED'
        refusal(lambda: finish(t, owner, response={'conflict': True}), 'OUTCOME_CONFLICT')
        assert journal.inspect(t, 'search:1')['response'] == {'synthetic': True}
    run('exact_finish_idempotent_conflicting_finish_refused', idempotency)

    def backoff_and_attempts():
        t = task('retry')
        for n in range(1, 4):
            _, owner, _ = start(t)
            delay = 2 if n == 1 else 0
            state = finish(t, owner, kind='transient', response=None, delay=delay)
            assert state == ('FAILED' if n == 3 else 'RETRY_WAIT')
            if n == 1:
                refusal(lambda: start(t), 'CALL_ATTEMPT_OR_BACKOFF_BOUNDARY')
                time.sleep(2.1)
        assert journal.inspect(t, 'search:1')['attempts'] == 3
        refusal(lambda: start(t), status='FAILED')
    run('server_backoff_and_three_attempt_ceiling', backoff_and_attempts)

    def quota_resume():
        t = task('quota')
        _, owner, _ = start(t)
        assert finish(t, owner, kind='quota', response=None) == 'DEFERRED_FREE_QUOTA'
        refusal(lambda: start(t), status='DEFERRED_FREE_QUOTA')
        journal.resume(t, 'search:1', 'a'*64)
        _, owner, _ = start(t)
        assert finish(t, owner) == 'SUCCEEDED'
        assert journal.inspect(t, 'search:1')['attempts'] == 2
    run('quota_pause_resume_retains_attempts', quota_resume)

    def logical_limits():
        t = task('logical-limits')
        for n in range(1, 4):
            call = f'search:{n}'
            _, owner, _ = start(t, call)
            assert finish(t, owner, call) == 'SUCCEEDED'
        refusal(lambda: start(t, 'search:4'), 'LOGICAL_CALL_LIMIT')
        _, owner, _ = start(t, 'analyze:1', stage='ANALYZE')
        assert finish(t, owner, 'analyze:1') == 'SUCCEEDED'
        refusal(lambda: start(t, 'analyze:2', stage='ANALYZE'), 'LOGICAL_CALL_LIMIT')
    run('three_search_calls_and_one_analysis_call', logical_limits)

    def budget():
        t = task('budget')
        _, owner, _ = start(t)
        assert finish(t, owner, elapsed=300) == 'FAILED'
        refusal(lambda: start(t, 'search:2'), 'TASK_ACTIVE_TIMEOUT', 'FAILED')
    run('cumulative_provider_budget_enforced', budget)

    def forged_jwt():
        parts = config.worker_access_token.split('.')
        parts[2] = ('A' if parts[2][0] != 'A' else 'B') + parts[2][1:]
        refusal(lambda: SupabaseCallJournal(replace(config, worker_access_token='.'.join(parts))),
                'SUPABASE_RPC_HTTP_401')
    run('forged_auth_signature_rejected_by_gateway', forged_jwt)

    def strict_payload():
        value = SupabaseRpcTransport().post(config, 'start', dict(
            task_id=prefix+':injected', call_id='search:1', stage='SEARCH', binding='a'*64, approved=True))
        assert value['ok'] is False
        assert journal.inspect(prefix+':injected', 'search:1') is None
    run('client_self_approval_payload_cannot_mutate', strict_payload)

    print(json.dumps(dict(scope='LIVE_STAGING_AUTH_POSTGREST_JOURNAL_ONLY',
        projectRef=PROJECT, instanceId=INSTANCE, taskPrefix=prefix, taskIds=tasks,
        adapterSha256=ADAPTER_SHA256, checks=checks, testsPassed=len(checks),
        providerRequests=0, fullStagingE2EPassed=False)))


if __name__ == '__main__':
    try: main()
    except BaseException as exc:
        # Auth sessions, raw requests and upstream replies never enter output.
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        print(json.dumps({'status': 'FAILED', 'failedCheck': ACTIVE_CHECK,
            'reason': str(exc) if isinstance(exc, ResearchExecutionStopped) else 'CLOUD_JOURNAL_PROBE_ASSERTION_OR_TRANSPORT_FAILED',
            'exceptionType': type(exc).__name__, 'probeLine': frame.lineno,
            'checksPassedBeforeFailure': PROGRESS}))
        raise SystemExit(1) from None
