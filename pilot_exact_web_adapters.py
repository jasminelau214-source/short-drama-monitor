from __future__ import annotations

import gzip
import html as html_lib
import json
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
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


def _document_semantic_marker(document: str, expected: str) -> bool:
    wanted = base.clean(expected, 200).casefold()
    if not wanted:
        return False
    title_match = re.search(r'<title[^>]*>(.*?)</title>', document or '', flags=re.I | re.S)
    if title_match and wanted in _strip_markup(title_match.group(1), 500).casefold():
        return True
    for raw in re.findall(
        r'<h[1-6][^>]*>(.*?)</h[1-6]>',
        document or '',
        flags=re.I | re.S,
    ):
        if wanted in _strip_markup(raw, 500).casefold():
            return True
    return False


def _rows_from_itemlist(document: str, expected_name: str):
    expected = base.clean(expected_name, 200).casefold()
    candidates = []
    for block in _script_json_blocks(document, ld_json=True):
        for itemlist in _find_itemlists(block):
            elems = itemlist.get("itemListElement")
            if not isinstance(elems, list):
                continue
            name = base.clean(itemlist.get("name"), 200)
            named_match = bool(expected and expected in name.casefold())
            candidates.append((named_match, len(elems), name, itemlist))

    if not candidates:
        return [], {
            "structuredData": "ItemList not found",
            "semanticVerified": False,
        }, False

    named = [x for x in candidates if x[0]]
    page_marker = _document_semantic_marker(document, expected_name)
    if named:
        named.sort(reverse=True, key=lambda x: x[1])
        _, _, name, best = named[0]
        semantic_verified = True
        semantic_method = "itemListName"
    elif page_marker and len(candidates) == 1:
        _, _, name, best = candidates[0]
        semantic_verified = True
        semantic_method = "pageTitleOrHeading+singleItemList"
    else:
        return [], {
            "structuredData": "JSON-LD ItemList",
            "itemListNames": [x[2] for x in candidates],
            "itemListCount": len(candidates),
            "semanticVerified": False,
            "semanticExpected": expected_name,
            "pageSemanticMarker": page_marker,
            "semanticError": "TARGET_ITEMLIST_NOT_IDENTIFIED",
        }, True

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
    return rows, {
        "structuredData": "JSON-LD ItemList",
        "itemListName": name,
        "itemListCount": len(candidates),
        "semanticVerified": semantic_verified,
        "semanticMethod": semantic_method,
        "semanticExpected": expected_name,
    }, True


