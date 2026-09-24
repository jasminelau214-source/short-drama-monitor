from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

from official_web_collectors import COLLECTION_LOCALE, COLLECTION_REGION, OfficialWebCollectorError, _clean, _unique_text, fetch_html_with_evidence


VOID_TAGS = {
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
    'meta', 'param', 'source', 'track', 'wbr',
}


def _classes(attrs) -> set[str]:
    for key, value in attrs:
        if key == 'class':
            return {x for x in str(value or '').split() if x}
    return set()


def _attr(attrs, key: str) -> str:
    for name, value in attrs:
        if name == key:
            return str(value or '')
    return ''


def _metric_from_text(value: str) -> str:
    text = _clean(value, 200)
    matches = re.findall(r'(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*[KMB]?(?![A-Za-z])', text, flags=re.I)
    if not matches:
        return ''
    return re.sub(r'\s+', '', matches[-1]).upper()


class GoodShortChannelParser(HTMLParser):
    """Parse GoodShort's server-rendered channel cards without browser automation."""

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.items: list[dict] = []
        self._card_depth = 0
        self._card: dict | None = None
        self._capture = ''
        self._capture_depth = 0
        self._buf: list[str] = []

    def _begin_capture(self, field: str):
        self._capture = field
        self._capture_depth = 1
        self._buf = []

    def handle_starttag(self, tag, attrs):
        classes = _classes(attrs)
        counts_depth = tag not in VOID_TAGS

        if tag == 'div' and 'book' in classes and self._card is None:
            self._card_depth = 1
            self._card = {
                'title': '',
                'url': '',
                'tags': [],
                'synopsis': '',
                'episode_count': '',
                'metric': '',
            }
        elif self._card is not None and self._card_depth and counts_depth:
            self._card_depth += 1

        if self._card is None:
            return

        if tag == 'a' and 'book-name' in classes:
            href = _attr(attrs, 'href')
            if href:
                self._card['url'] = urljoin(self.base_url, href)
            self._begin_capture('title')
        elif 'book-tag-item' in classes:
            self._begin_capture('tag')
        elif 'intro' in classes:
            self._begin_capture('synopsis')
        elif 'like' in classes:
            self._begin_capture('metric')
        elif self._capture_depth and counts_depth:
            self._capture_depth += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self._card is None:
            return
        if self._capture_depth:
            self._buf.append(data)
        text = _clean(data, 100)
        if not self._card.get('episode_count'):
            match = re.search(r'\bEP\s*([0-9]{1,4})\b', text, flags=re.I)
            if match:
                self._card['episode_count'] = match.group(1)

    def handle_endtag(self, tag):
        if tag in VOID_TAGS or self._card is None:
            return

        if self._capture_depth:
            self._capture_depth -= 1
            if self._capture_depth == 0:
                value = _clean(' '.join(self._buf), 3000)
                if self._capture == 'title' and value:
                    self._card['title'] = value
                elif self._capture == 'tag' and value:
                    if value not in self._card['tags']:
                        self._card['tags'].append(value)
                elif self._capture == 'synopsis' and value:
                    self._card['synopsis'] = value
                elif self._capture == 'metric' and value:
                    metric = _metric_from_text(value)
                    if metric:
                        self._card['metric'] = metric
                self._capture = ''
                self._buf = []

        if self._card_depth:
            self._card_depth -= 1
            if self._card_depth == 0:
                if self._card.get('title'):
                    self.items.append(self._card)
                self._card = None


def parse_goodshort_channel(document: str, base_url: str = 'https://www.goodshort.com/') -> list[dict]:
    parser = GoodShortChannelParser(base_url)
    parser.feed(document or '')
    parser.close()
    return parser.items


def collect_goodshort_top(
    *,
    url: str = 'https://www.goodshort.com/channel/Top-in-GoodShort',
    collection_date: str | None = None,
    top_n: int = 10,
    document: str | None = None,
) -> dict:
    """Collect the ordered 'Top in GoodShort' official-web shelf.

    This is OFFICIAL_WEB evidence only. It is deliberately not treated as the
    GoodShort App ranking until an App-vs-Web caliber comparison establishes that.
    """
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
    semantic_verified = bool(re.search(r'Top\s+in\s+GoodShort', document or '', flags=re.I))
    items = parse_goodshort_channel(document, url)
    if not items:
        raise OfficialWebCollectorError('GOODSHORT_CHANNEL_EMPTY')
    if len(items) < top_n:
        raise OfficialWebCollectorError(
            f'GOODSHORT_INCOMPLETE_TOP: expected={top_n} actual={len(items)}'
        )

    selected = items[:top_n]
    seen_titles = set()
    rows = []
    for rank, item in enumerate(selected, start=1):
        title = _clean(item.get('title'), 500)
        key = re.sub(r'[^a-z0-9]+', '', title.casefold())
        if not key:
            raise OfficialWebCollectorError(f'GOODSHORT_MISSING_TITLE: #{rank}')
        if key in seen_titles:
            raise OfficialWebCollectorError(f'GOODSHORT_DUPLICATE_TITLE: #{rank} {title}')
        seen_titles.add(key)

        metrics = {}
        if item.get('episode_count'):
            metrics['episode_count'] = str(item['episode_count'])
        if item.get('metric'):
            metrics['plays_display'] = str(item['metric'])

        row = {
            'rank': rank,
            'title': title,
            'tags': _unique_text(item.get('tags') or []),
            'synopsis': _clean(item.get('synopsis'), 3000),
            'metrics': metrics,
        }
        source_url = _clean(item.get('url'), 1000)
        if source_url:
            row['source_url'] = source_url
        rows.append(row)

    if collection_date is None:
        collection_date = datetime.now(timezone.utc).date().isoformat()

    return {
        'platform': 'GoodShort',
        'source_type': 'OFFICIAL_WEB',
        'source_id': 'officialweb_goodshort',
        'target_key': 'web_top_goodshort_pilot',
        'ranking_type': 'Top in GoodShort',
        'category': 'All',
        'collection_method': 'WEB_SCRAPE',
        'collection_date': collection_date,
        'top_n': top_n,
        'batch_complete': len(rows) == top_n,
        'rows': rows,
        'collector_version': 'goodshort-html-v1',
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'locale': COLLECTION_LOCALE,
        'region': COLLECTION_REGION,
        'evidence': {
            'url': url,
            'requestedUrl': url,
            'section': 'Top in GoodShort',
            'row_count': len(rows),
            'server_rendered': True,
            'semanticVerified': semantic_verified,
            **fetch_evidence,
        },
        'evidence_persistence': 'URL_AND_PARSED_FACTS',
        'provider': 'official-web-stdlib',
    }
