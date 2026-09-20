from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

import pilot_web_top10_v3 as current
from official_web_collectors import fetch_html_with_metadata

base = current.base
exact = current.exact

ROOT = Path(__file__).resolve().parent
OUT_ROOT = ROOT / "readiness_audit"
TZ = ZoneInfo("Asia/Shanghai")
TOP_N = 10
PLATFORMS = ("FlexTV", "GoodShort", "MoboReels", "NetShort", "ReelShort", "ShortMax")
PASS_STATUSES = {"PASS_VERIFIED", "PASS_CANDIDATE"}


def ordered_titles(rows: list[dict]) -> list[str]:
    return [base.norm_title(row.get("title")) for row in rows[:TOP_N]]


def load_scope() -> dict[str, dict]:
    payload = base.load_scope()
    return {
        str(item.get("platform")): item
        for item in payload.get("platforms") or []
        if isinstance(item, dict) and item.get("platform")
    }


def direct_attempt(cfg: dict, collection_date: str) -> dict:
    started = time.perf_counter()
    platform = str(cfg.get("platform") or "")
    try:
        fetched = fetch_html_with_metadata(str(cfg["url"]))
        document = str(fetched.get("document") or "")
        rows, parser_evidence, adapter_found = exact._parse_exact(
            platform,
            document,
            collection_date,
        )
        audit = base.audit_rows(rows)
        raw_status = base.status_from_audit(
            audit,
            verified_parser=False,
            adapter_found=adapter_found,
        )
        evidence = {
            "httpStatus": fetched.get("httpStatus"),
            "pageUrl": fetched.get("pageUrl"),
            "fetchedAt": fetched.get("fetchedAt"),
            **(parser_evidence if isinstance(parser_evidence, dict) else {}),
        }
        source_control = base.audit_source_evidence(cfg, evidence)
        final_status = base._status_with_source_control(raw_status, source_control)
        return {
            "ok": final_status in PASS_STATUSES,
            "status": final_status,
            "rawStatus": raw_status,
            "rows": rows,
            "rowCount": len(rows),
            "audit": audit,
            "sourceControl": source_control,
            "parserEvidence": parser_evidence,
            "durationSeconds": round(time.perf_counter() - started, 3),
            "error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "FAIL",
            "rawStatus": "FAIL",
            "rows": [],
            "rowCount": 0,
            "audit": base.audit_rows([]),
            "sourceControl": {"pass": False, "errors": ["DIRECT_ATTEMPT_EXCEPTION"]},
            "parserEvidence": {},
            "durationSeconds": round(time.perf_counter() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def current_attempt(browser, cfg: dict, collection_date: str, evidence_dir: Path) -> dict:
    started = time.perf_counter()
    result = base.collect_one(browser, cfg, collection_date, evidence_dir)
    return {
        "ok": result.extraction_status in PASS_STATUSES,
        "status": result.extraction_status,
        "rows": result.rows,
        "rowCount": len(result.rows),
        "audit": result.audit,
        "evidence": result.evidence,
        "durationSeconds": round(time.perf_counter() - started, 3),
        "error": result.error,
        "strategy": result.strategy,
    }


def decide(cfg: dict, incumbent: dict, direct: list[dict]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not incumbent.get("ok"):
        return "BLOCKED_INCUMBENT_INVALID", ["current production-candidate path did not pass"]

    if len(direct) != 3:
        return "BLOCKED_DIRECT_TEST_INCOMPLETE", ["direct HTTP comparison did not run three attempts"]

    direct_valid = all(item.get("ok") for item in direct)
    direct_orders = [ordered_titles(item.get("rows") or []) for item in direct]
    direct_stable = direct_valid and all(order == direct_orders[0] for order in direct_orders[1:])
    current_order = ordered_titles(incumbent.get("rows") or [])
    exact_match = direct_stable and all(order == current_order for order in direct_orders)

    if not direct_valid:
        bad = [str(item.get("status")) for item in direct if not item.get("ok")]
        reasons.append("direct HTTP did not pass all source/structure gates: " + ",".join(bad))
    if direct_valid and not direct_stable:
        reasons.append("direct HTTP produced inconsistent ordered Top10 across repeated attempts")
    if direct_stable and not exact_match:
        reasons.append("direct HTTP Top10 did not exactly match incumbent near-time output")

    current_strategy = str(cfg.get("strategy") or "")
    if current_strategy == "verified_parser":
        if direct_stable and exact_match:
            reasons.append("incumbent is already direct/server-rendered and passed the same safeguards")
            return "KEEP_CURRENT_DIRECT", reasons
        reasons.append("no safer or simpler replacement was demonstrated")
        return "KEEP_CURRENT", reasons

    if direct_stable and exact_match:
        direct_avg = statistics.mean(float(x.get("durationSeconds") or 0.0) for x in direct)
        current_seconds = float(incumbent.get("durationSeconds") or 0.0)
        reasons.extend(
            [
                "three direct HTTP attempts passed structure, semantics, host, HTTP and freshness gates",
                "all three ordered Top10 lists exactly matched the incumbent",
                "direct HTTP removes the Playwright/browser runtime dependency",
                f"observed duration: direct avg {direct_avg:.3f}s vs incumbent {current_seconds:.3f}s",
            ]
        )
        return "PREFER_DIRECT_HTTP_CANDIDATE", reasons

    reasons.append("retain browser path because equivalence was not proven")
    return "KEEP_CURRENT_BROWSER", reasons


def audit_platform(browser, cfg: dict, collection_date: str, evidence_dir: Path) -> dict:
    incumbent = current_attempt(browser, cfg, collection_date, evidence_dir)
    direct = []
    for index in range(3):
        direct.append(direct_attempt(cfg, collection_date))
        if index < 2:
            time.sleep(0.4)

    decision, reasons = decide(cfg, incumbent, direct)
    current_order = ordered_titles(incumbent.get("rows") or [])
    direct_orders = [ordered_titles(item.get("rows") or []) for item in direct]
    direct_valid_count = sum(1 for item in direct if item.get("ok"))
    direct_stable = (
        direct_valid_count == 3
        and len(direct_orders) == 3
        and all(order == direct_orders[0] for order in direct_orders[1:])
    )
    exact_match = direct_stable and all(order == current_order for order in direct_orders)

    return {
        "platform": cfg["platform"],
        "currentStrategy": cfg.get("strategy"),
        "current": incumbent,
        "directHttpAttempts": direct,
        "directPassCount": direct_valid_count,
        "directStable": direct_stable,
        "exactOrderedMatchToCurrent": exact_match,
        "decision": decision,
        "reasons": reasons,
        "productionWrite": False,
    }


def render_markdown(payload: dict) -> str:
    lines = [
        f"# Production Readiness C — Path Optimization — {payload['collectionDate']}",
        "",
        f"- Audit execution: **{payload['execution']}**",
        f"- C gate: **{payload['pathOptimizationGate']}**",
        f"- Platforms assessed: **{payload['platformCount']}**",
        f"- Direct HTTP replacement candidates: **{len(payload['directReplacementCandidates'])}**",
        "",
        "| Platform | Current path | Current sec | Direct pass | Direct stable | Exact Top10 | Decision |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for item in payload["platforms"]:
        lines.append(
            f"| {item['platform']} | {item.get('currentStrategy','-')} | "
            f"{float((item.get('current') or {}).get('durationSeconds') or 0):.3f} | "
            f"{item.get('directPassCount',0)}/3 | "
            f"{'YES' if item.get('directStable') else 'NO'} | "
            f"{'YES' if item.get('exactOrderedMatchToCurrent') else 'NO'} | "
            f"{item.get('decision','-')} |"
        )
    lines += [
        "",
        "Decision rule: a direct HTTP replacement is only preferred when three consecutive official-source attempts pass all safety gates and exactly match the incumbent ordered Top10.",
        "A retained browser path is a valid C PASS when the lighter path cannot prove equivalence.",
        "No production path is changed by this audit.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-09-20")
    args = parser.parse_args()

    scope = load_scope()
    missing = [platform for platform in PLATFORMS if platform not in scope]
    if missing:
        raise RuntimeError("SCOPE_MISSING:" + ",".join(missing))

    out_dir = OUT_ROOT / args.date / "phase_c"
    evidence_dir = out_dir / "current_path_evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            results = [
                audit_platform(browser, scope[platform], args.date, evidence_dir)
                for platform in PLATFORMS
            ]
        finally:
            browser.close()

    blockers = [
        item["platform"]
        for item in results
        if str(item.get("decision") or "").startswith("BLOCKED_")
    ]
    candidates = [
        item["platform"]
        for item in results
        if item.get("decision") == "PREFER_DIRECT_HTTP_CANDIDATE"
    ]
    gate = "PASS" if not blockers else "BLOCKED"

    payload = {
        "collectionDate": args.date,
        "generatedAt": datetime.now(TZ).isoformat(),
        "phase": "C_PATH_OPTIMIZATION",
        "execution": "AUDIT_EXECUTED_SUCCESSFULLY",
        "productionWrite": False,
        "pathOptimizationGate": gate,
        "platformCount": len(results),
        "blockingPlatforms": blockers,
        "directReplacementCandidates": candidates,
        "platforms": results,
    }

    (out_dir / "path_optimization.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "PATH_OPTIMIZATION.md").write_text(
        render_markdown(payload),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "phase": payload["phase"],
                "pathOptimizationGate": gate,
                "blockingPlatforms": blockers,
                "directReplacementCandidates": candidates,
                "decisions": {item["platform"]: item["decision"] for item in results},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
