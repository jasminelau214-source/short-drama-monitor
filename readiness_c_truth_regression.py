from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

from readiness_truth_audit import TARGETS, audit_platform

ROOT = Path(__file__).resolve().parent
OUT_ROOT = ROOT / "readiness_audit"
TZ = ZoneInfo("Asia/Shanghai")
PLATFORMS = ("FlexTV", "MoboReels", "NetShort", "ReelShort")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-09-20")
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            results = [
                audit_platform(browser, args.date, platform, TARGETS[platform])
                for platform in PLATFORMS
            ]
        finally:
            browser.close()

    gate = "PASS" if all(x.get("status") == "PASS_TRUTH" for x in results) else "BLOCKED"
    payload = {
        "collectionDate": args.date,
        "generatedAt": datetime.now(TZ).isoformat(),
        "phase": "C_DIRECT_PATH_TRUTH_REGRESSION",
        "productionWrite": False,
        "truthRegressionGate": gate,
        "platforms": results,
        "statuses": {x["platform"]: x.get("status") for x in results},
        "evidenceLevels": {
            x["platform"]: (x.get("truthEvidence") or {}).get("level")
            for x in results
        },
    }

    out_dir = OUT_ROOT / args.date / "phase_c"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "truth_regression.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "truthRegressionGate": gate,
                "statuses": payload["statuses"],
                "evidenceLevels": payload["evidenceLevels"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
