"""Compatibility shim for the V2 live publication layer.

The implementation moved to live_observations_v2.py so the existing app import path
remains stable while the publication model expands from fixed Top10/four-platform
logic to generic TopN, dynamic platforms and target-aware ranking observations.

This shim also attaches operational collection coverage from Supabase when the
persistence connector is configured. App ranking facts and Official Web evidence
remain separate publication layers: Web observations are exposed for review and
content analysis, but are never silently promoted into App ranking history.

It also preserves historical deep-research overrides across record-ID migrations.
V1 dynamic IDs used platform + normalized title; V2 IDs use platform + targetKey +
normalized title. Override lookup therefore checks both stable historical identities
before falling back to the record's current ID, so completed research is not lost
when the publication-layer ID format changes.
"""

import hashlib
import json
import re
from collections import Counter

from drama_identity import normalize_title
from runtime_ui_patch import main as _apply_runtime_ui_patch

# app.py imports this module before reading index.html, so apply the idempotent UI
# migration here. This keeps the deployed service compatible with its existing
# Render start command while the dashboard moves away from hard-coded Top10 copy.
_apply_runtime_ui_patch()

from live_observations_v2 import build_live_summary as _build_live_summary
from live_observations_v2 import merge_analysis_records as _merge_analysis_records
import persistence
import test_snapshot


def _platform_slug(platform):
    return ''.join(ch.lower() if ch.isalnum() else '-' for ch in str(platform or '')).strip('-') or 'platform'


_norm_title = normalize_title


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
    """Expose target-level status counts, not raw job-run counts."""
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


def _analysis_label(job_status, task_status):
    if job_status == 'PARTIAL':
        return '待补采/复核'
    if job_status == 'NEEDS_REVIEW':
        return '待质量复核'
    if job_status != 'SUCCEEDED':
        return '暂不进入内容分析'
    return {
        'PENDING': '待内容分析',
        'RESEARCHING': '分析中',
        'COMPLETE': '内容分析完成',
        'NEEDS_GPT': '需GPT复核',
        'REVIEW_REQUIRED': '需人工复核',
        'FAILED': '分析失败',
    }.get(task_status, '待进入内容分析')


def _build_web_collection(monitoring):
    """Build a separate Official Web evidence layer from each target's latest run.

    This intentionally includes PARTIAL/NEEDS_REVIEW rows in the collection view so
    the operator can see all captured evidence, while only SUCCEEDED rows are eligible
    for content-research tasks.
    """
    collection_date = str(monitoring.get('collectionDate') or '')
    targets = [
        x for x in (monitoring.get('targetStatus') or [])
        if str(x.get('source_group') or '') == 'OFFICIAL_WEB'
        and (not collection_date or str(x.get('latest_collection_date') or '') == collection_date)
    ]
    jobs = monitoring.get('jobs') or []

    task_by_key = {}
    if test_snapshot.available():
        tasks = test_snapshot.list_research_tasks(limit=500)
    else:
        try:
            tasks = persistence.list_research_tasks(limit=500)
        except Exception:
            tasks = []
    for task in tasks:
        if str(task.get('collection_date') or '') != collection_date:
            continue
        key = (
            str(task.get('platform') or '').strip(),
            _norm_title(task.get('title') or task.get('normalized_title')),
        )
        if key[0] and key[1]:
            task_by_key[key] = task

    rows = []
    selected_jobs = []
    for target in targets:
        target_id = str(target.get('target_id') or '')
        candidates = [
            j for j in jobs
            if str(j.get('target_id') or '') == target_id
            and (not collection_date or str(j.get('collection_date') or '') == collection_date)
        ]
        if not candidates:
            continue
        job = max(candidates, key=lambda j: (str(j.get('finished_at') or ''), str(j.get('created_at') or '')))
        selected_jobs.append(job)
        result = _json_obj(job.get('result_json'))
        platform = str(result.get('platform') or '').strip()
        if not platform:
            platform = re.sub(r'\s+Official Web$', '', str(target.get('source_name') or '').strip())
        target_key = str(target.get('target_key') or result.get('targetKey') or '').strip()
        ranking_type = str(target.get('ranking_type') or result.get('rankingType') or '').strip()
        category = str(target.get('category') or result.get('category') or 'All').strip() or 'All'
        job_status = str(job.get('status') or target.get('latest_job_status') or '')
        raw_rows = result.get('rows') if isinstance(result.get('rows'), list) else []
        for index, raw in enumerate(raw_rows):
            if not isinstance(raw, dict):
                continue
            title = str(raw.get('title') or '').strip()
            if not title:
                continue
            norm = _norm_title(title)
            task = task_by_key.get((platform, norm)) if norm else None
            task_status = str((task or {}).get('status') or '')
            research = _json_obj((task or {}).get('research_json'))
            rank = _row_rank(raw, index)
            digest = hashlib.sha1(f'{collection_date}|{platform}|{target_key}|{title}'.encode('utf-8')).hexdigest()[:14]
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
                'configuredTopN': int(target.get('configured_top_n') or len(raw_rows) or 0),
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
                'analysisStatus': _analysis_label(job_status, task_status),
                'researchTaskStatus': task_status,
                'research': research,
                'researchConfidence': str((task or {}).get('confidence') or ''),
                'researchSources': (task or {}).get('sources') if isinstance((task or {}).get('sources'), list) else [],
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
        'rows': sorted(rows, key=lambda r: (r['platform'], r['targetKey'], int(r['rank'] or 999), r['title'])),
    }


def build_live_summary(records, platform_order, clean, split_lane):
    summary = _build_live_summary(records, platform_order, clean, split_lane)
    monitoring = {
        'available': False,
        'collectionDate': '',
        'registry': {},
        'coverage': None,
        'statusCounts': {},
        'targetStatus': [],
        'jobs': [],
    }
    if test_snapshot.available():
        remote = test_snapshot.monitoring_status()
        coverage = remote.get('coverage')
        monitoring.update({
            'available': True,
            'collectionDate': str(remote.get('collectionDate') or ''),
            'registry': remote.get('registry') or {},
            'coverage': coverage,
            'statusCounts': _coverage_status_counts(coverage, remote.get('statusCounts') or {}),
            'targetStatus': remote.get('targetStatus') or [],
            'jobs': remote.get('jobs') or [],
            'sourceMode': 'READ_ONLY_PRODUCTION_SNAPSHOT',
        })
    elif persistence.configured():
        try:
            remote = persistence.monitoring_status()
            coverage = remote.get('coverage')
            monitoring.update({
                'available': True,
                'collectionDate': str(remote.get('collectionDate') or ''),
                'registry': remote.get('registry') or {},
                'coverage': coverage,
                'statusCounts': _coverage_status_counts(coverage, remote.get('statusCounts') or {}),
                'targetStatus': remote.get('targetStatus') or [],
                'jobs': remote.get('jobs') or [],
            })
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
        'rows': [],
    }
    return summary


__all__ = ['build_live_summary', 'merge_analysis_records']
