from __future__ import annotations

import gzip
import html as html_lib
import json
import re
from pathlib import Path
from typing import Any

import pilot_web_top10 as base

TOP_N = base.TOP_N


def _script_json_blocks(document: str, *, script_id: str = "", ld_json: bool = False) -> list[dict[str, Any]]:
    if script_id:
        pattern = rf'<script[^>]*id=["\']{re.escape(script_id)}["\'][^>]*>(.*?)</script>'
    elif ld_json:
        pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
    else:
        return []
    out = []
    for raw in re.findall(pattern, document, flags=re.I | re.S):
        try:
            value = json.loads(html_lib.unescape(raw.strip()))
        except Exception:
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def _find_itemlists(value: Any) -> list[dict[str, Any]]:
    out = []
    if isinstance(value, dict):
        t = value.get("@type")
        if t == "ItemList" or (isinstance(t, list) and "ItemList" in t):
            out.append(value)
        for child in value.values():
            out.extend(_find_itemlists(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(_find_itemlists(child))
    return out


def _rows_from_itemlist(document: str, expected_name: str):
    expected = base.clean(expected_name, 200).casefold()
    candidates = []
    for block in _script_json_blocks(document, ld_json=True):
        for itemlist in _find_itemlists(block):
            elems = itemlist.get("itemListElement")
            if not isinstance(elems, list):
                continue
            name = base.clean(itemlist.get("name"), 200)
            score = 2 if expected and expected in name.casefold() else 1
            candidates.append((score, len(elems), name, itemlist))
    if not candidates:
        return [], {"structuredData": "ItemList not found"}, False
    candidates.sort(reverse=True, key=lambda x: (x[0], x[1]))
    _, _, name, best = candidates[0]
    rows = []
    for idx, entry in enumerate(best.get("itemListElement") or [], start=1):
        if not isinstance(entry, dict):
            continue
        item = entry.get("item") if isinstance(entry.get("item"), dict) else entry
        title = base.clean(item.get("name") or entry.get("name"), 500)
        if not title:
            continue
        try:
            rank = int(entry.get("position") or idx)
        except Exception:
            rank = idx
        row = {"rank": rank, "title": title}
        url = base.clean(item.get("url") or entry.get("url") or item.get("@id"), 1200)
        if url:
            row["source_url"] = url
        rows.append(row)
        if len(rows) >= TOP_N:
            break
    rows.sort(key=lambda x: int(x.get("rank") or 999))
    return rows, {"structuredData": "JSON-LD ItemList", "itemListName": name}, True


def _rows_from_reelshort(document: str):
    blocks = _script_json_blocks(document, script_id="__NEXT_DATA__")
    if not blocks:
        return [], {"structuredData": "__NEXT_DATA__ not found"}, False
    pp = blocks[0].get("props", {}).get("pageProps", {})
    raw_list = pp.get("list")
    if not isinstance(raw_list, list):
        return [], {"structuredData": "pageProps.list not found"}, False
    rows = []
    for idx, item in enumerate(raw_list[:TOP_N], start=1):
        if isinstance(item, dict) and base.clean(item.get("book_title"), 500):
            rows.append({"rank": idx, "title": base.clean(item.get("book_title"), 500)})
    return rows, {
        "structuredData": "Next.js __NEXT_DATA__ pageProps.list",
        "shelfName": base.clean(pp.get("shelfName"), 200),
        "total": pp.get("total"),
    }, True


def _rows_from_moboreels(document: str):
    heading = re.search(
        r'<h2[^>]*class=["\'][^"\']*home-list-title[^"\']*["\'][^>]*>\s*Popular Series\s*</h2>',
        document,
        flags=re.I | re.S,
    )
    if not heading:
        return [], {"structuredData": "Popular Series heading not found"}, False
    start = heading.end()
    next_section = re.search(r'<div[^>]*class=["\'][^"\']*home-list(?:\s|["\'])', document[start:], flags=re.I)
    end = start + next_section.start() if next_section else min(len(document), start + 180000)
    section = document[start:end]
    titles = [
        base.clean(raw, 500)
        for raw in re.findall(
            r'<h3[^>]*class=["\'][^"\']*home-list-item-title[^"\']*["\'][^>]*>(.*?)</h3>',
            section,
            flags=re.I | re.S,
        )
    ]
    rows = []
    seen = set()
    for title in titles:
        key = base.norm_title(title)
        if not title or not key or key in seen:
            continue
        seen.add(key)
        rows.append({"rank": len(rows) + 1, "title": title})
        if len(rows) >= TOP_N:
            break
    return rows, {"structuredData": "MoboReels Popular Series DOM", "visibleTitles": len(titles)}, True


def _rows_from_dramabox(document: str, collection_date: str):
    try:
        from official_web_collectors import collect_dramabox_channel
        payload = collect_dramabox_channel(
            channel="trending",
            collection_date=collection_date,
            top_n=TOP_N,
            document=document,
        )
    except Exception as exc:
        return [], {"structuredData": "DramaBox browser parse failed", "parseError": f"{type(exc).__name__}: {exc}"}, False
    rows = [
        {"rank": int(x.get("rank") or i), "title": base.clean(x.get("title"), 500)}
        for i, x in enumerate(payload.get("rows") or [], start=1)
    ]
    return rows, {"structuredData": "DramaBox __NEXT_DATA__ browser fallback"}, True


def _parse_exact(platform: str, document: str, collection_date: str):
    if platform == "DramaBox":
        return _rows_from_dramabox(document, collection_date)
    if platform == "FlexTV":
        return _rows_from_itemlist(document, "Top in FlexTV")
    if platform == "NetShort":
        return _rows_from_itemlist(document, "Trending Now")
    if platform == "ReelShort":
        return _rows_from_reelshort(document)
    if platform == "MoboReels":
        return _rows_from_moboreels(document)
    return [], {"structuredData": "No exact browser adapter"}, False


def browser_probe(browser, cfg: dict[str, Any], evidence_dir: Path, collection_date: str | None = None):
    collection_date = collection_date or base.local_today()
    page = browser.new_page(viewport={"width": 1440, "height": 1200}, locale="en-US")
    try:
        page.goto(cfg["url"], wait_until="domcontentloaded", timeout=70000)
        page.wait_for_timeout(5000)
        document = page.content()
        raw = document.encode("utf-8", errors="replace")
        html_path = evidence_dir / f'{cfg["platform"]}.html.gz'
        with gzip.open(html_path, "wb", compresslevel=6) as fh:
            fh.write(raw)
        screenshot_path = evidence_dir / f'{cfg["platform"]}.png'
        page.screenshot(path=str(screenshot_path), full_page=True)
        rows, adapter_meta, found = _parse_exact(cfg["platform"], document, collection_date)
        evidence = {
            "pageTitle": base.clean(page.title(), 300),
            "pageUrl": base.clean(page.url, 1200),
            "documentLang": base.clean(page.locator("html").get_attribute("lang"), 40),
            **adapter_meta,
            "rawHtmlSha256": base.sha256_bytes(raw),
            "rawHtmlFile": str(html_path.relative_to(base.ROOT)),
            "screenshotFile": str(screenshot_path.relative_to(base.ROOT)),
            "screenshotSha256": base.sha256_bytes(screenshot_path.read_bytes()),
        }
        return rows, evidence, found
    finally:
        page.close()


def run_verified_parser(cfg: dict[str, Any], collection_date: str):
    if cfg.get("platform") != "ShortMax":
        return base.run_verified_parser(cfg, collection_date)
    from official_web_collectors import collect_shortmax
    payload = collect_shortmax(collection_date=collection_date, section="Most Popular", top_n=None)
    rows = []
    for idx, row in enumerate(payload.get("rows") or [], start=1):
        item = {
            "rank": int(row.get("rank") or idx),
            "title": base.clean(row.get("title"), 500),
            "tags": row.get("tags") if isinstance(row.get("tags"), list) else [],
        }
        url = base.clean(row.get("source_url") or row.get("sourceUrl"), 1200)
        if url:
            item["source_url"] = url
        rows.append(item)
    return rows, {
        "collectorVersion": base.clean(payload.get("collector_version"), 100),
        "collectedAt": base.clean(payload.get("collected_at"), 100),
        "payloadEvidence": payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {},
    }
