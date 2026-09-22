from __future__ import annotations

from drama_identity import normalize_title


def select_same_platform_record(records: list[dict], *, platform: str, title: str) -> dict | None:
    """Select a writeback target only within the task platform.

    Title identity alone never authorizes cross-platform writeback. Historical
    ranking events count as same-platform evidence when the record's latest app
    field no longer reflects the requested platform.
    """
    platform = str(platform or '').strip()
    norm = normalize_title(title)
    if not platform or not norm:
        return None

    candidates = [
        record for record in records
        if isinstance(record, dict) and normalize_title(record.get('title')) == norm
    ]

    for record in candidates:
        if str(record.get('app') or '').strip() == platform:
            return record

    for record in candidates:
        history = record.get('history') if isinstance(record.get('history'), list) else []
        if any(
            isinstance(event, dict) and str(event.get('app') or '').strip() == platform
            for event in history
        ):
            return record

    return None
