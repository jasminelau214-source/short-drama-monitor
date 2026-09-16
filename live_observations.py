"""Compatibility shim for the V2 live publication layer.

The implementation moved to live_observations_v2.py so the existing app import path
remains stable while the publication model expands from fixed Top10/four-platform
logic to generic TopN, dynamic platforms and target-aware ranking observations.
"""

from live_observations_v2 import build_live_summary, merge_analysis_records

__all__ = ['build_live_summary', 'merge_analysis_records']
