"""Compatibility shim for the V2 live publication layer.

The implementation moved to live_observations_v2.py so the existing app import path
remains stable while the publication model expands from fixed Top10/four-platform
logic to generic TopN, dynamic platforms and target-aware ranking observations.

This shim also attaches operational collection coverage from Supabase when the
persistence connector is configured. Ranking facts and collection-health metadata
remain separate: Official Web coverage is never silently promoted into App ranking
history.
"""

from live_observations_v2 import build_live_summary as _build_live_summary
from live_observations_v2 import merge_analysis_records
import persistence


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
            monitoring.update({
                'available': True,
                'collectionDate': str(remote.get('collectionDate') or ''),
                'registry': remote.get('registry') or {},
                'coverage': remote.get('coverage'),
                'statusCounts': remote.get('statusCounts') or {},
                'targetStatus': remote.get('targetStatus') or [],
                'jobs': remote.get('jobs') or [],
            })
        except Exception as exc:
            monitoring['error'] = str(exc)[:1000]
    summary['monitoring'] = monitoring
    return summary


__all__ = ['build_live_summary', 'merge_analysis_records']
