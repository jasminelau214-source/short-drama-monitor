from __future__ import annotations

import argparse
import copy
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

import pilot_exact_web_adapters as exact
import pilot_web_top10 as base
from goodshort_collector import collect_goodshort_top
from official_web_collectors import fetch_html

ROOT = Path(__file__).resolve().parent
OUT_ROOT = ROOT / "readiness_audit"
TZ = ZoneInfo("Asia/Shanghai")
TOP_N = 10
PASS_STATUSES = {"PASS_VERIFIED", "PASS_CANDIDATE"}
ELIGIBLE = ("FlexTV", "GoodShort", "MoboReels", "NetShort", "ReelShort", "ShortMax")

SEMANTIC_LABEL = {
    "FlexTV": "Top in FlexTV",
    "GoodShort": "Top in GoodShort",
    "MoboReels": "Popular Series",
    "NetShort": "Trending Now",
    "ReelShort": "TOP",
    "ShortMax": "Most Popular",
}


SHORTMAX_ITEMS_JS = r"""
() => {
  const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
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
    const linkEl = card.querySelector(
      'a.card-title-layout, a.card-text, a.overlay-title, a[href*="/drama/"]'
    );
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
"""


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def host(url: str) -> str:
    return (urlparse(str(url or "")).hostname or "").casefold()


def is_pass(status: str) -> bool:
    return str(status or "") in PASS_STATUSES


def status_for_rows(rows: list[dict], *, verified: bool, adapter_found: bool = True) -> tuple[str, dict]:
    audit = base.audit_rows(rows)
    return (
        base.status_from_audit(
            audit,
            verified_parser=verified,
            adapter_found=adapter_found,
        ),
        audit,
    )


def load_scope() -> dict[str, dict]:
    raw = json.loads((ROOT / "pilot_scope.json").read_text(encoding="utf-8"))
    platforms = raw.get("platforms") if isinstance(raw, dict) else None
    if not isinstance(platforms, list):
        raise RuntimeError("PILOT_SCOPE_PLATFORMS_MISSING")
    return {
        str(item.get("platform")): item
        for item in platforms
        if isinstance(item, dict) and item.get("platform")
    }


