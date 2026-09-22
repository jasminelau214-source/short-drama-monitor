from __future__ import annotations

import re
from datetime import datetime, timezone

from official_web_collectors import OfficialWebCollectorError, _clean, fetch_html_with_evidence


DEFAULT_URL = 'https://www.moboreels.com/'


def parse_moboreels_popular(document: str, top_n: int = 10) -> list[dict]:
    heading = re.search(
        r'<h2[^>]*class=["\'][^"\']*home-list-title[^"\']*["\'][^>]*>\s*Popular Series\s*</h2>',
        document or '',
        flags=re.I | re.S,
    )
    if not heading:
        raise OfficialWebCollectorError('MOBOREELS_POPULAR_SERIES_NOT_FOUND')

    start = heading.end()
    next_section = re.search(
        r'<div[^>]*class=["\'][^"\']*home-list(?:\s|["\'])',
        (document or '')[start:],
        flags=re.I,
    )
    end = start + next_section.start() if next_section else min(len(document or ''), start + 200000)
    section = (document or '')[start:end]

    raw_titles = re.findall(
        r'<h3[^>]*class=["\'][^"\']*home-list-item-title[^"\']*["\'][^>]*>(.*?)</h3>',
        section,
        flags=re.I | re.S,
    )

    rows = []
    seen = set()
    for raw in raw_titles:
        title = _clean(re.sub(r'<[^>]+>', ' ', raw), 500)
        key = re.sub(r'[^a-z0-9]+', '', title.casefold())
        if not title or not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        rows.append({'rank': len(rows) + 1, 'title': title})
        if len(rows) >= int(top_n):
            break

    return rows


def collect_moboreels_popular(
    *,
    url: str = DEFAULT_URL,
    collection_date: str | None = None,
    top_n: int = 10,
    document: str | None = None,
) -> dict:
    try:
        top_n = int(top_n)
    except (TypeError, ValueError) as exc:
        raise OfficialWebCollectorError(f'INVALID_TOP_N: {top_n!r}') from exc
    if not 1 <= top_n <= 100:
        raise OfficialWebCollectorError(f'INVALID_TOP_N: {top_n}')

    fetch_evidence = {}
    if document is None:
        fetched = fetch_html_with_evidence(url)
        document = str(fetched['document'])
        fetch_evidence = dict(fetched.get('evidence') or {})

    rows = parse_moboreels_popular(document or '', top_n=top_n)
    if len(rows) < top_n:
        raise OfficialWebCollectorError(
            f'MOBOREELS_INCOMPLETE_TOP: expected={top_n} actual={len(rows)}'
        )

    if collection_date is None:
        collection_date = datetime.now(timezone.utc).date().isoformat()

    return {
        'platform': 'MoboReels',
        'source_type': 'OFFICIAL_WEB',
        'source_id': 'officialweb_moboreels',
        'target_key': 'web_popular_series_all',
        'ranking_type': 'Popular Series',
        'category': 'All',
        'collection_method': 'WEB_SCRAPE',
        'collection_date': collection_date,
        'top_n': top_n,
        'batch_complete': len(rows) == top_n,
        'rows': rows,
        'collector_version': 'moboreels-popular-dom-v1',
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'locale': 'en',
        'evidence': {
            'url': url,
            'requestedUrl': url,
            'section': 'Popular Series',
            'row_count': len(rows),
            'semanticVerified': True,
            **fetch_evidence,
        },
        'evidence_persistence': 'URL_AND_PARSED_FACTS',
        'provider': 'official-web-dom',
    }
