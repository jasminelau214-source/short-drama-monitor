from __future__ import annotations

import threading
import time
from typing import Callable

import persistence
from research_pipeline import configured as research_configured, research_task

_WORKER_LOCK = threading.Lock()
_WORKER_RUNNING = False


def configured() -> bool:
    return persistence.configured() and research_configured()


def _run(*, apply_research: Callable[[dict, dict], None], max_tasks: int = 20) -> None:
    global _WORKER_RUNNING
    try:
        processed = 0
        while processed < max_tasks:
            tasks = persistence.claim_research_tasks(1)
            if not tasks:
                break
            task = tasks[0]
            task_id = str(task.get('id') or '')
            try:
                outcome = research_task(task)
                status = str(outcome.get('status') or 'REVIEW_REQUIRED')
                research = outcome.get('research') if isinstance(outcome.get('research'), dict) else {}
                sources = outcome.get('sources') if isinstance(outcome.get('sources'), list) else []
                confidence = str(outcome.get('confidence') or '')
                missing_fields = [str(x) for x in (outcome.get('missingFields') or []) if str(x)]
                error = str(outcome.get('error') or '')
                if status == 'COMPLETE':
                    apply_research(task, research)
                persistence.update_research_task(
                    task_id,
                    status=status,
                    research=research,
                    sources=sources,
                    confidence=confidence,
                    missing_fields=missing_fields,
                    error=error,
                )
                print(f"[research] {status} {task.get('platform')} #{task.get('rank')} {task.get('title')}")
            except Exception as exc:
                error = str(exc)[:4000]
                try:
                    persistence.update_research_task(task_id, status='FAILED', error=error)
                except Exception as inner:
                    print(f'[research] failed to persist error: {inner}')
                print(f"[research] FAILED {task.get('platform')} #{task.get('rank')} {task.get('title')}: {error}")
            processed += 1
            # Keep free search/model APIs comfortably below burst limits.
            time.sleep(0.8)
    finally:
        with _WORKER_LOCK:
            _WORKER_RUNNING = False


def schedule(*, apply_research: Callable[[dict, dict], None], delay: float = 0.5, max_tasks: int = 20) -> bool:
    global _WORKER_RUNNING
    if not configured():
        return False
    with _WORKER_LOCK:
        if _WORKER_RUNNING:
            return True
        _WORKER_RUNNING = True
    def start():
        _run(apply_research=apply_research, max_tasks=max_tasks)
    timer = threading.Timer(max(0.0, delay), start)
    timer.daemon = True
    timer.start()
    return True


def running() -> bool:
    with _WORKER_LOCK:
        return _WORKER_RUNNING
