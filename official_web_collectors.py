from __future__ import annotations

import html
import json
import re
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin


DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36'
)
VOID_TAGS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

COLLECTION_LANGUAGE = 'English'
COLLECTION_LOCALE = 'en-US'
COLLECTION_REGION = 'US'
COLLECTION_REGION_POLICY = 'US_WHEN_FILTER_AVAILABLE'
DEFAULT_ACCEPT_LANGUAGE = 'en-US,en;q=0.9'


class OfficialWebCollectorError(RuntimeError):
    pass


def _clean(value: object, limit: int = 6000) -> str:
    text = html.unescape(str(value or ''))
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:limit]


def _class_tokens(attrs) -> set[str]:
    for key, value in attrs:
        if key == 'class':
            return {x for x in str(value or '').split() if x}
    return set()


def _attr(attrs, key: str) -> str:
    for name, value in attrs:
        if name == key:
            return str(value or '')
    return ''


def _unique_text(values) -> list[str]:
    out: list[str] = []
    seen = set()
    for value in values or []:
        text = _clean(value, 160)
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        out.append(text)
    return out


class ShortMaxSectionParser(HTMLParser):
    """Parse rendered/SSR ShortMax HTML into named content sections."""

    def __init__(self, base_url: str = 'https://www.shorttv.live/'):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.sections: dict[str, list[dict]] = {}
        self._section_name = ''
        self._section_title_depth = 0
        self._section_title_buf: list[str] = []
        self._card_depth = 0
        self._card: dict | None = None
        self._capture_field = ''
        self._capture_depth = 0
        self._capture_buf: list[str] = []
        self._tag_span_depth = 0
        self._tag_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        classes = _class_tokens(attrs)
        counts_depth = tag not in VOID_TAGS

        if 'section-title' in classes:
            self._section_title_depth = 1
            self._section_title_buf = []
        elif self._section_title_depth and counts_depth:
            self._section_title_depth += 1

        if 'drama-card' in classes:
            self._card_depth = 1
            self._card = {'title': '', 'tags': [], 'synopsis': '', 'url': '', 'episodeUrl': ''}
        elif self._card_depth and counts_depth:
            self._card_depth += 1

        if self._card is not None:
            href = _attr(attrs, 'href')
            if tag == 'a' and href:
                absolute = urljoin(self.base_url, href)
                if 'card-title-layout' in classes or 'overlay-title' in classes:
                    self._card['url'] = absolute
                elif 'card-image' in classes or 'card-overlay' in classes:
                    if not self._card.get('episodeUrl'):
                        self._card['episodeUrl'] = absolute

            if 'card-title' in classes:
                self._capture_field = 'title'
                self._capture_depth = 1
                self._capture_buf = []
            elif 'overlay-description' in classes:
                self._capture_field = 'synopsis'
                self._capture_depth = 1
                self._capture_buf = []
            elif 'overlay-tags' in classes:
                self._capture_field = 'tags'
                self._capture_depth = 1
                self._capture_buf = []
            elif self._capture_depth and counts_depth:
                self._capture_depth += 1

            if self._capture_field == 'tags' and tag == 'span':
                self._tag_span_depth = 1
                self._tag_buf = []
            elif self._tag_span_depth and counts_depth:
                self._tag_span_depth += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self._section_title_depth:
            self._section_title_buf.append(data)
        if self._capture_depth:
            self._capture_buf.append(data)
        if self._tag_span_depth:
            self._tag_buf.append(data)

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return

        if self._tag_span_depth:
            self._tag_span_depth -= 1
            if self._tag_span_depth == 0 and self._card is not None:
                value = _clean(' '.join(self._tag_buf), 120)
                if value and value not in self._card['tags']:
                    self._card['tags'].append(value)
                self._tag_buf = []

        if self._capture_depth:
            self._capture_depth -= 1
            if self._capture_depth == 0 and self._card is not None:
                value = _clean(' '.join(self._capture_buf), 3000)
                if self._capture_field == 'title' and value:
                    self._card['title'] = value
                elif self._capture_field == 'synopsis' and value:
                    self._card['synopsis'] = value
                elif self._capture_field == 'tags' and value and not self._card['tags']:
                    self._card['tags'] = [value]
                self._capture_field = ''
                self._capture_buf = []

        if self._card_depth:
            self._card_depth -= 1
            if self._card_depth == 0:
                if self._section_name and self._card and self._card.get('title'):
                    self.sections.setdefault(self._section_name, []).append(self._card)
                self._card = None

        if self._section_title_depth:
            self._section_title_depth -= 1
            if self._section_title_depth == 0:
                value = _clean(' '.join(self._section_title_buf), 160)
                if value:
                    self._section_name = value
                    self.sections.setdefault(value, [])
                self._section_title_buf = []


