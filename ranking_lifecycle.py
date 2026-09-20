from __future__ import annotations

from drama_identity import normalize_title


def event_counts_as_app_ranking(event: object) -> bool:
    """Whether one historical event consumes first-App-ranking newness.

    Modern records must explicitly say SHORT_DRAMA_APP.
    Legacy pre-contract history has no sourceType/source metadata; repository
    release records confirm those entries came from manually reviewed App ranking
    screenshots, so they remain valid App-ranking baseline facts.

    Explicit Web/social/monitoring events never count as App ranking history.
    """
    if not isinstance(event, dict):
        return False

    source_type = str(event.get('sourceType') or '').strip()
    if source_type:
        return source_type == 'SHORT_DRAMA_APP'

    # Legacy V1/V1.2 ranking history: no sourceType and no source pointer.
    # Do not grant this compatibility rule to newer events carrying a source.
    return not str(event.get('source') or '').strip()


def known_app_ranked_titles(records: list[dict], before_date: str = '') -> list[str]:
    """Return titles previously observed in authoritative App ranking history."""
    out: dict[str, str] = {}

    for record in records:
        if not isinstance(record, dict):
            continue
        title = str(record.get('title') or '').strip()
        norm = normalize_title(title)
        if not title or not norm:
            continue

        for event in record.get('history') or []:
            if not event_counts_as_app_ranking(event):
                continue
            date = str(event.get('date') or '').strip()
            if before_date and (not date or date >= before_date):
                continue
            out.setdefault(norm, title)
            break

    return sorted(out.values())


def app_newness(title: str, prior_app_titles: list[str] | set[str]) -> str:
    prior = {
        normalize_title(item)
        for item in prior_app_titles
        if normalize_title(item)
    }
    norm = normalize_title(title)
    if not norm:
        return 'uncertain'
    return 'old' if norm in prior else 'new'
