from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser

DEFAULT_URL = "https://www.reelshort.com/shelf/top-short-movies-dramas-51001122"
USER_AGENT = "Mozilla/5.0 (compatible; ShortDramaMonitor/1.0; +https://short-drama-monitor.onrender.com/)"


class HeadingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.buf: list[str] = []
        self.headings: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        self.stack.append(tag.lower())
        if tag.lower() in {"h1", "h2", "h3"}:
            self.buf = []

    def handle_data(self, data: str):
        if self.stack and self.stack[-1] in {"h1", "h2", "h3"}:
            self.buf.append(data)

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in {"h1", "h2", "h3"}:
            text = re.sub(r"\s+", " ", " ".join(self.buf)).strip()
            if text:
                self.headings.append(html.unescape(text))
            self.buf = []
        if self.stack:
            try:
                idx = len(self.stack) - 1 - self.stack[::-1].index(tag)
                self.stack = self.stack[:idx]
            except ValueError:
                pass


def _fetch(url: str, timeout: int = 30) -> tuple[int, str, bytes]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return int(resp.status), resp.geturl(), raw
    except urllib.error.HTTPError as exc:
        return int(exc.code), url, exc.read()


def _candidate_titles_from_json(value, out: list[str]) -> None:
    if isinstance(value, dict):
        for key in ("title", "name", "bookName", "book_name", "videoName", "seriesName"):
            v = value.get(key)
            if isinstance(v, str):
                t = re.sub(r"\s+", " ", html.unescape(v)).strip()
                if 3 <= len(t) <= 180:
                    out.append(t)
        for child in value.values():
            _candidate_titles_from_json(child, out)
    elif isinstance(value, list):
        for child in value:
            _candidate_titles_from_json(child, out)


def _extract_json_scripts(text: str) -> list[str]:
    titles: list[str] = []
    patterns = [
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    ]
    for pat in patterns:
        for blob in re.findall(pat, text, flags=re.I | re.S):
            try:
                _candidate_titles_from_json(json.loads(html.unescape(blob)), titles)
            except Exception:
                continue
    return titles


def _clean_titles(values: list[str]) -> list[str]:
    banned = {
        "top verticals", "top short dramas / tv series", "reelshort", "home", "movies",
        "categories", "topics", "about", "support", "download",
    }
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        title = re.sub(r"\s+", " ", value).strip()
        key = title.casefold()
        if not title or key in banned:
            continue
        if len(title) < 3 or len(title) > 180:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(title)
    return out


def collect(url: str = DEFAULT_URL) -> dict:
    status, final_url, raw = _fetch(url)
    text = raw.decode("utf-8", errors="replace")
    parser = HeadingParser()
    try:
        parser.feed(text)
    except Exception:
        pass
    titles = _clean_titles(parser.headings + _extract_json_scripts(text))

    top10 = titles[:10]
    warnings: list[str] = []
    if status != 200:
        warnings.append(f"HTTP_{status}")
    if len(top10) < 10:
        warnings.append(f"ONLY_{len(top10)}_TITLE_CANDIDATES")
    warnings.append("OFFICIAL_WEB_TOP_IS_A_SEPARATE_SOURCE_FROM_APP_DAILY_TOP")

    return {
        "provider": "reelshort_official_web",
        "source_group": "OFFICIAL_WEB",
        "source_id": "officialweb_reelshort",
        "target_key": "web_top_all_pilot",
        "collection_method": "WEB_SCRAPE",
        "url": url,
        "final_url": final_url,
        "http_status": status,
        "raw_bytes": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "candidate_titles": top10,
        "candidate_count": len(top10),
        "batch_complete": len(top10) == 10,
        "warnings": warnings,
        "production_ready": False,
        "equivalent_to_app_daily_top": None,
    }


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    print(json.dumps(collect(url), ensure_ascii=False, indent=2))
