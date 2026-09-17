"""Compatibility shim for the V2 live publication layer.

The implementation moved to live_observations_v2.py so the existing app import path
remains stable while the publication model expands from fixed Top10/four-platform
logic to generic TopN, dynamic platforms and target-aware ranking observations.

This shim also attaches operational collection coverage from Supabase when the
persistence connector is configured. Ranking facts and collection-health metadata
remain separate: Official Web coverage is never silently promoted into App ranking
history.

It also preserves historical deep-research overrides across record-ID migrations.
V1 dynamic IDs used platform + normalized title; V2 IDs use platform + targetKey +
normalized title. Override lookup therefore checks both stable historical identities
before falling back to the record's current ID, so completed research is not lost
when the publication-layer ID format changes.
"""

import hashlib
import json

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
    """Expose target-level status counts, not raw job-run counts.

    collection_jobs can contain retries/reruns for one target on the same date. The
    dashboard describes target coverage, so its status totals must come from the
    deduplicated daily coverage view whenever that view is available.
    """
    if not isinstance(coverage, dict):
        return fallback if isinstance(fallback, dict) else {}
    return {
        'total': int(coverage.get('target_jobs') or 0),
        'succeeded': int(coverage.get('succeeded_targets') or 0),
        'partial': int(coverage.get('partial_targets') or 0),
        'needsReview': int(coverage.get('review_targets') or 0),
        'other': int(coverage.get('other_targets') or 0),
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
    if persistence.configured():
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
    return summary


__all__ = ['build_live_summary', 'merge_analysis_records']