def fetch_document(browser, platform: str, cfg: dict) -> dict:
    mobile = platform == "ShortMax"
    page = browser.new_page(
        viewport={"width": 390, "height": 844} if mobile else {"width": 1440, "height": 1200},
        locale="en-US",
        user_agent=(
            "Mozilla/5.0 (Linux; Android 15; Pixel 9 Pro) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Mobile Safari/537.36"
            if mobile
            else (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36"
            )
        ),
        is_mobile=mobile,
        has_touch=mobile,
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    try:
        response = page.goto(str(cfg["url"]), wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(3500)
        for _ in range(4):
            page.mouse.wheel(0, 1400)
            page.wait_for_timeout(300)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)

        document = page.content()
        extra = {}
        if platform == "ShortMax":
            live_items = page.evaluate(SHORTMAX_ITEMS_JS) or []
            mutation_applied = bool(
                page.evaluate(
                    r"""
() => {
  const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,[role="heading"]'));
  const heading = headings.find(
    el => clean(el.textContent).toLowerCase().startsWith('most popular')
  );
  if (!heading) return false;
  heading.textContent = 'Control Shelf';
  return true;
}
"""
                )
            )
            after_mutation = page.evaluate(SHORTMAX_ITEMS_JS) or []
            extra = {
                "shortMaxLiveRows": [
                    {
                        "rank": idx,
                        "title": clean(item.get("title")),
                        **({"source_url": clean(item.get("href"))} if item.get("href") else {}),
                    }
                    for idx, item in enumerate(live_items[:TOP_N], start=1)
                    if isinstance(item, dict) and clean(item.get("title"))
                ],
                "shortMaxSemanticMutationApplied": mutation_applied,
                "shortMaxRowsAfterSemanticMutation": [
                    {
                        "rank": idx,
                        "title": clean(item.get("title")),
                    }
                    for idx, item in enumerate(after_mutation[:TOP_N], start=1)
                    if isinstance(item, dict) and clean(item.get("title"))
                ],
            }

        return {
            "document": document,
            "httpStatus": int(response.status) if response is not None else None,
            "pageUrl": str(page.url or ""),
            "pageTitle": clean(page.title()),
            "method": "playwright-official-web",
            **extra,
        }
    finally:
        page.close()


def parse_document(platform: str, document: str, collection_date: str) -> tuple[list[dict], dict, bool]:
    if platform == "GoodShort":
        try:
            payload = collect_goodshort_top(
                collection_date=collection_date,
                top_n=TOP_N,
                document=document,
            )
        except Exception as exc:
            return [], {"parseError": f"{type(exc).__name__}: {exc}"}, False
        rows = [
            {
                "rank": int(row.get("rank") or idx),
                "title": clean(row.get("title")),
                **({"source_url": row.get("source_url")} if row.get("source_url") else {}),
            }
            for idx, row in enumerate(payload.get("rows") or [], start=1)
        ]
        return rows, {"structuredData": "GoodShort verified parser"}, True

    return exact._parse_exact(platform, document, collection_date)


def mutate_reelshort_shelf(document: str) -> tuple[str, bool]:
    pattern = re.compile(
        r'(<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>)(.*?)(</script>)',
        flags=re.I | re.S,
    )
    match = pattern.search(document)
    if not match:
        return document, False
    try:
        value = json.loads(match.group(2))
        page_props = value.setdefault("props", {}).setdefault("pageProps", {})
        page_props["shelfName"] = "Control Shelf"
        replacement = match.group(1) + json.dumps(value, ensure_ascii=False) + match.group(3)
        return document[: match.start()] + replacement + document[match.end() :], True
    except Exception:
        return document, False


def mutate_semantic(platform: str, document: str) -> tuple[str, bool]:
    if platform == "ReelShort":
        return mutate_reelshort_shelf(document)

    label = SEMANTIC_LABEL[platform]
    mutated, count = re.subn(
        re.escape(label),
        "Control Shelf",
        document,
        flags=re.I,
    )
    return mutated, count > 0


def source_fault_result(
    *,
    platform: str,
    scenario: str,
    document: str,
    collection_date: str,
    expected_fail_closed: bool = True,
) -> dict:
    rows, evidence, found = parse_document(platform, document, collection_date)
    status, audit = status_for_rows(
        rows,
        verified=platform == "GoodShort",
        adapter_found=found,
    )
    passes = is_pass(status)
    safe = (not passes) if expected_fail_closed else passes
    return {
        "scenario": scenario,
        "safe": safe,
        "currentStatus": status,
        "rowCount": len(rows),
        "adapterFound": found,
        "audit": audit,
        "parserEvidence": evidence,
    }


def row_faults(platform: str, baseline_rows: list[dict]) -> list[dict]:
    scenarios: list[tuple[str, list[dict]]] = []

    missing = copy.deepcopy(baseline_rows[:-1])
    scenarios.append(("missing_one_row", missing))

    dup_rank = copy.deepcopy(baseline_rows)
    if len(dup_rank) >= 2:
        dup_rank[1]["rank"] = dup_rank[0]["rank"]
    scenarios.append(("duplicate_rank", dup_rank))

    dup_title = copy.deepcopy(baseline_rows)
    if len(dup_title) >= 2:
        dup_title[1]["title"] = dup_title[0]["title"]
    scenarios.append(("duplicate_title", dup_title))

    empty_title = copy.deepcopy(baseline_rows)
    if empty_title:
        empty_title[0]["title"] = ""
    scenarios.append(("empty_title", empty_title))

    too_many = copy.deepcopy(baseline_rows)
    too_many.append({"rank": 11, "title": "Injected Extra Row"})
    scenarios.append(("unexpected_11th_row", too_many))

    out = []
    for name, rows in scenarios:
        status, audit = status_for_rows(
            rows,
            verified=platform == "GoodShort",
            adapter_found=True,
        )
        out.append(
            {
                "scenario": name,
                "safe": not is_pass(status),
                "currentStatus": status,
                "rowCount": len(rows),
                "audit": audit,
            }
        )
    return out


def timeout_wrapper_test(cfg: dict) -> dict:
    original = base.browser_probe
    try:
        def boom(*args, **kwargs):
            raise TimeoutError("fault-injection-timeout")

        base.browser_probe = boom
        fake = dict(cfg)
        fake["strategy"] = "browser_probe"
        with tempfile.TemporaryDirectory() as td:
            result = base.collect_one(
                None,
                fake,
                "2026-09-20",
                Path(td),
            )
        return {
            "scenario": "network_timeout",
            "safe": result.extraction_status == "FAIL",
            "currentStatus": result.extraction_status,
            "error": result.error,
        }
    finally:
        base.browser_probe = original


def metadata_control_findings(platform: str, cfg: dict, baseline_status: str) -> list[dict]:
    findings = []

    # Current collector status is derived from rows/audit. Browser evidence records
    # httpStatus/pageUrl, but collect_one does not feed either into status_from_audit.
    if platform != "GoodShort":
        findings.append(
            {
                "scenario": "parseable_body_with_http_503",
                "safe": False,
                "currentStatus": baseline_status,
                "classification": "CONTROL_MISSING",
                "reason": "browser path records httpStatus but current promotion status ignores it",
            }
        )
        findings.append(
            {
                "scenario": "cross_host_redirect_with_parseable_body",
                "safe": False,
                "currentStatus": baseline_status,
                "classification": "CONTROL_MISSING",
                "reason": "browser path records final pageUrl but current promotion status does not validate expected official host",
                "expectedHost": host(str(cfg.get("url") or "")),
            }
        )
    else:
        findings.append(
            {
                "scenario": "verified_parser_final_url_identity",
                "safe": False,
                "currentStatus": baseline_status,
                "classification": "OBSERVABILITY_GAP",
                "reason": "verified stdlib path persists configured URL but not the final response URL/redirect chain",
                "expectedHost": host(str(cfg.get("url") or "")),
            }
        )

    findings.append(
        {
            "scenario": "stale_replay_freshness",
            "safe": False,
            "currentStatus": baseline_status,
            "classification": "OBSERVABILITY_GAP",
            "reason": "candidate evidence has no authoritative source freshness signal; accidental replay cannot be distinguished from a legitimately unchanged ranking by rows alone",
        }
    )
    return findings


def audit_platform(browser, platform: str, cfg: dict, collection_date: str) -> dict:
    try:
        fetched = fetch_document(browser, platform, cfg)
    except Exception as exc:
        return {
            "platform": platform,
            "status": "BLOCKED_FIXTURE_UNAVAILABLE",
            "error": f"{type(exc).__name__}: {exc}",
            "scenarios": [],
        }

    if platform == "ShortMax":
        rows = fetched.get("shortMaxLiveRows") or []
        parse_evidence = {
            "structuredData": "ShortMax actual live DOM Most Popular adapter",
            "renderedCardCount": len(rows),
        }
        found = bool(rows)
    else:
        rows, parse_evidence, found = parse_document(
            platform,
            fetched["document"],
            collection_date,
        )
    baseline_status, baseline_audit = status_for_rows(
        rows,
        verified=platform == "GoodShort",
        adapter_found=found,
    )
    if not is_pass(baseline_status):
        return {
            "platform": platform,
            "status": "BLOCKED_BASELINE_NOT_PASSING",
            "baselineStatus": baseline_status,
            "baselineAudit": baseline_audit,
            "parserEvidence": parse_evidence,
            "scenarios": [],
        }

    scenarios = row_faults(platform, rows)

    blocked_page = (
        "<!doctype html><html><head><title>Access denied</title></head>"
        "<body><h1>403 Forbidden</h1><p>Please sign in</p></body></html>"
    )
    scenarios.append(
        source_fault_result(
            platform=platform,
            scenario="blocked_or_login_page",
            document=blocked_page,
            collection_date=collection_date,
        )
    )

    if platform == "ShortMax":
        changed = bool(fetched.get("shortMaxSemanticMutationApplied"))
        mutated_rows = fetched.get("shortMaxRowsAfterSemanticMutation") or []
        mutated_status, mutated_audit = status_for_rows(
            mutated_rows,
            verified=False,
            adapter_found=bool(mutated_rows),
        )
        semantic_result = {
            "scenario": "target_semantic_replaced_but_items_remain",
            "safe": bool(changed and not is_pass(mutated_status)),
            "currentStatus": mutated_status,
            "rowCount": len(mutated_rows),
            "audit": mutated_audit,
            "mutationApplied": changed,
            "parserEvidence": {
                "structuredData": "ShortMax actual live DOM adapter after heading mutation"
            },
        }
        if not changed:
            semantic_result["classification"] = "TEST_BLOCKED"
            semantic_result["reason"] = "Most Popular live heading not found for mutation"
    else:
        semantic_doc, changed = mutate_semantic(platform, fetched["document"])
        if changed:
            semantic_result = source_fault_result(
                platform=platform,
                scenario="target_semantic_replaced_but_items_remain",
                document=semantic_doc,
                collection_date=collection_date,
            )
            semantic_result["mutationApplied"] = True
        else:
            semantic_result = {
                "scenario": "target_semantic_replaced_but_items_remain",
                "safe": False,
                "classification": "TEST_BLOCKED",
                "mutationApplied": False,
                "reason": "semantic label could not be mutated in fetched source",
            }
    scenarios.append(semantic_result)

    scenarios.append(timeout_wrapper_test(cfg))
    scenarios.extend(metadata_control_findings(platform, cfg, baseline_status))

    unsafe = [x for x in scenarios if not x.get("safe")]
    confirmed_silent = [
        x
        for x in unsafe
        if x.get("scenario") == "target_semantic_replaced_but_items_remain"
        and is_pass(str(x.get("currentStatus") or ""))
    ]
    control_gaps = [
        x for x in unsafe if x.get("classification") in {"CONTROL_MISSING", "OBSERVABILITY_GAP"}
    ]
    test_blocked = [x for x in unsafe if x.get("classification") == "TEST_BLOCKED"]

    if confirmed_silent:
        status = "FAIL_SILENT_FAILURE"
    elif control_gaps or test_blocked:
        status = "BLOCKED_CONTROL_GAPS"
    elif unsafe:
        status = "FAIL_FAULT_INJECTION"
    else:
        status = "PASS_FAULT"

    return {
        "platform": platform,
        "status": status,
        "baselineStatus": baseline_status,
        "baselineRowCount": len(rows),
        "baselineAudit": baseline_audit,
        "parserEvidence": parse_evidence,
        "source": {
            "httpStatus": fetched.get("httpStatus"),
            "pageUrl": fetched.get("pageUrl"),
            "pageTitle": fetched.get("pageTitle"),
            "method": fetched.get("method"),
        },
        "scenarioCount": len(scenarios),
        "unsafeScenarioCount": len(unsafe),
        "confirmedSilentFailureCount": len(confirmed_silent),
        "controlGapCount": len(control_gaps),
        "testBlockedCount": len(test_blocked),
        "scenarios": scenarios,
    }


def render_markdown(payload: dict) -> str:
    lines = [
        f"# Production Readiness B — Fault / Silent Failure Audit — {payload['collectionDate']}",
        "",
        f"- Audit execution: **{payload['execution']}**",
        f"- B gate overall: **{payload['faultGate']}**",
        f"- Platforms tested: **{payload['platformCount']}**",
        f"- PASS_FAULT: **{payload['passCount']}**",
        f"- Confirmed silent-failure platforms: **{payload['silentFailurePlatformCount']}**",
        "",
        "| Platform | B status | Baseline | Scenarios | Unsafe | Silent failures | Control gaps |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for item in payload["platforms"]:
        lines.append(
            f"| {item['platform']} | {item['status']} | {item.get('baselineStatus','-')} | "
            f"{item.get('scenarioCount',0)} | {item.get('unsafeScenarioCount',0)} | "
            f"{item.get('confirmedSilentFailureCount',0)} | {item.get('controlGapCount',0)} |"
        )
    lines += [
        "",
        "The audit intentionally distinguishes confirmed silent failures from missing observability/controls.",
        "A green GitHub job means only the audit executed; it does not make the B gate pass.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-09-20")
    args = parser.parse_args()

    scope = load_scope()
    missing = [p for p in ELIGIBLE if p not in scope]
    if missing:
        raise RuntimeError(f"SCOPE_MISSING:{','.join(missing)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            results = [
                audit_platform(browser, platform, scope[platform], args.date)
                for platform in ELIGIBLE
            ]
        finally:
            browser.close()

    pass_count = sum(1 for x in results if x["status"] == "PASS_FAULT")
    silent_platforms = sum(
        1 for x in results if int(x.get("confirmedSilentFailureCount") or 0) > 0
    )
    fault_gate = "PASS" if pass_count == len(ELIGIBLE) else "BLOCKED"

    payload = {
        "collectionDate": args.date,
        "generatedAt": datetime.now(TZ).isoformat(),
        "phase": "B_FAULT_INJECTION",
        "productionWrite": False,
        "execution": "AUDIT_EXECUTED_SUCCESSFULLY",
        "faultGate": fault_gate,
        "platformCount": len(results),
        "passCount": pass_count,
        "silentFailurePlatformCount": silent_platforms,
        "platforms": results,
    }

    out_dir = OUT_ROOT / args.date / "phase_b"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "fault_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "FAULT_AUDIT.md").write_text(
        render_markdown(payload),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "phase": payload["phase"],
                "faultGate": fault_gate,
                "passCount": pass_count,
                "silentFailurePlatformCount": silent_platforms,
                "statuses": {x["platform"]: x["status"] for x in results},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
