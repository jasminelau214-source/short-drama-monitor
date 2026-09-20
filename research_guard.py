from __future__ import annotations

import json

from drama_identity import normalize_title


def _json_obj(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _scope(run: dict) -> tuple[str, str]:
    result = _json_obj(run.get('result_json'))
    collector = result.get('collector') if isinstance(result.get('collector'), dict) else {}
    source_type = str(collector.get('sourceType') or 'SHORT_DRAMA_APP').strip() or 'SHORT_DRAMA_APP'
    target_key = str(collector.get('targetKey') or 'daily_top_all').strip() or 'daily_top_all'
    return source_type, target_key


def _is_complete_ranking(run: dict) -> bool:
    result = _json_obj(run.get('result_json'))
    if result.get('batchComplete') is not True:
        return False

    rows = result.get('rows') if isinstance(result.get('rows'), list) else []
    collector = result.get('collector') if isinstance(result.get('collector'), dict) else {}
    raw_top_n = collector.get('topN') or len(rows)
    try:
        top_n = int(raw_top_n)
    except (TypeError, ValueError):
        return False
    if not 1 <= top_n <= 100 or len(rows) != top_n:
        return False

    ranks = set()
    titles = set()
    for item in rows:
        if not isinstance(item, dict):
            return False
        try:
            rank = int(item.get('rank'))
        except (TypeError, ValueError):
            return False
        norm = normalize_title(item.get('title'))
        if rank < 1 or rank > top_n or rank in ranks or not norm or norm in titles:
            return False
        ranks.add(rank)
        titles.add(norm)
    return ranks == set(range(1, top_n + 1))


def _run_titles(run: dict) -> set[str]:
    result = _json_obj(run.get('result_json'))
    rows = result.get('rows') if isinstance(result.get('rows'), list) else []
    return {
        normalize_title(item.get('title'))
        for item in rows
        if isinstance(item, dict) and normalize_title(item.get('title'))
    }


def assess_research_task(task: dict, analysis_runs: list[dict]) -> dict:
    """Require the task to belong to the latest complete same-scope App run."""
    task_run_id = str(task.get('analysis_run_id') or '')
    collection_date = str(task.get('collection_date') or '')
    platform = str(task.get('platform') or '')
    title_norm = normalize_title(task.get('normalized_title') or task.get('title'))

    if not title_norm:
        return {
            'current': False,
            'code': 'RESEARCH_IDENTITY_UNRESOLVED',
            'reason': 'Task has no valid Drama Identity key.',
        }

    run_by_id = {str(run.get('id') or ''): run for run in analysis_runs}
    origin = run_by_id.get(task_run_id)
    if not origin:
        return {
            'current': False,
            'code': 'AUTHORITATIVE_RUN_UNRESOLVED',
            'reason': 'Task origin run is missing from the available run window.',
        }

    source_type, target_key = _scope(origin)
    if source_type != 'SHORT_DRAMA_APP':
        return {
            'current': False,
            'code': 'INVALID_RESEARCH_SOURCE_TYPE',
            'reason': f'Auto research accepts SHORT_DRAMA_APP only, got {source_type}.',
        }

    candidates = []
    for run in analysis_runs:
        if str(run.get('collection_date') or '') != collection_date:
            continue
        if str(run.get('platform') or '') != platform:
            continue
        if _scope(run) != (source_type, target_key):
            continue
        if not _is_complete_ranking(run):
            continue
        candidates.append(run)

    if not candidates:
        return {
            'current': False,
            'code': 'NO_COMPLETE_AUTHORITATIVE_RUN',
            'reason': 'No complete ranking run exists for this task scope.',
        }

    latest = max(candidates, key=lambda run: (str(run.get('updated_at') or ''), str(run.get('id') or '')))
    latest_id = str(latest.get('id') or '')
    if title_norm not in _run_titles(latest):
        return {
            'current': False,
            'code': 'SUPERSEDED_BY_LATER_RUN',
            'reason': f'Title is absent from latest authoritative run {latest_id}.',
            'latestRunId': latest_id,
            'targetKey': target_key,
        }

    return {
        'current': True,
        'code': 'CURRENT',
        'reason': 'Task title is present in the latest complete authoritative run.',
        'latestRunId': latest_id,
        'targetKey': target_key,
        'sourceType': source_type,
    }
