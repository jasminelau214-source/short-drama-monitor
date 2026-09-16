from __future__ import annotations

import html
import re
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin


DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36'
)


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


class ShortMaxSectionParser(HTMLParser):
    """Parse the rendered/SSR ShortMax homepage into named content sections.

    The site currently renders each section with `section-title`, followed by
    `drama-card` blocks containing `card-title`, `overlay-tags` and
    `overlay-description`. The parser intentionally keys off those semantic
    classes rather than brittle absolute DOM paths.
    """

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

        if 'section-title' in classes:
            self._section_title_depth = 1
            self._section_title_buf = []
        elif self._section_title_depth:
            self._section_title_depth += 1

        if 'drama-card' in classes:
            self._card_depth = 1
            self._card = {'title': '', 'tags': [], 'synopsis': '', 'url': '', 'episodeUrl': ''}
        elif self._card_depth:
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
            elif self._capture_depth:
                self._capture_depth += 1

            if self._capture_field == 'tags' and tag == 'span':
                self._tag_span_depth = 1
                self._tag_buf = []
            elif self._tag_span_depth:
                self._tag_span_depth += 1

    def handle_data(self, data):
        if self._section_title_depth:
            self._section_title_buf.append(data)
        if self._capture_depth:
            self._capture_buf.append(data)
        if self._tag_span_depth:
            self._tag_buf.append(data)

    def handle_endtag(self, tag):
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


def parse_shortmax_sections(document: str, base_url: str = 'https://www.shorttv.live/') -> dict[str, list[dict]]:
    parser = ShortMaxSectionParser(base_url)
    parser.feed(document or '')
    parser.close()
    return parser.sections


def fetch_html(url: str, timeout: int = 25) -> str:
    request = urllib.request.Request(
        url,
        headers={
            'User-Agent': DEFAULT_USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or 'utf-8'
            return response.read().decode(charset, errors='replace')
    except Exception as exc:
        raise OfficialWebCollectorError(f'WEB_FETCH_FAILED: {url}: {exc}') from exc


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
    """Collect one ShortMax official-web section into the generic collector schema.

    This returns OFFICIAL_WEB facts. It must stay separate from App ranking history
    unless a later audit explicitly proves the web section is equivalent to an App ranking.
    """
    if document is None:
        document = fetch_html(url)
    sections = parse_shortmax_sections(document, url)
    section_name, cards = _find_section(sections, section)
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

    target_slug = re.sub(r'[^a-z0-9]+', '_', _section_key(section_name)).strip('_') or 'section'
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
        'target_key': f'web_{target_slug}',
        'ranking_type': section_name,
        'category': 'All' if _section_key(section_name).startswith('most popular') else section_name,
        'collection_method': 'WEB_SCRAPE',
        'collection_date': collection_date,
        'top_n': top_n,
        'batch_complete': len(rows) == top_n,
        'rows': rows,
        'collector_version': 'shortmax-web-v1',
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'locale': 'en-US',
        'evidence': {
            'url': url,
            'section': section_name,
            'row_count': len(rows),
        },
        'evidence_persistence': 'URL_AND_PARSED_FACTS',
        'provider': 'official-web-stdlib',
    }
