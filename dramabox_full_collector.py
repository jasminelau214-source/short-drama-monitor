from __future__ import annotations

import re
from datetime import datetime, timezone

from official_web_collectors import OfficialWebCollectorError, collect_dramabox_channel


def _page_url(channel: str, page: int) -> str:
    slug = str(channel or '').strip().strip('/')
    if not slug:
        raise OfficialWebCollectorError('INVALID_CHANNEL')
    base = f'https://www.dramaboxdb.com/channel/{slug}'
    return base if page == 1 else f'{base}/{page}'


def _title_key(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(value or '').casefold())


def collect_dramabox_channel_all_pages(
    *,
    channel: str = 'trending',
    collection_date: str | None = None,
    documents: dict[int, str] | None = None,
    max_pages: int = 10,
) -> dict:
    """Collect every server-rendered pagination page for one DramaBox web channel.

    The returned `rank` is the global ordered position across the channel pages.
    This is still OFFICIAL_WEB evidence and remains separate from the App ranking.
    `documents` is only for deterministic tests; production normally fetches live URLs.
    """
    if collection_date is None:
        collection_date = datetime.now(timezone.utc).date().isoformat()

    def collect_page(page: int) -> dict:
        kwargs = {
            'channel': channel,
            'url': _page_url(channel, page),
            'collection_date': collection_date,
            'top_n': None,
        }
        if documents is not None:
            if page not in documents:
                raise OfficialWebCollectorError(f'MISSING_TEST_DOCUMENT: page={page}')
            kwargs['document'] = documents[page]
        return collect_dramabox_channel(**kwargs)

    first = collect_page(1)
    try:
        total_pages = int(first.get('evidence', {}).get('pages') or 1)
    except (TypeError, ValueError) as exc:
        raise OfficialWebCollectorError('DRAMABOX_INVALID_PAGE_COUNT') from exc
    if not 1 <= total_pages <= max_pages:
        raise OfficialWebCollectorError(
            f'DRAMABOX_PAGE_COUNT_OUT_OF_RANGE: pages={total_pages} max={max_pages}'
        )

    page_payloads = [first]
    for page in range(2, total_pages + 1):
        payload = collect_page(page)
        observed_page = payload.get('evidence', {}).get('page')
        try:
            observed_page = int(observed_page)
        except (TypeError, ValueError) as exc:
            raise OfficialWebCollectorError(f'DRAMABOX_INVALID_PAGE_NO: page={page}') from exc
        if observed_page != page:
            raise OfficialWebCollectorError(
                f'DRAMABOX_PAGE_MISMATCH: requested={page} observed={observed_page}'
            )
        observed_pages = payload.get('evidence', {}).get('pages')
        if observed_pages not in (None, '') and int(observed_pages) != total_pages:
            raise OfficialWebCollectorError(
                f'DRAMABOX_PAGE_TOTAL_CHANGED: first={total_pages} page{page}={observed_pages}'
            )
        if payload.get('ranking_type') != first.get('ranking_type'):
            raise OfficialWebCollectorError(
                f'DRAMABOX_CHANNEL_CHANGED: first={first.get("ranking_type")} page{page}={payload.get("ranking_type")}'
            )
        page_payloads.append(payload)

    combined_rows = []
    seen_urls = set()
    seen_titles = set()
    page_counts = []
    page_urls = []
    page_fetch_evidence = []
    for page, payload in enumerate(page_payloads, start=1):
        rows = payload.get('rows') or []
        if not rows:
            raise OfficialWebCollectorError(f'DRAMABOX_EMPTY_PAGE: page={page}')
        page_counts.append(len(rows))
        page_urls.append(_page_url(channel, page))
        evidence = payload.get('evidence') if isinstance(payload.get('evidence'), dict) else {}
        if evidence.get('httpStatus') is not None or evidence.get('pageUrl') or evidence.get('fetchedAt'):
            page_fetch_evidence.append({
                'requestedUrl': evidence.get('requestedUrl') or _page_url(channel, page),
                'httpStatus': evidence.get('httpStatus'),
                'pageUrl': evidence.get('pageUrl'),
                'fetchedAt': evidence.get('fetchedAt'),
                'semanticVerified': evidence.get('semanticVerified') is True,
            })
        for item in rows:
            source_url = str(item.get('source_url') or '').strip()
            title = str(item.get('title') or '').strip()
            url_key = source_url.casefold()
            title_key = _title_key(title)
            if not url_key and not title_key:
                raise OfficialWebCollectorError(f'DRAMABOX_EMPTY_ITEM_KEY: page={page}')
            if (url_key and url_key in seen_urls) or (title_key and title_key in seen_titles):
                raise OfficialWebCollectorError(
                    f'DRAMABOX_DUPLICATE_ACROSS_PAGES: page={page} title={title}'
                )
            if url_key:
                seen_urls.add(url_key)
            if title_key:
                seen_titles.add(title_key)
            row = dict(item)
            row['rank'] = len(combined_rows) + 1
            row['source_page'] = page
            combined_rows.append(row)

    result = dict(first)
    result.update({
        'top_n': len(combined_rows),
        'batch_complete': True,
        'rows': combined_rows,
        'collector_version': 'dramabox-nextdata-full-v1',
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'evidence': {
            **(first.get('evidence') or {}),
            'url': _page_url(channel, 1),
            'pages': total_pages,
            'page_urls': page_urls,
            'page_item_counts': page_counts,
            'row_count': len(combined_rows),
            'pagination_mode': 'path_page_number',
            **({'pageFetchEvidence': page_fetch_evidence} if page_fetch_evidence else {}),
        },
    })
    return result
