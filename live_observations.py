"""Compatibility shim for the V2 live publication layer.

The implementation moved to live_observations_v2.py so the existing app import path
remains stable while the publication model expands from fixed Top10/four-platform
logic to generic TopN, dynamic platforms and target-aware ranking observations.

This shim also attaches operational collection coverage from Supabase. App ranking
facts and Official Web evidence remain separate publication layers. Current-scope
Web metrics are derived from pilot_scope.json rather than historical target totals,
so retired shelves and legacy targets cannot inflate the visible dashboard.

It also preserves historical deep-research overrides across record-ID migrations.
"""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from runtime_ui_patch import main as _apply_runtime_ui_patch

# app.py imports this module before reading index.html, so apply the idempotent UI
# migration here. This keeps the deployed service compatible with its existing
# Render start command while the dashboard moves away from hard-coded Top10 copy.
_apply_runtime_ui_patch()

from live_observations_v2 import build_live_summary as _build_live_summary
from live_observations_v2 import merge_analysis_records as _merge_analysis_records
import persistence


def _platform_slug(platform):
    return ''.join(ch.lower() if ch.isalnum() else '-' for ch in str(platform or '')).strip('-') or 'platform'


def _norm_title(value):
    return re.sub(r'[^a-z0-9]+', '', str(value or '').casefold())


def _legacy_v1_id(platform, title, normalize_title):
    norm = normalize_title(title) or str(title or '').casefold().strip()
    if not norm:
        return ''
    digest = hashlib.sha1(norm.encode('utf-8')).hexdigest()[:12]
    return f'auto-{_platform_slug(platform)}-{digest}'


def _stable_v2_id(platform, target_key, title, normalize_title):
    norm = normalize_title(title) or str(title or '').casefold().strip()
    if not norm:
        return ''
    target_key = str(target_key or 'daily_top_all').strip() or 'daily_top_all'
    digest = hashlib.sha1(f'{target_key}|{norm}'.encode('utf-8')).hexdigest()[:12]
    return f'auto-{_platform_slug(platform)}-{digest}'


def _load_overrides(connect):
    try:
        with connect() as c:
            rows = c.execute('SELECT drama_id,fields_json FROM drama_overrides').fetchall()
    except Exception:
        return {}
    overrides = {}
    for row in rows:
        try:
            raw = row['fields_json']
            fields = raw if isinstance(raw, dict) else json.loads(raw or '{}')
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(fields, dict):
            overrides[str(row['drama_id'] or '')] = fields
    return overrides


def _restore_research_overrides(records, connect, normalize_title, split_lane):
    overrides = _load_overrides(connect)
    if not overrides:
        return records
    for record in records:
        platform = str(record.get('app') or '').strip()
        title = str(record.get('title') or '').strip()
        target_key = str(record.get('targetKey') or 'daily_top_all').strip() or 'daily_top_all'
        direct_id = str(record.get('id') or '').strip()
        legacy_id = _legacy_v1_id(platform, title, normalize_title)
        stable_v2_id = _stable_v2_id(platform, target_key, title, normalize_title)

        merged = {}
        # Oldest identity first; newer/current identities win on field conflicts.
        for drama_id in dict.fromkeys([legacy_id, stable_v2_id, direct_id]):
            fields = overrides.get(drama_id)
            if isinstance(fields, dict):
                merged.update(fields)
        if merged:
            record.update(merged)
            record['laneTerms'] = split_lane(record.get('lane'))
    return records


def merge_analysis_records(base_records, connect, normalize_title, split_lane):
    records = _merge_analysis_records(base_records, connect, normalize_title, split_lane)
    return _restore_research_overrides(records, connect, normalize_title, split_lane)


def _coverage_status_counts(coverage, fallback):
    """Expose current-scope target counts, never raw attempt counts."""
    if not isinstance(coverage, dict):
        return fallback if isinstance(fallback, dict) else {}
    return {
        'total': int(coverage.get('target_jobs') or 0),
        'succeeded': int(coverage.get('succeeded_targets') or 0),
        'partial': int(coverage.get('partial_targets') or 0),
        'needsReview': int(coverage.get('review_targets') or 0),
        'other': int(coverage.get('other_targets') or 0),
    }


