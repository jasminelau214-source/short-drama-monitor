"""Fail-closed offline verification of four V2 OFFICIAL_WEB shelves.
No external requests, Supabase writes, research triggers or production promotion.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from datetime import datetime

PLATFORMS = {
    "FlexTV": {"heading": "Top in FlexTV", "rank_origin": "JSON_LD_POSITION"},
    "MoboReels": {"heading": "Popular Series", "rank_origin": "DISPLAY_ORDER_ONLY"},
    "NetShort": {"heading": "Trending Now", "rank_origin": "JSON_LD_POSITION"},
    "ReelShort": {"heading": "TOP", "rank_origin": "DISPLAY_ORDER_ONLY"},
}

def clean(value):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", " ", str(value or "")))).strip()

def key(value):
    return re.sub(r"[^a-z0-9]", "", clean(value).casefold())

class VisiblePage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.script_depth = 0
        self.h2_buffer = None
        self.a_buffer = None
        self.section = ""
        self.anchors = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.script_depth += 1
            return
        if self.script_depth:
            return
        if tag == "h2":
            self.h2_buffer = []
        if tag == "a":
            self.a_buffer = []

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.script_depth = max(0, self.script_depth - 1)
            return
        if self.script_depth:
            return
        if tag == "h2" and self.h2_buffer is not None:
            self.section = clean(" ".join(self.h2_buffer))
            self.h2_buffer = None
        elif tag == "a" and self.a_buffer is not None:
            self.anchors.append((self.section, clean(" ".join(self.a_buffer))))
            self.a_buffer = None

    def handle_data(self, data):
        if self.script_depth:
            return
        if self.h2_buffer is not None:
            self.h2_buffer.append(data)
        if self.a_buffer is not None:
            self.a_buffer.append(data)

def itemlists(html):
    def walk(v):
        if isinstance(v, dict):
            types = v.get("@type", [])
            if types == "ItemList" or (isinstance(types, list) and "ItemList" in types):
                yield v
            for child in v.values():
                yield from walk(child)
        elif isinstance(v, list):
            for child in v:
                yield from walk(child)
    scripts = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S)
    for body in scripts:
        try:
            yield from walk(json.loads(body))
        except (ValueError, TypeError):
            continue

def items_from_ld(itemlist):
    out = []
    for entry in itemlist.get("itemListElement") or []:
        if not isinstance(entry, dict):
            continue
        item = entry.get("item") if isinstance(entry.get("item"), dict) else entry
        try:
            rank = int(entry.get("position"))  # Never fabricate missing rank.
        except (TypeError, ValueError):
            return []
        out.append((rank, clean(item.get("name") or entry.get("name"))))
    return out

def verify(platform, payload, html, png, evidence_name=""):
    errors = []
    rows = payload.get("rows") or []
    expected = [key(r.get("title")) for r in rows if isinstance(r, dict)]
    if (payload.get("platform") != platform or payload.get("source_type") != "OFFICIAL_WEB"
            or payload.get("production_write") is not False or payload.get("row_count") != 10
            or len(rows) != 10 or [r.get("rank") for r in rows if isinstance(r, dict)] != list(range(1, 11))
            or len(expected) != 10 or not all(expected) or len(set(expected)) != 10
            or (payload.get("audit") or {}).get("batchComplete") is not True):
        errors.append("ROW_OR_SOURCE_CONTRACT_FAIL")
    evidence = payload.get("evidence") or {}
    html_digest = hashlib.sha256(html).hexdigest() if html else ""
    png_digest = hashlib.sha256(png).hexdigest() if png else ""
    if not html or html_digest != evidence.get("rawHtmlSha256"):
        errors.append("RAW_HTML_HASH_MISMATCH_OR_MISSING")
    if not png or png_digest != evidence.get("screenshotSha256"):
        errors.append("SCREENSHOT_HASH_MISMATCH_OR_MISSING")
    if evidence.get("httpStatus") != 200 or evidence.get("pageUrl") != payload.get("source_url"):
        errors.append("PAGE_OR_HTTP_WITNESS_MISMATCH")
    html = (html or b"").decode("utf-8", "replace")
    if not re.search(r'<html\b[^>]*\blang=["\']en(?:-US)?["\']', html, re.I):
        errors.append("SOURCE_LANGUAGE_UNVERIFIED")
    visible = VisiblePage()
    visible.feed(html)
    witness = {}
    if platform == "FlexTV":
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
        dom = [key(x) for x in re.findall(r"<h3[^>]*>(.*?)</h3>", html, re.I | re.S)]
        matches = [z for z in itemlists(html)
                   if [r for r, _ in items_from_ld(z)[:10]] == list(range(1, 11))
                   and [key(t) for _, t in items_from_ld(z)[:10]] == expected]
        if not h1 or "Top in FlexTV" not in clean(h1.group(1)):
            errors.append("FLEXTV_PAGE_HEADING_MISMATCH")
        if dom[:10] != expected:
            errors.append("FLEXTV_JSONLD_DOM_ORDER_CONFLICT")
        if len(matches) != 1:
            errors.append("FLEXTV_AMBIGUOUS_OR_WRONG_ITEMLIST")
        witness = {"pageHeading": clean(h1.group(1)) if h1 else "", "matchingItemLists": len(matches), "domCards": len(dom)}
    elif platform == "MoboReels":
        h2 = re.search(r'<h2[^>]*class=["\'][^"\']*home-list-title[^"\']*["\'][^>]*>\s*Popular Series\s*</h2>', html, re.I | re.S)
        if not h2:
            errors.append("MOBORREELS_POPULAR_SECTION_MISSING")
            titles = []
        else:
            start = h2.end()
            next_block = re.search(r'<div[^>]*class=["\'][^"\']*home-list(?:\s|["\'])', html[start:], re.I)
            section = html[start:start+next_block.start()] if next_block else html[start:start+180000]
            titles = [key(x) for x in re.findall(r'<h3[^>]*class=["\'][^"\']*home-list-item-title[^"\']*["\'][^>]*>(.*?)</h3>', section, re.I | re.S)]
        if titles[:10] != expected:
            errors.append("MOBORREELS_DISPLAY_ORDER_MISMATCH")
        witness = {"sectionHeading": "Popular Series" if h2 else "", "visibleCards": len(titles), "explicitNumericRanks": False}
    elif platform == "NetShort":
        lists = [z for z in itemlists(html) if clean(z.get("name")) == "Trending Now"]
        named = [z for z in lists if [r for r, _ in items_from_ld(z)[:10]] == list(range(1, 11))
                 and [key(t) for _, t in items_from_ld(z)[:10]] == expected]
        if len(lists) != 1 or len(named) != 1:
            errors.append("NETSHORT_NAMED_ITEMLIST_MISMATCH")
        if not re.search(r"<h2[^>]*>\s*Trending Now\s*</h2>", html, re.I):
            errors.append("NETSHORT_VISIBLE_HEADING_MISSING")
        trending = [key(t) for section, t in visible.anchors if section == "Trending Now"]
        indices = [next((i for i, t in enumerate(trending) if t == name), -1) for name in expected]
        if -1 in indices or indices != sorted(indices) or len(set(indices)) != 10:
            errors.append("NETSHORT_VISIBLE_SECTION_TITLE_OR_ORDER_MISMATCH")
        witness = {"itemListName": "Trending Now" if lists else "", "explicitPositions": bool(named), "visibleSectionAnchors": len(trending)}
    elif platform == "ReelShort":
        scripts = re.findall(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.I | re.S)
        pp = {}
        if len(scripts) == 1:
            try:
                pp = json.loads(scripts[0])["props"]["pageProps"]
            except (ValueError, KeyError, TypeError):
                pass
        if (str(pp.get("shelfId")) != "51001122" or pp.get("shelfName") != "TOP"
                or pp.get("page") != 1 or pp.get("locale") != "en"):
            errors.append("REELSHORT_TOP_SHELF_ID_OR_LOCALE_MISMATCH")
        if [key(x.get("book_title")) for x in (pp.get("list") or [])[:10]] != expected:
            errors.append("REELSHORT_NEXTDATA_ORDER_MISMATCH")
        anchors = [key(t) for _, t in visible.anchors]
        indices = [next((i for i, t in enumerate(anchors) if t == name), -1) for name in expected]
        if -1 in indices or indices != sorted(indices) or len(set(indices)) != 10:
            errors.append("REELSHORT_DOM_ORDER_MISMATCH")
        witness = {"shelfId": pp.get("shelfId"), "shelfName": pp.get("shelfName"),
                   "page": pp.get("page"), "total": pp.get("total"), "domAnchorsMatched": sum(i >= 0 for i in indices)}
    rank_origin = PLATFORMS[platform]["rank_origin"]
    return {
        "platform": platform,
        "collectionDate": payload.get("collection_date"),
        "sourceType": "OFFICIAL_WEB",
        "rankOrigin": rank_origin,
        "semanticTarget": PLATFORMS[platform]["heading"],
        "htmlSha256": html_digest,
        "screenshotSha256": png_digest,
        "witness": witness,
        "errors": sorted(set(errors)),
        "status": "WEB_SHELF_ORDER_EVIDENCE_MATCHED" if not errors else "SEMANTIC_WITNESS_REJECTED",
        "businessLimit": ("Official Web shelf display order, NOT an explicit numeric popularity ranking"
                          if rank_origin == "DISPLAY_ORDER_ONLY" else
                          "Official Web item-list positions; independent Truth/Fault Gates NOT passed")
    }

def run(root, date):
    results = {}
    for platform in PLATFORMS:
        data = root/"pilot_data"/date/(platform+".json")
        evidence = root/"pilot_evidence"/date/(platform+".html.gz")
        pic = root/"pilot_evidence"/date/(platform+".png")
        try:
            payload = json.loads(data.read_text(encoding="utf-8"))
            item = verify(platform, payload, gzip.decompress(evidence.read_bytes()), pic.read_bytes(), evidence.name)
            if payload.get("collection_date") != date:
                item["errors"].append("STALE_DATE")
                item["status"] = "SEMANTIC_WITNESS_REJECTED"
            results[platform] = item
        except (OSError, ValueError, EOFError, KeyError) as exc:
            results[platform] = {"platform": platform, "status": "SEMANTIC_WITNESS_MISSING", "errors": [type(exc).__name__]}
    matched = sum(x["status"] == "WEB_SHELF_ORDER_EVIDENCE_MATCHED" for x in results.values())
    report = {
        "date": date, "scope": "FOUR_CANDIDATE_OFFICIAL_WEB_SURFACES",
        "matched": matched, "expected": 4,
        "classification": "WEB_SHELF_ORDER_ONLY_NOT_APP_TRUTH",
        "independentTruthGate": "NOT_PASSED",
        "faultGate": "NOT_PASSED",
        "productionPromotion": "BLOCK_PROMOTION",
        "productionWrite": False,
        "researchAutoTrigger": False,
        "platforms": results
    }
    dest = root/"pilot_data"/date/"SEMANTIC_TRUTH_AUDIT.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return report

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--date", default=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
    args = parser.parse_args()
    report = run(args.root, args.date)
    print(json.dumps({"date": args.date, "matched": report["matched"], "expected": 4,
                      "statuses": {p: x["status"] for p, x in report["platforms"].items()}}, ensure_ascii=False))
    return 0 if report["matched"] == 4 else 2

if __name__ == "__main__":
    raise SystemExit(main())
