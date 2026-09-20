from __future__ import annotations

import threading
import time
from typing import Callable

import persistence
from research_guard import assess_research_task
from research_pipeline import configured as research_configured, research_task
from research_validation import validate_research_payload

_WORKER_LOCK = threading.Lock()
_WORKER_RUNNING = False


def _source_urls(sources: list[dict]) -> set[str]:
    return {
        str(item.get('url') or '').strip()
        for item in sources
        if isinstance(item, dict) and str(item.get('url') or '').strip()
    }


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
                # Fail before any search/model API spend when a task no longer
                # belongs to the authoritative same-day App ranking run.
                runs = persistence.list_analysis_runs(500)
                guard = assess_research_task(task, runs)
                if not guard.get('current'):
                    code = str(guard.get('code') or 'RESEARCH_SCOPE_REVIEW_REQUIRED')
                    persistence.update_research_task(
                        task_id,
                        status='REVIEW_REQUIRED',
                        research=task.get('research_json') if isinstance(task.get('research_json'), dict) else {},
                        sources=task.get('sources') if isinstance(task.get('sources'), list) else [],
                        confidence=str(task.get('confidence') or ''),
                        missing_fields=[str(x) for x in (task.get('missing_fields') or []) if str(x)],
                        error=code,
                    )
                    print(
                        f"[research] REVIEW_REQUIRED {task.get('platform')} "
                        f"#{task.get('rank')} {task.get('title')}: {code}"
                    )
                    processed += 1
                    continue

                outcome = research_task(task)
                status = str(outcome.get('status') or 'REVIEW_REQUIRED')
                research = outcome.get('research') if isinstance(outcome.get('research'), dict) else {}
                sources = outcome.get('sources') if isinstance(outcome.get('sources'), list) else []
                confidence = str(outcome.get('confidence') or '')
                missing_fields = [str(x) for x in (outcome.get('missingFields') or []) if str(x)]
                error = str(outcome.get('error') or '')

                # Final deterministic write gate. Upstream COMPLETE is not enough.
                if status == 'COMPLETE':
                    validation = validate_research_payload(
                        research,
                        allowed_source_urls=_source_urls(sources),
                    )
                    if not validation.get('ok'):
                        status = 'REVIEW_REQUIRED'
                        error = 'RESEARCH_SCHEMA_INVALID: ' + '; '.join(validation.get('errors') or [])
                    else:
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