def _json_obj(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _row_rank(row, index):
    for key in ('rank', 'position'):
        try:
            value = int(row.get(key))
        except (TypeError, ValueError, AttributeError):
            continue
        if value > 0:
            return value
    return index + 1


def _load_current_web_scope():
    """Load the user-confirmed current Web target semantics from pilot_scope.json."""
    path = Path(__file__).resolve().parent / 'pilot_scope.json'
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    default_top_n = int(raw.get('top_n') or 10)
    out = {}
    for item in raw.get('platforms') or []:
        if not isinstance(item, dict):
            continue
        platform = str(item.get('platform') or '').strip()
        if not platform:
            continue
        out[platform] = {
            'targetKey': str(item.get('target_key') or '').strip(),
            'rankingType': str(item.get('ranking_type') or '').strip(),
            'topN': int(item.get('top_n') or default_top_n),
        }
    return out


_CURRENT_WEB_SCOPE = _load_current_web_scope()


def _target_platform(target):
    source_name = str(target.get('source_name') or '').strip()
    return re.sub(r'\s+Official Web$', '', source_name).strip()


def _target_scope_spec(target):
    """Match a registry target to current semantics without trusting legacy target keys.

    A legacy target may still be usable when the ranking meaning is identical and it
    contains at least the configured TopN. This is why 9/16 DramaBox Top60 can yield
    the verified first Top10, while ShortMax Top8 and unrelated category shelves
    cannot be promoted.
    """
    if str(target.get('source_group') or '') != 'OFFICIAL_WEB':
        return None
    if target.get('source_enabled') is False or target.get('target_enabled') is False:
        return None
    platform = _target_platform(target)
    spec = _CURRENT_WEB_SCOPE.get(platform)
    if not spec:
        return None
    expected_ranking = str(spec.get('rankingType') or '').strip().casefold()
    actual_ranking = str(target.get('ranking_type') or '').strip().casefold()
    if not expected_ranking or actual_ranking != expected_ranking:
        return None
    category = str(target.get('category') or 'All').strip().casefold()
    if category not in {'', 'all'}:
        return None
    try:
        configured_top_n = int(target.get('top_n') or 0)
    except (TypeError, ValueError):
        return None
    if configured_top_n < int(spec.get('topN') or 10):
        return None
    return {'platform': platform, **spec}


def _eligible_web_targets(monitoring):
    targets = []
    for target in monitoring.get('targetStatus') or []:
        spec = _target_scope_spec(target)
        if spec:
            targets.append((target, spec))
    return targets


def _latest_job_for_target(monitoring, target_id):
    collection_date = str(monitoring.get('collectionDate') or '')
    candidates = [
        j for j in (monitoring.get('jobs') or [])
        if str(j.get('target_id') or '') == str(target_id or '')
        and (not collection_date or str(j.get('collection_date') or '') == collection_date)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda j: (
            str(j.get('finished_at') or ''),
            str(j.get('started_at') or ''),
            str(j.get('created_at') or ''),
            str(j.get('id') or ''),
        ),
    )


def _scoped_job_rows(job, top_n):
    result = _json_obj((job or {}).get('result_json'))
    raw_rows = result.get('rows') if isinstance(result.get('rows'), list) else []
    rows = []
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            continue
        rank = _row_rank(raw, index)
        if 1 <= rank <= int(top_n):
            rows.append((rank, raw))
    rows.sort(key=lambda pair: pair[0])
    return rows


def _build_current_scope_coverage(monitoring):
    """Build the frontend metric layer from current target semantics only."""
    collection_date = str(monitoring.get('collectionDate') or '')
    selected = []
    platforms = set()
    extracted_rows = 0
    for target, spec in _eligible_web_targets(monitoring):
        job = _latest_job_for_target(monitoring, target.get('target_id'))
        if not job:
            continue
        selected.append(job)
        platforms.add(spec['platform'])
        extracted_rows += len(_scoped_job_rows(job, spec['topN']))

    statuses = Counter(str(job.get('status') or '') for job in selected)
    known = {'SUCCEEDED', 'PARTIAL', 'NEEDS_REVIEW'}
    return {
        'collection_date': collection_date,
        'app_platforms_with_jobs': 0,
        'web_platforms_with_jobs': len(platforms),
        'target_jobs': len(selected),
        'succeeded_targets': statuses.get('SUCCEEDED', 0),
        'partial_targets': statuses.get('PARTIAL', 0),
        'review_targets': statuses.get('NEEDS_REVIEW', 0),
        'other_targets': sum(v for k, v in statuses.items() if k not in known),
        'extracted_rows': extracted_rows,
        'scope_policy': 'pilot_scope_semantic_match',
    }


def _analysis_label(job_status):
    if job_status == 'PARTIAL':
        return '待补采/复核'
    if job_status == 'NEEDS_REVIEW':
        return '待质量复核'
    if job_status == 'SUCCEEDED':
        return '证据层已采集（不自动深研）'
    return '暂不进入内容分析'


def _build_web_collection(monitoring):
    """Build current-scope Official Web evidence only.

    Official Web never auto-joins research_tasks here. Historical 9/16 Web tasks were
    generated by an obsolete path; showing or reusing them would make the frontend
    repeat that error. Only ranking/source evidence is published in this layer.
    """
    collection_date = str(monitoring.get('collectionDate') or '')
    rows = []

    for target, spec in _eligible_web_targets(monitoring):
        job = _latest_job_for_target(monitoring, target.get('target_id'))
        if not job:
            continue
        result = _json_obj(job.get('result_json'))
        platform = spec['platform']
        target_key = str(target.get('target_key') or result.get('targetKey') or '').strip()
        ranking_type = str(target.get('ranking_type') or result.get('rankingType') or '').strip()
        category = str(target.get('category') or result.get('category') or 'All').strip() or 'All'
        job_status = str(job.get('status') or target.get('latest_status') or '')
        for rank, raw in _scoped_job_rows(job, spec['topN']):
            title = str(raw.get('title') or '').strip()
            if not title:
                continue
            digest = hashlib.sha1(
                f'{collection_date}|{platform}|{target_key}|{title}'.encode('utf-8')
            ).hexdigest()[:14]
            tags = raw.get('tags')
            if isinstance(tags, list):
                tag_text = ', '.join(str(x).strip() for x in tags if str(x).strip())
            else:
                tag_text = str(tags or '').strip()
            metrics = raw.get('metrics') if isinstance(raw.get('metrics'), dict) else {}
            rows.append({
                'id': f'web-{digest}',
                'collectionDate': collection_date,
                'sourceType': 'OFFICIAL_WEB',
                'platform': platform,
                'sourceName': str(target.get('source_name') or ''),
                'targetKey': target_key,
                'rankingType': ranking_type,
                'category': category,
                'configuredTopN': int(spec['topN']),
                'rank': rank,
                'title': title,
                'tags': tag_text,
                'metrics': metrics,
                'jobStatus': job_status,
                'qualityStatus': {
                    'SUCCEEDED': '采集通过',
                    'PARTIAL': '部分采集',
                    'NEEDS_REVIEW': '待质量复核',
                }.get(job_status, job_status or '未知'),
                'analysisStatus': _analysis_label(job_status),
                'researchTaskStatus': '',
                'research': {},
                'researchConfidence': '',
                'researchSources': [],
                'provisional': bool(result.get('provisional')),
            })

    platform_names = sorted({r['platform'] for r in rows if r.get('platform')})
    target_keys = sorted({(r['platform'], r['targetKey']) for r in rows})
    quality_counts = Counter(r['jobStatus'] for r in rows)
    analysis_counts = Counter(r['analysisStatus'] for r in rows)
    return {
        'available': bool(rows),
        'collectionDate': collection_date,
        'rowCount': len(rows),
        'uniqueTitles': len({_norm_title(r['title']) for r in rows if _norm_title(r['title'])}),
        'platformCount': len(platform_names),
        'platforms': platform_names,
        'targetCount': len(target_keys),
        'qualityCounts': dict(sorted(quality_counts.items())),
        'analysisCounts': dict(sorted(analysis_counts.items())),
        'scopePolicy': 'pilot_scope_semantic_match',
        'rows': sorted(rows, key=lambda r: (r['platform'], r['targetKey'], int(r['rank'] or 999), r['title'])),
    }


def build_live_summary(records, platform_order, clean, split_lane):
    summary = _build_live_summary(records, platform_order, clean, split_lane)
    monitoring = {
        'available': False,
        'collectionDate': '',
        'registry': {},
        'coverage': None,
        'rawCoverage': None,
        'statusCounts': {},
        'targetStatus': [],
        'jobs': [],
    }
    if persistence.configured():
        try:
            remote = persistence.monitoring_status()
            raw_coverage = remote.get('coverage')
            monitoring.update({
                'available': True,
                'collectionDate': str(remote.get('collectionDate') or ''),
                'registry': remote.get('registry') or {},
                'rawCoverage': raw_coverage,
                'targetStatus': remote.get('targetStatus') or [],
                'jobs': remote.get('jobs') or [],
            })
            current_coverage = _build_current_scope_coverage(monitoring)
            monitoring['coverage'] = current_coverage
            monitoring['statusCounts'] = _coverage_status_counts(
                current_coverage,
                remote.get('statusCounts') or {},
            )
        except Exception as exc:
            monitoring['error'] = str(exc)[:1000]
    summary['monitoring'] = monitoring
    summary['webCollection'] = _build_web_collection(monitoring) if monitoring.get('available') else {
        'available': False,
        'collectionDate': '',
        'rowCount': 0,
        'uniqueTitles': 0,
        'platformCount': 0,
        'platforms': [],
        'targetCount': 0,
        'qualityCounts': {},
        'analysisCounts': {},
        'scopePolicy': 'pilot_scope_semantic_match',
        'rows': [],
    }
    return summary


__all__ = ['build_live_summary', 'merge_analysis_records']
