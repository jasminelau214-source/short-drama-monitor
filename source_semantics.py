from __future__ import annotations


SECONDARY_SOURCE_TYPES = {
    'OFFICIAL_WEB',
    'OFFICIAL_WEB_PILOT',
    'VIDEO_SOCIAL',
    'DATA_MONITORING',
    'APP_STORE',
    'SEARCH_TREND',
}


def source_semantics_error(payload: object) -> str:
    """Return a blocking error when provenance is promoted to App ranking semantics.

    Source semantics describe where the fact was actually observed. A Web/social/
    monitoring observation may not be relabeled SHORT_DRAMA_APP merely to pass a
    downstream App-ranking contract or E2E test.
    """
    if not isinstance(payload, dict):
        return ''

    declared = str(payload.get('source_type') or 'SHORT_DRAMA_APP').strip()
    evidence = payload.get('evidence') if isinstance(payload.get('evidence'), dict) else {}

    original = str(
        evidence.get('originalSourceType')
        or evidence.get('original_source_type')
        or ''
    ).strip()

    if declared == 'SHORT_DRAMA_APP' and original and original != 'SHORT_DRAMA_APP':
        return f'SOURCE_SEMANTIC_UPGRADE_FORBIDDEN:{original}->SHORT_DRAMA_APP'

    if (
        declared == 'SHORT_DRAMA_APP'
        and evidence.get('shadowFixture') is True
        and original in SECONDARY_SOURCE_TYPES
    ):
        return f'SOURCE_SEMANTIC_UPGRADE_FORBIDDEN:{original}->SHORT_DRAMA_APP'

    return ''
