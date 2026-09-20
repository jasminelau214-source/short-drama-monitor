from __future__ import annotations

from datetime import datetime, timezone

from official_web_collectors import (
    OfficialWebCollectorError,
    _clean,
    fetch_html_with_evidence,
    parse_next_data,
)


DEFAULT_URL = 'https://www.reelshort.com/shelf/top-short-movies-dramas-51001122'


def collect_reelshort_top(
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

    next_data = parse_next_data(document)
    page_props = next_data.get('props', {}).get('pageProps', {})
    if not isinstance(page_props, dict):
        raise OfficialWebCollectorError('REELSHORT_PAGE_PROPS_NOT_FOUND')

    shelf_name = _clean(page_props.get('shelfName'), 120)
    semantic_verified = shelf_name.casefold() == 'top'
    if not semantic_verified:
        raise OfficialWebCollectorError(
            f'REELSHORT_SHELF_MISMATCH: expected=TOP actual={shelf_name or "(blank)"}'
        )

    raw_list = page_props.get('list')
    if not isinstance(raw_list, list):
        raise OfficialWebCollectorError('REELSHORT_LIST_NOT_FOUND')
    if len(raw_list) < top_n:
        raise OfficialWebCollectorError(
            f'REELSHORT_INCOMPLETE_TOP: expected={top_n} actual={len(raw_list)}'
        )

    rows = []
    seen = set()
    for index, item in enumerate(raw_list[:top_n], start=1):
        if not isinstance(item, dict):
            raise OfficialWebCollectorError(f'REELSHORT_INVALID_ITEM: #{index}')
        title = _clean(item.get('book_title') or item.get('bookTitle') or item.get('title'), 500)
        key = ''.join(ch for ch in title.casefold() if ch.isalnum())
        if not title or not key:
            raise OfficialWebCollectorError(f'REELSHORT_MISSING_TITLE: #{index}')
        if key in seen:
            raise OfficialWebCollectorError(f'REELSHORT_DUPLICATE_TITLE: #{index} {title}')
        seen.add(key)

        row = {
            'rank': index,
            'title': title,
        }
        source_url = _clean(
            item.get('book_url')
            or item.get('bookUrl')
            or item.get('share_url')
            or item.get('shareUrl'),
            1200,
        )
        if source_url:
            row['source_url'] = source_url
        rows.append(row)

    if collection_date is None:
        collection_date = datetime.now(timezone.utc).date().isoformat()

    return {
        'platform': 'ReelShort',
        'source_type': 'OFFICIAL_WEB',
        'source_id': 'officialweb_reelshort',
        'target_key': 'web_top_shelf_all',
        'ranking_type': 'TOP',
        'category': 'All',
        'collection_method': 'WEB_SCRAPE',
        'collection_date': collection_date,
        'top_n': top_n,
        'batch_complete': len(rows) == top_n,
        'rows': rows,
        'collector_version': 'reelshort-nextdata-v1',
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'locale': 'en',
        'evidence': {
            'url': url,
            'requestedUrl': url,
            'shelfName': shelf_name,
            'total': page_props.get('total'),
            'row_count': len(rows),
            'semanticVerified': semantic_verified,
            'nextBuildId': _clean(next_data.get('buildId'), 120),
            **fetch_evidence,
        },
        'evidence_persistence': 'URL_AND_PARSED_FACTS',
        'provider': 'official-web-nextdata',
    }