class NextDataParser(HTMLParser):
    """Extract a Next.js __NEXT_DATA__ JSON script without brittle regex over HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self._capturing = False
        self._buf: list[str] = []
        self.value = ''

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and _attr(attrs, 'id') == '__NEXT_DATA__':
            self._capturing = True
            self._buf = []

    def handle_data(self, data):
        if self._capturing:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self._capturing:
            self._capturing = False
            self.value = ''.join(self._buf).strip()


def parse_shortmax_sections(document: str, base_url: str = 'https://www.shorttv.live/') -> dict[str, list[dict]]:
    parser = ShortMaxSectionParser(base_url)
    parser.feed(document or '')
    parser.close()
    return parser.sections


def parse_next_data(document: str) -> dict:
    parser = NextDataParser()
    parser.feed(document or '')
    parser.close()
    if not parser.value:
        raise OfficialWebCollectorError('NEXT_DATA_NOT_FOUND')
    try:
        value = json.loads(parser.value)
    except json.JSONDecodeError as exc:
        raise OfficialWebCollectorError(f'NEXT_DATA_INVALID_JSON: {exc}') from exc
    if not isinstance(value, dict):
        raise OfficialWebCollectorError('NEXT_DATA_NOT_OBJECT')
    return value


def fetch_html_with_evidence(url: str, timeout: int = 25) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            'User-Agent': DEFAULT_USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': DEFAULT_ACCEPT_LANGUAGE,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or 'utf-8'
            document = response.read().decode(charset, errors='replace')
            status = int(getattr(response, 'status', None) or response.getcode() or 0)
            final_url = str(response.geturl() or '')
            return {
                'document': document,
                'evidence': {
                    'requestedUrl': url,
                    'httpStatus': status,
                    'pageUrl': final_url,
                    'fetchedAt': datetime.now(timezone.utc).isoformat(),
                    'requestedLanguage': COLLECTION_LANGUAGE,
                    'requestedLocale': COLLECTION_LOCALE,
                    'requestedRegion': COLLECTION_REGION,
                    'regionPolicy': COLLECTION_REGION_POLICY,
                },
            }
    except Exception as exc:
        raise OfficialWebCollectorError(f'WEB_FETCH_FAILED: {url}: {exc}') from exc


def fetch_html(url: str, timeout: int = 25) -> str:
    """Backward-compatible document-only fetch wrapper."""
    return str(fetch_html_with_evidence(url, timeout=timeout)['document'])


def _section_key(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', value.casefold()).strip()


def _find_section(sections: dict[str, list[dict]], requested: str) -> tuple[str, list[dict]]:
    wanted = _section_key(requested)
    exact = [(name, rows) for name, rows in sections.items() if _section_key(name) == wanted]
    if exact:
        return exact[0]
    candidates = [(name, rows) for name, rows in sections.items() if wanted and wanted in _section_key(name)]
    if len(candidates) == 1:
        return candidates[0]
    available = ', '.join(sections.keys()) or '(none)'
    raise OfficialWebCollectorError(f'SECTION_NOT_FOUND: {requested}; available={available}')


def collect_shortmax(
    *,
    url: str = 'https://www.shorttv.live/',
    section: str = 'Most Popular',
    collection_date: str | None = None,
    top_n: int | None = None,
    document: str | None = None,
) -> dict:
    """Collect a ShortMax official-web section into the generic collector schema."""
    fetch_evidence = {}
    if document is None:
        fetched = fetch_html_with_evidence(url)
        document = str(fetched['document'])
        fetch_evidence = dict(fetched.get('evidence') or {})
    sections = parse_shortmax_sections(document, url)
    section_name, cards = _find_section(sections, section)
    semantic_verified = _section_key(section_name) == _section_key(section)
    if not cards:
        raise OfficialWebCollectorError(f'EMPTY_SECTION: {section_name}')

    if top_n is None:
        top_n = len(cards)
    try:
        top_n = int(top_n)
    except (TypeError, ValueError) as exc:
        raise OfficialWebCollectorError(f'INVALID_TOP_N: {top_n!r}') from exc
    if not 1 <= top_n <= 100:
        raise OfficialWebCollectorError(f'INVALID_TOP_N: {top_n}')
    if len(cards) < top_n:
        raise OfficialWebCollectorError(f'INCOMPLETE_SECTION: {section_name}: expected={top_n} actual={len(cards)}')

    selected = cards[:top_n]
    if collection_date is None:
        collection_date = datetime.now(timezone.utc).date().isoformat()

    section_slug = re.sub(r'[^a-z0-9]+', '_', _section_key(section_name)).strip('_') or 'section'
    if _section_key(section_name).startswith('most popular'):
        target_key = 'web_most_popular_all'
        category = 'All'
    else:
        target_key = f'web_category_{section_slug}'
        category = section_name

    rows = []
    for index, card in enumerate(selected, start=1):
        row = {
            'rank': index,
            'title': _clean(card.get('title'), 500),
            'tags': list(card.get('tags') or []),
            'synopsis': _clean(card.get('synopsis'), 3000),
        }
        url_value = _clean(card.get('url'), 1000)
        episode_url = _clean(card.get('episodeUrl'), 1000)
        if url_value:
            row['source_url'] = url_value
        if episode_url:
            row['episode_url'] = episode_url
        rows.append(row)

    return {
        'platform': 'ShortMax',
        'source_type': 'OFFICIAL_WEB',
        'source_id': 'officialweb_shortmax',
        'target_key': target_key,
        'ranking_type': section_name,
        'category': category,
        'collection_method': 'WEB_SCRAPE',
        'collection_date': collection_date,
        'top_n': top_n,
        'batch_complete': len(rows) == top_n,
        'rows': rows,
        'collector_version': 'shortmax-web-v1',
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'locale': COLLECTION_LOCALE,
        'region': COLLECTION_REGION,
        'evidence': {
            'url': url,
            'requestedUrl': url,
            'section': section_name,
            'row_count': len(rows),
            'semanticVerified': semantic_verified,
            **fetch_evidence,
        },
        'evidence_persistence': 'URL_AND_PARSED_FACTS',
        'provider': 'official-web-stdlib',
    }


def collect_dramabox_channel(
    *,
    channel: str = 'trending',
    url: str | None = None,
    collection_date: str | None = None,
    top_n: int | None = None,
    document: str | None = None,
) -> dict:
    """Collect one DramaBox official web channel from its server-rendered Next.js data.

    The channel route exposes ordered items plus platform-native synopsis, tags,
    F-Drama/M-Drama type, view count, rating and episode count. This is web-channel
    evidence and is not automatically treated as equivalent to the App ranking.
    """
    channel_slug = re.sub(r'[^a-z0-9]+', '-', str(channel or '').casefold()).strip('-')
    if not channel_slug:
        raise OfficialWebCollectorError('INVALID_CHANNEL')
    if url is None:
        url = f'https://www.dramaboxdb.com/channel/{channel_slug}'
    fetch_evidence = {}
    if document is None:
        fetched = fetch_html_with_evidence(url)
        document = str(fetched['document'])
        fetch_evidence = dict(fetched.get('evidence') or {})

    next_data = parse_next_data(document)
    try:
        page_props = next_data['props']['pageProps']
        more_data = page_props['moreData']
        items = more_data['items']
    except (KeyError, TypeError) as exc:
        raise OfficialWebCollectorError('DRAMABOX_MORE_DATA_NOT_FOUND') from exc
    if not isinstance(items, list) or not items:
        raise OfficialWebCollectorError('DRAMABOX_CHANNEL_EMPTY')

    if top_n is None:
        top_n = len(items)
    try:
        top_n = int(top_n)
    except (TypeError, ValueError) as exc:
        raise OfficialWebCollectorError(f'INVALID_TOP_N: {top_n!r}') from exc
    if not 1 <= top_n <= 100:
        raise OfficialWebCollectorError(f'INVALID_TOP_N: {top_n}')
    if len(items) < top_n:
        raise OfficialWebCollectorError(f'INCOMPLETE_CHANNEL: {channel_slug}: expected={top_n} actual={len(items)}')

    if collection_date is None:
        collection_date = datetime.now(timezone.utc).date().isoformat()

    rows = []
    for index, item in enumerate(items[:top_n], start=1):
        if not isinstance(item, dict):
            raise OfficialWebCollectorError(f'DRAMABOX_INVALID_ITEM: #{index}')
        title = _clean(item.get('bookName') or item.get('name'), 500)
        if not title:
            raise OfficialWebCollectorError(f'DRAMABOX_MISSING_TITLE: #{index}')
        book_id = _clean(item.get('bookId') or item.get('action'), 80)
        lower = _clean(item.get('bookNameLower'), 300)
        source_url = f'https://www.dramaboxdb.com/movie/{book_id}/{lower}' if book_id and lower else url
        tags = _unique_text(
            list(item.get('typeOneNames') or [])
            + list(item.get('typeTwoNames') or [])
            + list(item.get('tags') or [])
        )
        metrics = {}
        for key, value in (
            ('views', item.get('viewCount')),
            ('views_display', item.get('viewCountDisplay')),
            ('rating', item.get('ratings')),
            ('episode_count', item.get('chapterCount')),
        ):
            if value not in (None, ''):
                metrics[key] = str(value)
        rows.append({
            'rank': index,
            'title': title,
            'tags': tags,
            'synopsis': _clean(item.get('introduction'), 3000),
            'source_url': source_url,
            'metrics': metrics,
        })

    observed_locale = _clean(page_props.get('locale'), 40)
    if observed_locale and not observed_locale.casefold().startswith('en'):
        raise OfficialWebCollectorError(
            f'DRAMABOX_LOCALE_MISMATCH: expected={COLLECTION_LOCALE} actual={observed_locale}'
        )

    display_name = _clean(more_data.get('name'), 120) or channel_slug.replace('-', ' ').title()
    ranking_type = {'当前热播': 'Trending', '必看好剧': 'Must-sees', '精彩剧集': 'Hidden Gems'}.get(display_name, display_name)
    expected_ranking = {
        'trending': 'Trending',
        'must-sees': 'Must-sees',
        'must-see': 'Must-sees',
        'hidden-gems': 'Hidden Gems',
        'hidden-gem': 'Hidden Gems',
    }.get(channel_slug)
    semantic_verified = bool(expected_ranking and ranking_type.casefold() == expected_ranking.casefold())
    target_slug = channel_slug.replace('-', '_')
    return {
        'platform': 'DramaBox',
        'source_type': 'OFFICIAL_WEB',
        'source_id': 'officialweb_dramabox',
        'target_key': f'web_{target_slug}_all',
        'ranking_type': ranking_type,
        'category': 'All',
        'collection_method': 'WEB_SCRAPE',
        'collection_date': collection_date,
        'top_n': top_n,
        'batch_complete': len(rows) == top_n,
        'rows': rows,
        'collector_version': 'dramabox-nextdata-v1',
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'locale': COLLECTION_LOCALE,
        'region': COLLECTION_REGION,
        'evidence': {
            'url': url,
            'requestedUrl': url,
            'channel': channel_slug,
            'page': page_props.get('pageNo', 1),
            'pages': page_props.get('pages'),
            'row_count': len(rows),
            'next_build_id': _clean(next_data.get('buildId'), 120),
            'observedLocale': observed_locale,
            'semanticVerified': semantic_verified,
            **fetch_evidence,
        },
        'evidence_persistence': 'URL_AND_PARSED_FACTS',
        'provider': 'official-web-nextdata',
    }
