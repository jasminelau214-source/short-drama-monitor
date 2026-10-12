from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass
from typing import Callable

import persistence
from research_pipeline import research_task
from research_execution_v2 import AdaptiveCallLimiter, ResearchExecutionSession
from research_completion_v2 import ResearchWritebackRejected
from drama_identity_v2 import IdentityReviewRequired

_WORKER_LOCK = threading.Lock()
_WORKER_RUNNING = False


@dataclass(frozen=True)
class ResearchHandoff:
    """Trusted code injection; never construct this from request/task JSON.

    claim must address only the isolated V2 queue. Context must load protected
    authority/review ledgers freshly for each stage; update owns that queue's
    status persistence. No live implementation is installed by this module.
    """
    claim: Callable
    context: Callable
    update: Callable
    execution: Callable | None = None


def _valid_handoff(handoff):
    return isinstance(handoff, ResearchHandoff) and all(callable(value) for value in (handoff.claim,handoff.context,handoff.update))


def configured(*, handoff=None) -> bool:
    return _valid_handoff(handoff) and callable(handoff.execution)


def _process(task, *, apply_research, handoff, limiter):
    task_id = str(task.get('id') or '')
    try:
        context = handoff.context(task, 'BEFORE_RESEARCH', None)
        session_kw = {}
        if handoff.execution is not None:
            session = handoff.execution(task, limiter)
            if not isinstance(session, ResearchExecutionSession) or session.limiter is not limiter or session.task_id != task_id:
                raise IdentityReviewRequired('EXACT_TASK_SESSION_AND_SHARED_LIMITER_REQUIRED')
            session_kw['provider_session'] = session
        outcome = research_task(task, validation_context=context,
            review_context_provider=lambda result,sources: handoff.context(task,'AFTER_RESEARCH',{'research':result,'sources':sources}), **session_kw)
        status = str(outcome.get('status') or 'REVIEW_REQUIRED')
        research = outcome.get('research') if isinstance(outcome.get('research'), dict) else {}
        sources = outcome.get('sources') if isinstance(outcome.get('sources'), list) else []
        confidence = str(outcome.get('confidence') or '')
        missing_fields = [str(x) for x in (outcome.get('missingFields') or []) if str(x)]
        error = str(outcome.get('error') or '')
        if status == 'COMPLETE':
            write_context = handoff.context(task, 'BEFORE_WRITEBACK', outcome)
            apply_research(task, research, validation_context=write_context)
        handoff.update(task_id, status=status, research=research, sources=sources,
                       confidence=confidence, missing_fields=missing_fields, error=error)
        print(f"[research] {status} {task.get('platform')} #{task.get('rank')} {task.get('title')}")
    except (ResearchWritebackRejected, IdentityReviewRequired) as exc:
        try:
            handoff.update(task_id, status='REVIEW_REQUIRED', error=str(exc)[:4000])
        except Exception:
            print('[research] failed to persist review refusal')
        print(f'[research] REVIEW_REQUIRED {task_id}')
    except Exception:
        # A task is never restarted as a whole after an ambiguous provider or
        # writer outcome. Its journal and queue state need reconciliation.
        try:
            handoff.update(task_id, status='FAILED', error='WORKER_OUTCOME_REQUIRES_RECONCILIATION')
        except Exception:
            print('[research] failed to persist error')
        print(f'[research] FAILED {task_id}')


def _run(*, apply_research: Callable, max_tasks: int | None = None, handoff=None) -> None:
    global _WORKER_RUNNING
    try:
        # An unconfigured candidate must not claim/mutate the legacy queue,
        # even if production credentials happen to be present in its env.
        if not _valid_handoff(handoff):
            return
        limiter = AdaptiveCallLimiter()
        width = 2 if callable(handoff.execution) else 1
        submitted, pending = 0, set()
        with ThreadPoolExecutor(max_workers=width, thread_name_prefix='jsm-v2-research') as pool:
            while max_tasks is None or submitted < max_tasks:
                if len(pending) >= width:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        future.result()
                tasks = handoff.claim(1)  # Adapter owns durable isolated claim/CAS.
                if not tasks:
                    break
                if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict):
                    raise IdentityReviewRequired('EXACT_SINGLE_ISOLATED_TASK_CLAIM_REQUIRED')
                pending.add(pool.submit(_process, tasks[0], apply_research=apply_research, handoff=handoff, limiter=limiter))
                submitted += 1
            for future in pending:
                future.result()
    finally:
        with _WORKER_LOCK:
            _WORKER_RUNNING = False


def schedule(*, apply_research: Callable, delay: float = 0.5, max_tasks: int | None = None, handoff=None) -> bool:
    global _WORKER_RUNNING
    if not configured(handoff=handoff):
        return False
    with _WORKER_LOCK:
        if _WORKER_RUNNING:
            return True
        _WORKER_RUNNING = True
    def start():
        _run(apply_research=apply_research, max_tasks=max_tasks, handoff=handoff)
    timer = threading.Timer(max(0.0, delay), start)
    timer.daemon = True
    timer.start()
    return True


def running() -> bool:
    with _WORKER_LOCK:
        return _WORKER_RUNNING
