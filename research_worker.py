from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

import persistence
from research_pipeline import configured as research_configured, research_task
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


def _valid_handoff(handoff):
    return isinstance(handoff, ResearchHandoff) and all(callable(value) for value in (handoff.claim,handoff.context,handoff.update))


def configured(*, handoff=None) -> bool:
    return _valid_handoff(handoff) and research_configured()


def _run(*, apply_research: Callable, max_tasks: int | None = None, handoff=None) -> None:
    global _WORKER_RUNNING
    try:
        # An unconfigured candidate must not claim/mutate the legacy queue,
        # even if production credentials happen to be present in its env.
        if not _valid_handoff(handoff):
            return
        processed = 0
        while max_tasks is None or processed < max_tasks:
            tasks = handoff.claim(1)
            if not tasks:
                break
            task = tasks[0]
            task_id = str(task.get('id') or '')
            try:
                context = handoff.context(task, 'BEFORE_RESEARCH', None)
                outcome = research_task(task, validation_context=context,
                    review_context_provider=lambda result,sources: handoff.context(task,'AFTER_RESEARCH',{'research':result,'sources':sources}))
                status = str(outcome.get('status') or 'REVIEW_REQUIRED')
                research = outcome.get('research') if isinstance(outcome.get('research'), dict) else {}
                sources = outcome.get('sources') if isinstance(outcome.get('sources'), list) else []
                confidence = str(outcome.get('confidence') or '')
                missing_fields = [str(x) for x in (outcome.get('missingFields') or []) if str(x)]
                error = str(outcome.get('error') or '')
                if status == 'COMPLETE':
                    write_context = handoff.context(task, 'BEFORE_WRITEBACK', outcome)
                    apply_research(task, research, validation_context=write_context)
                handoff.update(
                    task_id,
                    status=status,
                    research=research,
                    sources=sources,
                    confidence=confidence,
                    missing_fields=missing_fields,
                    error=error,
                )
                print(f"[research] {status} {task.get('platform')} #{task.get('rank')} {task.get('title')}")
            except (ResearchWritebackRejected, IdentityReviewRequired) as exc:
                try:
                    handoff.update(task_id, status='REVIEW_REQUIRED', error=str(exc)[:4000])
                except Exception as inner:
                    print(f'[research] failed to persist review refusal: {inner}')
                print(f"[research] REVIEW_REQUIRED {task.get('platform')} {task.get('title')}: {exc}")
            except Exception as exc:
                error = str(exc)[:4000]
                try:
                    handoff.update(task_id, status='FAILED', error=error)
                except Exception as inner:
                    print(f'[research] failed to persist error: {inner}')
                print(f"[research] FAILED {task.get('platform')} #{task.get('rank')} {task.get('title')}: {error}")
            processed += 1
            # Keep free search/model APIs comfortably below burst limits.
            time.sleep(0.8)
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
