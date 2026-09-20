from __future__ import annotations

"""Poster / cover enrichment for JSM drama records.

This module is intentionally independent from ranking collection. Ranking collection
must never fail because a cover image is unavailable.

Preferred flow:
1. Ranking/Web collectors opportunistically emit poster_url when the source already
   exposes it.
2. This enrichment worker fills missing posters from already-known source/evidence
   pages.
3. The result is written as a standard updates/*.json record_patches file and can be
   reviewed before being applied.

No search engine API is required by this first version. It only follows URLs JSM
already knows (sourceUrl / episodeUrl / research sources), which keeps provenance
clear and avoids title-matching mistakes.
"""

import argparse
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse


ROOT = Path(__file__).resolve().parent
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36 JSMPosterEnricher/1.0"
)


def _clean(value, limit=2000):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _attr(attrs, name):
    for key, value in attrs:
        if key == name:
            return value or ""
    return ""


def _tokens(value):
    return {x for x in re.split(r"[^a-z0-9_-]+", str(value or "").casefold()) if x}


def _safe_image_url(url, base_url):
    value = _clean(url, 3000)
    if not value or value.startswith(("data:", "blob:")):
        return ""
    absolute = urljoin(base_url, value)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return ""
    if parsed.path.lower().endswith(".svg"):
        return ""
    return absolute


class ImageCandidateParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.candidates = []

    def _add(self, url, score, reason):
        absolute = _safe_image_url(url, self.base_url)
        if not absolute:
            return
        self.candidates.append({"url": absolute, "score": score, "reason": reason})

    def handle_starttag(self, tag, attrs):
        tag = tag.casefold()
        if tag == "meta":
            key = (_attr(attrs, "property") or _attr(attrs, "name")).casefold()
            content = _attr(attrs, "content")
            if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}:
                self._add(content, 100, key)
            elif key in {"image", "thumbnail", "thumbnailurl"}:
                self._add(content, 85, key)
            return

        if tag == "link":
            rel = _tokens(_attr(attrs, "rel"))
            if "image_src" in rel or "preload" in rel and _attr(attrs, "as") == "image":
                self._add(_attr(attrs, "href"), 80, "link-image")
            return

        if tag != "img":
            return

        src = (
            _attr(attrs, "data-src")
            or _attr(attrs, "data-original")
            or _attr(attrs, "data-lazy-src")
            or _attr(attrs, "src")
        )
        marker = " ".join(
            [
                _attr(attrs, "class"),
                _attr(attrs, "id"),
                _attr(attrs, "alt"),
                _attr(attrs, "title"),
            ]
        ).casefold()
        score = 30
        if any(word in marker for word in ("poster", "cover", "book", "drama", "movie")):
            score += 45
        if any(word in marker for word in ("logo", "avatar", "icon", "banner")):
            score -= 25
        self._add(src, score, "img")


def fetch_html(url, timeout=20):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content_type = str(resp.headers.get("Content-Type") or "")
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            return ""
        raw = resp.read(3 * 1024 * 1024)
    return raw.decode("utf-8", errors="replace")


def extract_best_image(page_url, html):
    parser = ImageCandidateParser(page_url)
    parser.feed(html)
    seen = set()
    ranked = []
    for item in parser.candidates:
        url = item["url"]
        if url in seen:
            continue
        seen.add(url)
        ranked.append(item)
    ranked.sort(key=lambda x: (-int(x["score"]), x["url"]))
    return ranked[0] if ranked else None


def source_urls_for(record, research_meta):
    urls = []
    for key in ("sourceUrl", "episodeUrl", "posterSourceUrl"):
        value = _clean(record.get(key), 3000)
        if value:
            urls.append(value)
    for value in record.get("researchSources") or []:
        value = _clean(value, 3000)
        if value:
            urls.append(value)
    meta = research_meta.get(record.get("title") or "") if isinstance(research_meta, dict) else None
    if isinstance(meta, dict):
        for value in meta.get("sources") or []:
            value = _clean(value, 3000)
            if value:
                urls.append(value)
    out = []
    seen = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def enrich_record(record, research_meta, timeout=20):
    if _clean(record.get("posterUrl"), 3000):
        return None
    for source_url in source_urls_for(record, research_meta):
        try:
            html = fetch_html(source_url, timeout=timeout)
            if not html:
                continue
            candidate = extract_best_image(source_url, html)
            if not candidate:
                continue
            return {
                "posterUrl": candidate["url"],
                "posterSourceUrl": source_url,
                "posterUpdatedAt": datetime.now(timezone.utc).isoformat(),
                "posterEvidence": candidate["reason"],
            }
        except Exception as exc:
            print(f"[poster] skip {record.get('title','')}: {source_url}: {exc}")
    return None


def main():
    parser = argparse.ArgumentParser(description="Enrich JSM drama records with poster/cover URLs.")
    parser.add_argument("--data", default=str(ROOT / "data.json"))
    parser.add_argument("--research-meta", default=str(ROOT / "research_meta.json"))
    parser.add_argument("--output", default=str(ROOT / "updates" / "poster_assets_preview.json"))
    parser.add_argument("--limit", type=int, default=0, help="0 = no limit")
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    research_meta = {}
    meta_path = Path(args.research_meta)
    if meta_path.exists():
        research_meta = json.loads(meta_path.read_text(encoding="utf-8"))

    patches = {}
    attempted = 0
    matched = 0
    for record in data.get("records") or []:
        if args.limit and attempted >= args.limit:
            break
        if _clean(record.get("posterUrl"), 3000):
            continue
        if not source_urls_for(record, research_meta):
            continue
        attempted += 1
        patch = enrich_record(record, research_meta, timeout=args.timeout)
        if patch:
            patches[record["id"]] = patch
            matched += 1
            print(f"[poster] matched {record.get('title')} -> {patch['posterUrl']}")
        if args.delay:
            time.sleep(args.delay)

    output = {
        "kind": "JSM_POSTER_ENRICHMENT_V1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "attempted": attempted,
        "matched": matched,
        "record_patches": patches,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[poster] wrote {out_path} attempted={attempted} matched={matched}")


if __name__ == "__main__":
    main()