def _rows_from_reelshort(document: str):
    blocks = _script_json_blocks(document, script_id="__NEXT_DATA__")
    if not blocks:
        return [], {
            "structuredData": "__NEXT_DATA__ not found",
            "semanticVerified": False,
        }, False
    pp = blocks[0].get("props", {}).get("pageProps", {})
    shelf_name = base.clean(pp.get("shelfName"), 200)
    semantic_verified = shelf_name.casefold() == "top"
    raw_list = pp.get("list")
    if not isinstance(raw_list, list):
        return [], {
            "structuredData": "pageProps.list not found",
            "shelfName": shelf_name,
            "semanticVerified": semantic_verified,
        }, False
    if not semantic_verified:
        return [], {
            "structuredData": "Next.js __NEXT_DATA__ pageProps.list",
            "shelfName": shelf_name,
            "total": pp.get("total"),
            "semanticVerified": False,
            "semanticExpected": "TOP",
            "semanticError": "REELSHORT_SHELF_MISMATCH",
        }, True

    rows = []
    for idx, item in enumerate(raw_list[:TOP_N], start=1):
        if isinstance(item, dict) and base.clean(item.get("book_title"), 500):
            rows.append({"rank": idx, "title": base.clean(item.get("book_title"), 500)})
    return rows, {
        "structuredData": "Next.js __NEXT_DATA__ pageProps.list",
        "shelfName": shelf_name,
        "total": pp.get("total"),
        "semanticVerified": True,
        "semanticExpected": "TOP",
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
    return rows, {
        "structuredData": "MoboReels Popular Series DOM",
        "visibleTitles": len(titles),
        "semanticVerified": True,
        "semanticExpected": "Popular Series",
    }, True


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
    semantic_verified = _document_semantic_marker(document, "Top in GoodShort")
    if not semantic_verified:
        return [], {
            "structuredData": "GoodShort rendered channel cards",
            "semanticVerified": False,
            "semanticExpected": "Top in GoodShort",
            "semanticError": "GOODSHORT_SHELF_MISMATCH",
        }, True
    try:
        from goodshort_collector import parse_goodshort_channel
        items = parse_goodshort_channel(document, "https://www.goodshort.com/")
    except Exception as exc:
        return [], {"structuredData": "GoodShort rendered DOM parse failed", "parseError": f"{type(exc).__name__}: {exc}", "semanticVerified": True}, False

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
    return rows, {
        "structuredData": "GoodShort rendered channel cards",
        "cardCount": len(items),
        "semanticVerified": True,
        "semanticExpected": "Top in GoodShort",
    }, bool(items)


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
        "semanticVerified": True,
        "semanticExpected": "Most Popular",
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


def run_direct_exact(cfg: dict[str, Any], collection_date: str):
    """Run the exact parser against the official HTTP response and freeze the source."""
    from official_web_collectors import fetch_html_with_metadata

    platform = str(cfg.get("platform") or "")
    fetched = fetch_html_with_metadata(str(cfg.get("url") or ""))
    document = str(fetched.get("document") or "")
    rows, adapter_meta, adapter_found = _parse_exact(
        platform,
        document,
        collection_date,
    )

    raw = document.encode("utf-8", errors="replace")
    evidence_dir = base.EVIDENCE_ROOT / collection_date
    evidence_dir.mkdir(parents=True, exist_ok=True)
    html_path = evidence_dir / f"{platform}-direct.html.gz"
    with gzip.open(html_path, "wb", compresslevel=6) as fh:
        fh.write(raw)

    evidence = {
        "collectorVersion": "exact-direct-http-v1",
        "httpStatus": fetched.get("httpStatus"),
        "pageUrl": base.clean(fetched.get("pageUrl"), 1200),
        "fetchedAt": base.clean(fetched.get("fetchedAt"), 100),
        "adapterFound": bool(adapter_found),
        **(adapter_meta if isinstance(adapter_meta, dict) else {}),
        "rawHtmlSha256": base.sha256_bytes(raw),
        "rawHtmlFile": str(html_path.relative_to(base.ROOT)),
        "productionWrite": False,
    }
    return rows, evidence


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
    mobile_first = platform in {"DramaWave", "ShortMax"}
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
    dramawave_profile_rank_titles: dict[int, set[str]] = {}
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

            # Trigger lazy rendering without inferring rank. DramaWave is an
            # infinite recommendation stream, so exhaust rendered cards (bounded)
            # instead of stopping after an arbitrary number of wheel events.
            dramawave_rank_titles: dict[int, set[str]] = {}
            dramawave_scroll_rounds = 0
            dramawave_final_metrics: dict[str, Any] = {}

            if platform == "DramaWave":
                # The public H5 page can expand to full document height in headless
                # browsers, which prevents its internal infinite-scroll observer from
                # ever firing. Constrain the real x-home-page scroller to the viewport
                # so scrolling behaves like an actual phone/desktop session.
                page.evaluate("""
() => {
  const layout = document.querySelector('x-search-and-tabs-layout');
  const innerMain = layout && layout.querySelector('main');
  const scroller = document.querySelector('x-home-page');
  const h = Math.max(620, window.innerHeight - 56);
  if (layout) {
    layout.style.height = window.innerHeight + 'px';
    layout.style.maxHeight = window.innerHeight + 'px';
  }
  if (innerMain) {
    innerMain.style.height = h + 'px';
    innerMain.style.maxHeight = h + 'px';
  }
  if (scroller) {
    scroller.style.height = h + 'px';
    scroller.style.maxHeight = h + 'px';
    scroller.style.overflowY = 'auto';
    scroller.scrollTop = 0;
  }
}
""")
                page.wait_for_timeout(500)

                bottom_stable = 0
                previous_height = -1

                def capture_dramawave_rank_labels() -> None:
                    snapshots = page.evaluate("""
() => Array.from(document.querySelectorAll('x-drama-card')).map(card => {
  const title = String(card.querySelector('x-drama-title')?.textContent || '').replace(/\\s+/g, ' ').trim();
  const text = String(card.textContent || '').replace(/\\s+/g, ' ').trim();
  const match = text.match(/\\b(\\d{1,2})(?:st|nd|rd|th)\\s+Most\\s+Trending\\b/i);
  return match && title ? {rank: Number(match[1]), title} : null;
}).filter(Boolean)
""")
                    for snap in snapshots or []:
                        if not isinstance(snap, dict):
                            continue
                        try:
                            rank = int(snap.get("rank"))
                        except Exception:
                            continue
                        title = base.clean(snap.get("title"), 500)
                        if not 1 <= rank <= 30 or not title:
                            continue
                        dramawave_rank_titles.setdefault(rank, set()).add(title)

                for _ in range(60):
                    capture_dramawave_rank_labels()
                    metrics = page.evaluate("""
() => {
  const scroller = document.querySelector('x-home-page');
  if (!scroller) {
    window.scrollBy(0, Math.max(700, window.innerHeight * 0.8));
    return {
      scrollTop: window.scrollY,
      scrollHeight: document.documentElement.scrollHeight,
      clientHeight: window.innerHeight,
      atBottom: window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 8
    };
  }
  const step = Math.max(700, scroller.clientHeight * 0.8);
  scroller.scrollTop = Math.min(scroller.scrollTop + step, scroller.scrollHeight);
  return {
    scrollTop: scroller.scrollTop,
    scrollHeight: scroller.scrollHeight,
    clientHeight: scroller.clientHeight,
    atBottom: scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 8
  };
}
""")
                    dramawave_scroll_rounds += 1
                    page.wait_for_timeout(650)
                    current_height = int((metrics or {}).get("scrollHeight") or 0)
                    at_bottom = bool((metrics or {}).get("atBottom"))
                    if at_bottom and current_height == previous_height:
                        bottom_stable += 1
                    elif at_bottom:
                        bottom_stable = 1
                    else:
                        bottom_stable = 0
                    previous_height = current_height
                    dramawave_final_metrics = metrics or {}
                    if bottom_stable >= 5:
                        break

                capture_dramawave_rank_labels()
            else:
                for _ in range(4):
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

            if platform == "ShortMax":
                live_items = page.evaluate("""
() => {
  const clean = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,[role="heading"]'));
  const heading = headings.find(el => clean(el.textContent).toLowerCase().startsWith('most popular'));
  if (!heading) return [];
  const section = heading.closest('section') || heading.parentElement?.parentElement;
  if (!section) return [];
  const cards = Array.from(section.querySelectorAll('.drama-card, .card-item'));
  const out = [];
  const seen = new Set();
  for (const card of cards) {
    const titleEl = card.querySelector('.card-title, .overlay-title, [class*="card-title"]');
    const linkEl = card.querySelector('a.card-title-layout, a.card-text, a.overlay-title, a[href*="/drama/"]');
    const title = clean(titleEl && titleEl.textContent);
    const href = linkEl && linkEl.href ? String(linkEl.href) : '';
    const key = title.toLowerCase().replace(/[^a-z0-9]+/g, '');
    if (!title || !key || seen.has(key)) continue;
    seen.add(key);
    out.push({title, href});
    if (out.length >= 20) break;
  }
  return out;
}
""")
                rows = []
                for idx, item in enumerate(live_items[:TOP_N], start=1):
                    title = base.clean(item.get("title"), 500) if isinstance(item, dict) else ""
                    if not title:
                        continue
                    row = {"rank": idx, "title": title}
                    url = base.clean(item.get("href"), 1200) if isinstance(item, dict) else ""
                    if url:
                        row["source_url"] = url
                    rows.append(row)
                adapter_meta = {
                    "structuredData": "ShortMax live DOM Most Popular section",
                    "renderedCardCount": len(live_items),
                    "sectionScoped": True,
                    "semanticVerified": bool(live_items),
                    "semanticExpected": "Most Popular",
                }
                found = bool(live_items)
            else:
                rows, adapter_meta, found = _parse_exact(platform, document, collection_date)
                if platform == "DramaWave":
                    # Merge the final DOM parse with rank labels observed while scrolling.
                    # Never replace a larger verified final-DOM set with a smaller scroll snapshot.
                    merged_rank_titles: dict[int, set[str]] = {}
                    for row in rows or []:
                        try:
                            rank = int(row.get("rank"))
                        except Exception:
                            continue
                        title = base.clean(row.get("title"), 500)
                        if 1 <= rank <= TOP_N and title:
                            merged_rank_titles.setdefault(rank, set()).add(title)
                    for rank, titles in dramawave_rank_titles.items():
                        if 1 <= rank <= TOP_N:
                            for title in titles:
                                clean_title = base.clean(title, 500)
                                if clean_title:
                                    merged_rank_titles.setdefault(rank, set()).add(clean_title)

                    conflicts = {
                        rank: sorted(titles)
                        for rank, titles in merged_rank_titles.items()
                        if len(titles) > 1
                    }
                    rows = [
                        {"rank": rank, "title": next(iter(merged_rank_titles[rank]))}
                        for rank in sorted(merged_rank_titles)
                        if len(merged_rank_titles[rank]) == 1
                    ]
                    if rows:
                        found = True
                    adapter_meta = {
                        **adapter_meta,
                        "rankCaptureMethod": "explicit Most Trending labels merged from final DOM and scrolling snapshots",
                        "explicitRanksSeen": sorted(merged_rank_titles),
                        "explicitRankConflicts": conflicts,
                        "scrollRounds": dramawave_scroll_rounds,
                        "finalScrollMetrics": dramawave_final_metrics,
                    }
            status_code = int(response.status) if response is not None else None
            evidence = {
                "pageTitle": base.clean(page.title(), 300),
                "pageUrl": base.clean(page.url, 1200),
                "documentLang": base.clean(page.locator("html").get_attribute("lang"), 40),
                "httpStatus": status_code,
                "fetchedAt": datetime.now(timezone.utc).isoformat(),
                "browserAttempt": attempt,
                "mobileProfile": bool(profile["is_mobile"]),
                **adapter_meta,
                "rawHtmlSha256": base.sha256_bytes(raw),
                "rawHtmlFile": str(html_path.relative_to(base.ROOT)),
                "screenshotFile": str(screenshot_path.relative_to(base.ROOT)),
                "screenshotSha256": base.sha256_bytes(screenshot_path.read_bytes()),
            }
            if platform == "DramaWave":
                for row in rows or []:
                    try:
                        rank = int(row.get("rank"))
                    except Exception:
                        continue
                    title = base.clean(row.get("title"), 500)
                    if 1 <= rank <= TOP_N and title:
                        dramawave_profile_rank_titles.setdefault(rank, set()).add(title)

                profile_conflicts = {
                    rank: sorted(titles)
                    for rank, titles in dramawave_profile_rank_titles.items()
                    if len(titles) > 1
                }
                combined_rows = [
                    {"rank": rank, "title": next(iter(dramawave_profile_rank_titles[rank]))}
                    for rank in sorted(dramawave_profile_rank_titles)
                    if len(dramawave_profile_rank_titles[rank]) == 1
                ]
                evidence = {
                    **evidence,
                    "profileMergeMethod": "merge explicit Most Trending ranks across mobile and desktop profiles",
                    "profileMergedRanks": [row["rank"] for row in combined_rows],
                    "profileRankConflicts": profile_conflicts,
                }
                last_evidence = evidence
                if len(combined_rows) >= TOP_N or attempt == len(profiles):
                    return combined_rows, evidence, bool(combined_rows)
                continue

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
