from __future__ import annotations

import gzip
import html as html_lib
import json
import re
import urllib.error
import urllib.request
from datetime import date, timedelta
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


def _strip_markup(value: Any, limit: int = 500) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return base.clean(html_lib.unescape(text), limit)


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


def _rows_from_dramawave(document: str):
    """Parse explicit `Nth Most Trending` cards from DramaWave Popular Choices.

    The mobile official Web page renders custom x-* elements. Rank is taken only from
    the platform's explicit Most Trending label; visual grid order is never promoted
    into a rank and missing positions are left missing for the pilot audit to flag.
    """
    heading = re.search(
        r'<x-title\b[^>]*>\s*Popular Choices\s*</x-title>',
        document,
        flags=re.I | re.S,
    )
    if not heading:
        return [], {"structuredData": "DramaWave Popular Choices heading not found"}, False

    start = heading.end()
    next_heading = re.search(r'<x-title\b[^>]*>', document[start:], flags=re.I)
    end = start + next_heading.start() if next_heading else min(len(document), start + 500000)
    section = document[start:end]
    cards = re.findall(r'<x-drama-card\b[^>]*>(.*?)</x-drama-card>', section, flags=re.I | re.S)

    by_rank: dict[int, dict[str, Any]] = {}
    duplicate_ranks: list[int] = []
    for card in cards:
        title_match = re.search(r'<x-drama-title\b[^>]*>(.*?)</x-drama-title>', card, flags=re.I | re.S)
        rank_match = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)\s+Most\s+Trending\b', card, flags=re.I)
        if not title_match or not rank_match:
            continue
        title = _strip_markup(title_match.group(1), 500)
        try:
            rank = int(rank_match.group(1))
        except (TypeError, ValueError):
            continue
        if not 1 <= rank <= TOP_N or not title:
            continue
        if rank in by_rank:
            duplicate_ranks.append(rank)
            continue
        by_rank[rank] = {"rank": rank, "title": title}

    rows = [by_rank[r] for r in sorted(by_rank)]
    return rows, {
        "structuredData": "DramaWave Popular Choices explicit Most Trending labels",
        "cardCount": len(cards),
        "explicitRankCount1To10": len(rows),
        "duplicateExplicitRanks": sorted(set(duplicate_ranks)),
    }, True


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



def _rows_from_goodshort(document: str):
    try:
        from goodshort_collector import parse_goodshort_channel
        items = parse_goodshort_channel(document, "https://www.goodshort.com/")
    except Exception as exc:
        return [], {"structuredData": "GoodShort rendered DOM parse failed", "parseError": f"{type(exc).__name__}: {exc}"}, False

    rows = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        title = base.clean(item.get("title"), 500)
        key = base.norm_title(title)
        if not title or not key or key in seen:
            continue
        seen.add(key)
        row = {"rank": len(rows) + 1, "title": title}
        url = base.clean(item.get("url"), 1200)
        if url:
            row["source_url"] = url
        rows.append(row)
        if len(rows) >= TOP_N:
            break
    return rows, {"structuredData": "GoodShort rendered channel cards", "cardCount": len(items)}, bool(items)


def _rows_from_shortmax(document: str, collection_date: str):
    try:
        from official_web_collectors import collect_shortmax
        payload = collect_shortmax(
            collection_date=collection_date,
            section="Most Popular",
            top_n=None,
            document=document,
        )
    except Exception as exc:
        return [], {"structuredData": "ShortMax rendered DOM parse failed", "parseError": f"{type(exc).__name__}: {exc}"}, False

    rows = []
    for idx, item in enumerate(payload.get("rows") or [], start=1):
        title = base.clean(item.get("title"), 500)
        if not title:
            continue
        row = {"rank": int(item.get("rank") or idx), "title": title}
        url = base.clean(item.get("source_url") or item.get("sourceUrl"), 1200)
        if url:
            row["source_url"] = url
        rows.append(row)
        if len(rows) >= TOP_N:
            break
    return rows, {
        "structuredData": "ShortMax rendered Most Popular DOM",
        "renderedCardCount": len(payload.get("rows") or []),
    }, bool(rows)


