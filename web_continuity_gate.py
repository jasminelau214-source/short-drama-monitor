"""Fail-closed six-platform Web observation gate. No production/queue imports."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "pilot_data"
TZ = ZoneInfo("Asia/Shanghai")
EXPECTED = {
    "DramaBox": ("Trending", "web_pilot_trending_top10", "www.dramaboxdb.com"),
    "FlexTV": ("Top in FlexTV", "web_pilot_top_in_flextv_top10", "www.flextv.cc"),
    "GoodShort": ("Top in GoodShort", "web_pilot_top_in_goodshort_top10", "www.goodshort.com"),
    "MoboReels": ("Popular Series", "web_pilot_popular_series_top10", "www.moboreels.com"),
    "NetShort": ("Trending Now", "web_pilot_trending_now_top10", "netshort.com"),
    "ReelShort": ("TOP", "web_pilot_top_shelf_top10", "www.reelshort.com"),
}
PASS = {"PASS_CANDIDATE", "PASS_VERIFIED"}


def today():
    return datetime.now(TZ).date().isoformat()


def _host(url):
    try:
        parsed = urlsplit(url)
        return parsed.hostname if parsed.scheme == "https" else None
    except (ValueError, TypeError):
        return None


def _digest(title):
    return re.sub(r"[^a-z0-9]+", "", str(title or "").casefold())


def preflight(scope):
    if not isinstance(scope, dict):
        raise ValueError("INVALID_SCOPE")
    for key in ("production_write", "research_auto_trigger", "research_queue_write"):
        if scope.get(key) is not False:
            raise ValueError("UNSAFE_SCOPE_" + key)
    if scope.get("source_type") != "OFFICIAL_WEB":
        raise ValueError("WRONG_SOURCE_TYPE")
    if (scope.get("locale"), scope.get("region"), scope.get("language")) != ("en-US", "US", "English"):
        raise ValueError("WRONG_LOCALE_OR_REGION")
    entries = scope.get("platforms")
    if not isinstance(entries, list) or len(entries) != len(EXPECTED):
        raise ValueError("WRONG_PLATFORM_COUNT")
    if set(x.get("platform") for x in entries if isinstance(x, dict)) != set(EXPECTED):
        raise ValueError("WRONG_PLATFORM_SCOPE")
    for item in entries:
        platform = item["platform"]
        ranking, target, host = EXPECTED[platform]
        if item.get("ranking_type") != ranking or item.get("target_key") != target:
            raise ValueError("RANKING_CONTRACT_CONFLICT_" + platform)
        if _host(item.get("url")) != host:
            raise ValueError("SOURCE_URL_MISMATCH_" + platform)
    return {x["platform"]: x for x in entries}


def check(platform, payload, scope_item, date):
    """Local evidence checks. Capture validity is NOT independent Truth Gate."""
    ranking, target, host = EXPECTED[platform]
    problems = []
    if not isinstance(payload, dict):
        return {"status": "MISSING", "errors": ["NO_DATA"], "rowCount": 0}
    for key, value in {
        "platform": platform, "collection_date": date, "source_type": "OFFICIAL_WEB",
        "target_key": target, "ranking_type": ranking, "top_n": 10,
        "production_write": False,
    }.items():
        if payload.get(key) != value:
            problems.append("CONTRACT_" + key.upper())
    if payload.get("source_url") != scope_item.get("url"):
        problems.append("WRONG_PAGE")
    if payload.get("status") not in PASS:
        problems.append("COLLECTOR_NOT_PASS")
    rows = payload.get("rows")
    rows = rows if isinstance(rows, list) else []
    audit = payload.get("audit") or {}
    if len(rows) != 10 or payload.get("row_count") != 10 or audit.get("batchComplete") is not True:
        problems.append("INCOMPLETE_TOP10")
    if [r.get("rank") for r in rows if isinstance(r, dict)] != list(range(1, 11)):
        problems.append("INVALID_RANKS")
    titles = [_digest(r.get("title")) for r in rows if isinstance(r, dict)]
    if len(titles) != 10 or not all(titles) or len(set(titles)) != 10:
        problems.append("INVALID_TITLES")
    for row in rows:
        if not isinstance(row, dict):
            problems.append("INVALID_ROW")
            break
        if row.get("source_url") and _host(row["source_url"]) != host:
            problems.append("CROSS_DOMAIN_TITLE_URL")
            break
    evidence = payload.get("evidence") or {}
    if not isinstance(evidence, dict):
        evidence = {}
    if platform == "DramaBox":
        source = evidence.get("sourceEvidence") or evidence.get("payloadEvidence") or {}
        if not isinstance(source, dict) or not (
            source.get("raw_html_sha256") or source.get("rawHtmlSha256") or source.get("url")
        ):
            problems.append("NO_SOURCE_WITNESS")
    elif platform == "GoodShort":
        source = evidence.get("payloadEvidence") or {}
        if source.get("section") != "Top in GoodShort" or source.get("row_count") != 10:
            problems.append("WRONG_SECTION_WITNESS")
    else:
        if evidence.get("pageUrl") != scope_item.get("url") or not evidence.get("rawHtmlSha256"):
            problems.append("NO_PAGE_WITNESS")
        expected_structured = {
            "FlexTV": "JSON-LD ItemList",
            "MoboReels": "MoboReels Popular Series DOM",
            "NetShort": "JSON-LD ItemList",
            "ReelShort": "Next.js __NEXT_DATA__ pageProps.list",
        }[platform]
        if evidence.get("structuredData") != expected_structured:
            problems.append("WRONG_RANKING_WITNESS")
        if platform == "NetShort" and evidence.get("itemListName") != "Trending Now":
            problems.append("WRONG_ITEMLIST_NAME")
        if platform == "ReelShort" and str(evidence.get("shelfName", "")).upper() != "TOP":
            problems.append("WRONG_SHELF_NAME")
    if problems:
        return {"status": "REJECTED_OR_PARTIAL", "errors": sorted(set(problems)), "rowCount": len(rows)}
    # FlexTV has an unnamed JSON-LD ItemList: incomplete independent ranking witness.
    review = ["FLEXTV_UNNAMED_ITEMLIST_REQUIRES_TRUTH_REVIEW"] if platform == "FlexTV" and not evidence.get("itemListName") else []
    return {
        "status": "STRUCTURE_PASS_TRUTH_PENDING" if review else "OBSERVATION_CAPTURED_TRUTH_PENDING",
        "errors": review, "rowCount": len(rows),
    }


def build_report(date, scope, read=lambda path: json.loads(path.read_text(encoding="utf-8"))):
    entries = preflight(scope)
    results = {}
    for name in EXPECTED:
        path = DATA / date / (name + ".json")
        try:
            payload = read(path)
            result = check(name, payload, entries[name], date)
            result["sha256"] = hashlib.sha256(
                json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            result = {"status": "MISSING", "errors": [type(exc).__name__], "rowCount": 0}
        results[name] = result
    captured = sum(x["status"].endswith("_TRUTH_PENDING") for x in results.values())
    return {
        "collectionDate": date,
        "generatedAt": datetime.now(TZ).isoformat(),
        "sourceType": "OFFICIAL_WEB",
        "language": "English", "locale": "en-US", "region": "US",
        "expectedPlatforms": len(EXPECTED),
        "capturedPlatforms": captured,
        "coverage": f"{captured}/{len(EXPECTED)}",
        "productionWrite": False, "researchAutoTrigger": False,
        "promotionState": "BLOCK_PROMOTION",
        "businessAcceptance": "NOT_RUN",
        "platforms": results,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=today())
    ap.add_argument("--validate-only", action="store_true")
    args = ap.parse_args(argv)
    scope = json.loads((ROOT / "pilot_scope.json").read_text(encoding="utf-8"))
    preflight(scope)
    if not args.validate_only:
        # Import v3 only after preflight, so its verified adapters are installed.
        import pilot_web_top10_v3  # noqa: F401
        import pilot_web_top10 as engine
        engine.main(["--date", args.date])
    report = build_report(args.date, scope)
    folder = DATA / args.date
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "V2_CAPTURE_GATE.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (DATA / "LATEST_V2_CAPTURE_GATE.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"date": args.date, "coverage": report["coverage"], "platforms": {
        k: v["status"] for k, v in report["platforms"].items()
    }}, ensure_ascii=False))
    return 0 if report["capturedPlatforms"] == len(EXPECTED) else 2


if __name__ == "__main__":
    raise SystemExit(main())
