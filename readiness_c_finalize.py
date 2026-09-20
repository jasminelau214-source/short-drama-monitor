from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
OUT_ROOT = ROOT / "readiness_audit"
TZ = ZoneInfo("Asia/Shanghai")

CHANGED = ("FlexTV", "MoboReels", "NetShort", "ReelShort")
ALL_SIX = ("FlexTV", "GoodShort", "MoboReels", "NetShort", "ReelShort", "ShortMax")


def read_json(path: Path) -> dict:
    if not path.exists():
        return {"_error": f"MISSING:{path.name}"}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-09-20")
    args = parser.parse_args()

    phase_c = OUT_ROOT / args.date / "phase_c"
    selected = read_json(phase_c / "selected_path_collection.json")
    truth = read_json(phase_c / "truth_regression.json")
    fault = read_json(OUT_ROOT / args.date / "phase_b" / "fault_audit.json")
    path = read_json(phase_c / "path_optimization.json")

    selected_ok = bool(selected.get("allDirectPrimaryActive"))
    truth_ok = truth.get("truthRegressionGate") == "PASS"
    fault_statuses = {
        item.get("platform"): item.get("status")
        for item in fault.get("platforms") or []
    }
    fault_ok = all(fault_statuses.get(p) == "PASS_FAULT" for p in ALL_SIX)
    path_decisions = {
        item.get("platform"): item.get("decision")
        for item in path.get("platforms") or []
    }
    expected_direct = all(
        path_decisions.get(p) == "KEEP_CURRENT_DIRECT"
        for p in (*CHANGED, "GoodShort")
    )
    shortmax_ok = path_decisions.get("ShortMax") == "KEEP_CURRENT_BROWSER"
    path_ok = (
        path.get("pathOptimizationGate") == "PASS"
        and expected_direct
        and shortmax_ok
    )

    gate = "PASS" if all((selected_ok, truth_ok, fault_ok, path_ok)) else "BLOCKED"
    final_paths = {
        "FlexTV": "DIRECT_HTTP_PRIMARY_WITH_BROWSER_FALLBACK",
        "GoodShort": "DIRECT_HTTP",
        "MoboReels": "DIRECT_HTTP_PRIMARY_WITH_BROWSER_FALLBACK",
        "NetShort": "DIRECT_HTTP_PRIMARY_WITH_BROWSER_FALLBACK",
        "ReelShort": "DIRECT_HTTP_PRIMARY_WITH_BROWSER_FALLBACK",
        "ShortMax": "BROWSER",
    }

    payload = {
        "collectionDate": args.date,
        "generatedAt": datetime.now(TZ).isoformat(),
        "phase": "C_FINAL_PATH_DECISION",
        "productionWrite": False,
        "pathOptimizationGate": gate,
        "checks": {
            "changedPlatformsUseDirectPrimary": selected_ok,
            "changedPlatformsTruthRegression": truth_ok,
            "sixPlatformFaultRegression": fault_ok,
            "finalPathComparison": path_ok,
        },
        "truthEvidenceLevels": truth.get("evidenceLevels") or {},
        "faultStatuses": fault_statuses,
        "pathDecisions": path_decisions,
        "finalPaths": final_paths,
    }
    phase_c.mkdir(parents=True, exist_ok=True)
    (phase_c / "final_path_decision.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        f"# Production Readiness C — Final Path Decision — {args.date}",
        "",
        f"- C gate: **{gate}**",
        f"- Direct-primary switch active on four candidates: **{'YES' if selected_ok else 'NO'}**",
        f"- Focused Truth regression: **{'PASS' if truth_ok else 'BLOCKED'}**",
        f"- Six-platform Fault regression: **{'PASS' if fault_ok else 'BLOCKED'}**",
        "",
        "| Platform | Final isolated-audit path |",
        "|---|---|",
    ]
    for platform in ALL_SIX:
        lines.append(f"| {platform} | {final_paths[platform]} |")
    lines += [
        "",
        "This decision is confined to the isolated C audit branch. No production path, scheduler, database, or deployment is changed.",
        "",
    ]
    (phase_c / "FINAL_PATH_DECISION.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
