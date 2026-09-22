from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

from official_web_collectors import COLLECTION_LOCALE, COLLECTION_REGION, OfficialWebCollectorError, _clean, fetch_html_with_evidence


NETSHORT_URL = 'https://netshort.com/'
FLEXTV_URL = 'https://www.flextv.cc/drama/Top-in-FlexTV'


class JsonLdScriptParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self._capturing = False
        self._buf: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.casefold() != 'script':
            return
        attr = {str(k).casefold(): str(v or '') for k, v in attrs}
        if attr.get('type', '').casefold() == 'application/ld+json':
            self._capturing = True
            self._buf = []

    def handle_data(self, data):
        if self._capturing:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag.casefold() == 'script' and self._capturing:
            self._capturing = False
            raw = ''.join(self._buf).strip()
            if raw:
                self.blocks.append(raw)
            self._buf = []


def _jsonld_blocks(document: str) -> list[dict[str, Any]]:
    parser = JsonLdScriptParser()
    parser.feed(document or '')
    parser.close()
    out: list[dict[str, Any]] = []
    for raw in parser.blocks:
        try:
            value = json.loads(html.unescape(raw))
        except Exception:
            continue
        if isinstance(value, dict):
            out.append(value)
        elif isinstance(value, list):
            out.append({'@graph': value})
    return out


def _find_itemlists(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(value, dict):
        kind = value.get('@type')
        if kind == 'ItemList' or (isinstance(kind, list) and 'ItemList' in kind):
            out.append(value)
        for child in value.values():
            out.extend(_find_itemlists(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(_find_itemlists(child))
    return out


def _strip_markup(value: Any, limit: int = 500) -> str:
    return _clean(re.sub(r'<[^>]+>', ' ', str(value or '')), limit)


def _document_semantic_marker(document: str, expected_name: str) -> bool:
    wanted = _clean(expected_name, 200).casefold()
    if not wanted:
        return False

    title_match = re.search(r'<title[^>]*>(.*?)</title>', document or '', flags=re.I | re.S)
    if title_match and wanted in _strip_markup(title_match.group(1), 500).casefold():
        return True

    for raw in re.findall(r'<h[1-6][^>]*>(.*?)</h[1-6]>', document or '', flags=re.I | re.S):
        if wanted in _strip_markup(raw, 500).casefold():
            return True
    return False


def parse_target_itemlist(document: str, *, expected_name: str, top_n: int) -> tuple[list[dict], dict]:
    expected = _clean(expected_name, 200).casefold()
    candidates = []
    for block in _jsonld_blocks(document):
        for itemlist in _find_itemlists(block):
            elems = itemlist.get('itemListElement')
            if not isinstance(elems, list):
                continue
            name = _clean(itemlist.get('name'), 200)
            named_match = bool(expected and expected in name.casefold())
            candidates.append((named_match, len(elems), name, itemlist))

    if not candidates:
        raise OfficialWebCollectorError('ITEMLIST_NOT_FOUND')

    named = [item for item in candidates if item[0]]
    page_marker = _document_semantic_marker(document, expected_name)

    if named:
        named.sort(reverse=True, key=lambda item: item[1])
        _, _, name, best = named[0]
        semantic_method = 'itemListName'
    elif page_marker and len(candidates) == 1:
        _, _, name, best = candidates[0]
        semantic_method = 'pageTitleOrHeading+singleItemList'
    else:
        names = [x[2] for x in candidates]
        raise OfficialWebCollectorError(
            'TARGET_ITEMLIST_NOT_IDENTIFIED: '
            f'expected={expected_name} candidates={names} pageMarker={page_marker}'
        )

    elems = best.get('itemListElement') or []
    if len(elems) < top_n:
        raise OfficialWebCollectorError(
            f'ITEMLIST_INCOMPLETE_TOP: expected={top_n} actual={len(elems)}'
        )

    rows = []
    seen_titles = set()
    for idx, entry in enumerate(elems[:top_n], start=1):
        if not isinstance(entry, dict):
            raise OfficialWebCollectorError(f'ITEMLIST_INVALID_ENTRY: #{idx}')
        item = entry.get('item') if isinstance(entry.get('item'), dict) else entry
        title = _clean(item.get('name') or entry.get('name'), 500)
        key = re.sub(r'[^a-z0-9]+', '', title.casefold())
        if not title or not key:
            raise OfficialWebCollectorError(f'ITEMLIST_MISSING_TITLE: #{idx}')
        if key in seen_titles:
            raise OfficialWebCollectorError(f'ITEMLIST_DUPLICATE_TITLE: #{idx} {title}')
        seen_titles.add(key)

        try:
            rank = int(entry.get('position') or idx)
        except (TypeError, ValueError):
            rank = idx

        row = {'rank': rank, 'title': title}
        source_url = _clean(item.get('url') or entry.get('url') or item.get('@id'), 1200)
        if source_url:
            row['source_url'] = source_url
        rows.append(row)

    rows.sort(key=lambda item: int(item.get('rank') or 999))
    return rows, {
        'structuredData': 'JSON-LD ItemList',
        'itemListName': name,
        'itemListCount': len(candidates),
        'semanticVerified': True,
        'semanticMethod': semantic_method,
        'semanticExpected': expected_name,
        'pageSemanticMarker': page_marker,
    }


def _collect_itemlist_platform(
    *,
    platform: str,
    url: str,
    source_id: str,
    target_key: str,
    ranking_type: str,
    collection_date: str | None,
    top_n: int,
    document: str | None,
    collector_version: str,
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

    rows, parser_evidence = parse_target_itemlist(
        document or '',
        expected_name=ranking_type,
        top_n=top_n,
    )

    if collection_date is None:
        collection_date = datetime.now(timezone.utc).date().isoformat()

    return {
        'platform': platform,
        'source_type': 'OFFICIAL_WEB',
        'source_id': source_id,
        'target_key': target_key,
        'ranking_type': ranking_type,
        'category': 'All',
        'collection_method': 'WEB_SCRAPE',
        'collection_date': collection_date,
        'top_n': top_n,
        'batch_complete': len(rows) == top_n,
        'rows': rows,
        'collector_version': collector_version,
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'locale': COLLECTION_LOCALE,
        'region': COLLECTION_REGION,
        'evidence': {
            'url': url,
            'requestedUrl': url,
            'row_count': len(rows),
            **parser_evidence,
            **fetch_evidence,
        },
        'evidence_persistence': 'URL_AND_PARSED_FACTS',
        'provider': 'official-web-jsonld',
    }


def collect_netshort_trending(
    *,
    url: str = NETSHORT_URL,
    collection_date: str | None = None,
    top_n: int = 10,
    document: str | None = None,
) -> dict:
    return _collect_itemlist_platform(
        platform='NetShort',
        url=url,
        source_id='officialweb_netshort',
        target_key='web_trending_now_all',
        ranking_type='Trending Now',
        collection_date=collection_date,
        top_n=top_n,
        document=document,
        collector_version='netshort-jsonld-v1',
    )


def collect_flextv_top(
    *,
    url: str = FLEXTV_URL,
    collection_date: str | None = None,
    top_n: int = 10,
    document: str | None = None,
) -> dict:
    return _collect_itemlist_platform(
        platform='FlexTV',
        url=url,
        source_id='officialweb_flextv',
        target_key='web_top_in_flextv_all',
        ranking_type='Top in FlexTV',
        collection_date=collection_date,
        top_n=top_n,
        document=document,
        collector_version='flextv-jsonld-v1',
    )