def _parse_exact(platform: str, document: str, collection_date: str):
    if platform == "DramaBox":
        return _rows_from_dramabox(document, collection_date)
    if platform == "DramaWave":
        return _rows_from_dramawave(document)
    if platform == "FlexTV":
        return _rows_from_itemlist(document, "Top in FlexTV")
    if platform == "GoodShort":
        return _rows_from_goodshort(document)
    if platform == "NetShort":
        return _rows_from_itemlist(document, "Trending Now")
    if platform == "ReelShort":
        return _rows_from_reelshort(document)
    if platform == "MoboReels":
        return _rows_from_moboreels(document)
    if platform == "ShortMax":
        return _rows_from_shortmax(document, collection_date)
    return [], {"structuredData": "No exact browser adapter"}, False


def browser_probe(browser, cfg: dict[str, Any], evidence_dir: Path, collection_date: str | None = None):
    collection_date = collection_date or base.local_today()
    platform = str(cfg.get("platform") or "")

    desktop_ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36"
    )
    mobile_ua = (
        "Mozilla/5.0 (Linux; Android 15; Pixel 9 Pro) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Mobile Safari/537.36"
    )
    mobile_first = platform == "DramaWave"
    profiles = [
        {
            "viewport": {"width": 390, "height": 844} if mobile_first else {"width": 1440, "height": 1200},
            "user_agent": mobile_ua if mobile_first else desktop_ua,
            "is_mobile": mobile_first,
            "has_touch": mobile_first,
        },
        {
            "viewport": {"width": 1440, "height": 1200} if mobile_first else {"width": 390, "height": 844},
            "user_agent": desktop_ua if mobile_first else mobile_ua,
            "is_mobile": not mobile_first,
            "has_touch": not mobile_first,
        },
    ]

    last_error = ""
    last_evidence: dict[str, Any] = {}
    for attempt, profile in enumerate(profiles, start=1):
        page = browser.new_page(
            viewport=profile["viewport"],
            locale="en-US",
            user_agent=profile["user_agent"],
            is_mobile=profile["is_mobile"],
            has_touch=profile["has_touch"],
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        try:
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            response = page.goto(cfg["url"], wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(4000)

            # Trigger common lazy-render/carousel hydration paths without inferring ranks.
            scroll_rounds = 10 if platform == "DramaWave" else 4
            for _ in range(scroll_rounds):
                page.mouse.wheel(0, 1400)
                page.wait_for_timeout(350)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(700)

            document = page.content()
            raw = document.encode("utf-8", errors="replace")
            suffix = "" if attempt == 1 else f"-attempt{attempt}"
            html_path = evidence_dir / f'{cfg["platform"]}{suffix}.html.gz'
            with gzip.open(html_path, "wb", compresslevel=6) as fh:
                fh.write(raw)
            screenshot_path = evidence_dir / f'{cfg["platform"]}{suffix}.png'
            page.screenshot(path=str(screenshot_path), full_page=True)

            rows, adapter_meta, found = _parse_exact(platform, document, collection_date)
            status_code = int(response.status) if response is not None else None
            evidence = {
                "pageTitle": base.clean(page.title(), 300),
                "pageUrl": base.clean(page.url, 1200),
                "documentLang": base.clean(page.locator("html").get_attribute("lang"), 40),
                "httpStatus": status_code,
                "browserAttempt": attempt,
                "mobileProfile": bool(profile["is_mobile"]),
                **adapter_meta,
                "rawHtmlSha256": base.sha256_bytes(raw),
                "rawHtmlFile": str(html_path.relative_to(base.ROOT)),
                "screenshotFile": str(screenshot_path.relative_to(base.ROOT)),
                "screenshotSha256": base.sha256_bytes(screenshot_path.read_bytes()),
            }
            last_evidence = evidence
            if rows or (found and status_code is not None and status_code < 400):
                return rows, evidence, found
            last_error = f"attempt={attempt} status={status_code} adapter_found={found}"
        except Exception as exc:
            last_error = f"attempt={attempt} {type(exc).__name__}: {exc}"
        finally:
            page.close()

    if last_error:
        last_evidence = {**last_evidence, "browserRetryError": last_error}
    return [], last_evidence, False


def _dramabox_direct_next_data(collection_date: str):
    """Try DramaBox's public Next.js JSON route when the HTML route blocks cloud IPs."""
    try:
        anchor = date.fromisoformat(collection_date)
    except Exception:
        anchor = date.today()

    build_ids = ["dramaboxdb_prod_20260908"]
    build_ids.extend(
        f"dramaboxdb_prod_{(anchor - timedelta(days=offset)).strftime('%Y%m%d')}"
        for offset in range(0, 15)
    )
    seen = set()
    ordered_build_ids = []
    for build_id in build_ids:
        if build_id not in seen:
            seen.add(build_id)
            ordered_build_ids.append(build_id)

    errors = []
    for build_id in ordered_build_ids:
        for locale_prefix in ("", "en/"):
            url = (
                "https://www.dramaboxdb.com/_next/data/"
                f"{build_id}/{locale_prefix}channel/trending.json"
            )
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36"
                    ),
                    "Accept": "application/json,text/plain,*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    raw = response.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 403:
                    raise RuntimeError(f"DRAMABOX_NEXT_DATA_FORBIDDEN:{build_id}") from exc
                if exc.code == 404:
                    continue
                errors.append(f"{build_id}:{exc.code}")
                continue
            except Exception as exc:
                errors.append(f"{build_id}:{type(exc).__name__}")
                continue

            try:
                payload = json.loads(raw.decode("utf-8", errors="replace"))
                page_props = payload.get("pageProps") or payload.get("props", {}).get("pageProps") or {}
                more_data = page_props.get("moreData") or {}
                items = more_data.get("items") or []
            except Exception as exc:
                errors.append(f"{build_id}:json:{type(exc).__name__}")
                continue
            if not isinstance(items, list) or len(items) < TOP_N:
                continue

            rows = []
            for idx, item in enumerate(items[:TOP_N], start=1):
                if not isinstance(item, dict):
                    continue
                title = base.clean(item.get("bookName") or item.get("name"), 500)
                if not title:
                    continue
                row = {"rank": idx, "title": title}
                book_id = base.clean(item.get("bookId") or item.get("action"), 80)
                slug = base.clean(item.get("bookNameLower") or item.get("replacedBookName"), 300)
                if book_id and slug:
                    row["source_url"] = f"https://www.dramaboxdb.com/movie/{book_id}/{slug}"
                rows.append(row)
            if len(rows) == TOP_N:
                return rows, {
                    "collectorVersion": "dramabox-nextdata-direct-pilot-v1",
                    "nextDataUrl": url,
                    "nextBuildId": build_id,
                    "rankingName": base.clean(more_data.get("name"), 120),
                    "rowCount": len(rows),
                    "cloudHtmlBypass": True,
                }

    raise RuntimeError(
        "DRAMABOX_NEXT_DATA_UNAVAILABLE"
        + (":" + ",".join(errors[-5:]) if errors else "")
    )


def run_verified_parser(cfg: dict[str, Any], collection_date: str):
    platform = cfg.get("platform")
    if platform == "DramaBox":
        try:
            return base.run_verified_parser(cfg, collection_date)
        except Exception as primary:
            rows, evidence = _dramabox_direct_next_data(collection_date)
            evidence["stdlibFallbackError"] = f"{type(primary).__name__}: {primary}"
            return rows, evidence
    if platform != "ShortMax":
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
