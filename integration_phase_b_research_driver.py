from __future__ import annotations

import json
import os
import re

if os.environ.get('JSM_ALLOW_INTEGRATION_STUB', '').strip() != '1':
    raise RuntimeError('INTEGRATION_STUB_NOT_EXPLICITLY_ENABLED')

import app
import persistence
import research_worker
from research_pipeline import CORE_FIELDS, OPTIONAL_FIELDS


def _slug(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', value.casefold()).strip('-')[:80] or 'drama'


def _stub_research(task: dict) -> dict:
    title = str(task.get('title') or '')
    url = f"https://integration.invalid/{_slug(title)}"
    research = {field: 'integration verified' for field in CORE_FIELDS}
    research['genre'] = '现代都市'
    research['audience'] = '泛受众'
    research.update({field: '' for field in OPTIONAL_FIELDS})
    research.update({
        'canonicalTitle': title,
        'newnessResolution': 'new',
        'confidence': 'medium',
        'missingFields': [],
        'auditNotes': ['INTEGRATION_STUB_EVIDENCE_ONLY'],
        'sourceUrls': [url],
        'needsGPT': False,
    })
    return {
        'status': 'COMPLETE',
        'research': research,
        'sources': [{
            'url': url,
            'title': 'Integration deterministic stub',
            'official': True,
            'score': 1.0,
            'safetyNotes': [],
        }],
        'confidence': 'medium',
        'missingFields': [],
        'error': '',
        'searchMeta': {
            'integrationStub': True,
            'officialCount': 1,
            'sanitizedSources': 0,
        },
    }


def main() -> None:
    if not persistence.configured():
        raise RuntimeError('PHASE_B_PERSISTENCE_NOT_CONFIGURED')

    app.sync_persistent_cache()

    before = persistence.list_research_tasks(limit=100)
    pending = [row for row in before if row.get('status') == 'PENDING']
    if len(pending) != 9:
        raise AssertionError(f'expected 9 PENDING tasks before stub research, got {len(pending)}')

    original = research_worker.research_task
    original_sleep = research_worker.time.sleep
    try:
        research_worker.research_task = _stub_research
        research_worker.time.sleep = lambda _: None
        research_worker._run(apply_research=app.apply_research_result, max_tasks=20)
    finally:
        research_worker.research_task = original
        research_worker.time.sleep = original_sleep
        research_worker._WORKER_RUNNING = False

    tasks = persistence.list_research_tasks(limit=100)
    complete = [row for row in tasks if row.get('status') == 'COMPLETE']
    bad = [row for row in tasks if row.get('status') in {'PENDING','RESEARCHING','FAILED','REVIEW_REQUIRED'}]
    overrides = persistence.list_overrides()

    if len(complete) != 9:
        raise AssertionError(f'expected 9 COMPLETE tasks, got {len(complete)}')
    if bad:
        raise AssertionError(f'unexpected unfinished/error tasks: {[(x.get("id"),x.get("status")) for x in bad]}')
    if len(overrides) != 9:
        raise AssertionError(f'expected 9 persisted overrides, got {len(overrides)}')

    print(json.dumps({
        'status': 'PASS_PHASE_B_RESEARCH_STUB',
        'researchProvider': 'DETERMINISTIC_STUB_NOT_REAL_PROVIDER',
        'tasksBefore': len(before),
        'pendingBefore': len(pending),
        'completeAfter': len(complete),
        'overridesAfter': len(overrides),
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
