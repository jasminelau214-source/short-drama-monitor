from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

import pilot_web_top10_v3 as current

base = current.base
ROOT = Path(__file__).resolve().parent
OUT_ROOT = ROOT / "readiness_audit"
TZ = ZoneInfo("Asia/Shanghai")
PLATFORMS = ("FlexTV", "MoboReels", "NetShort", "ReelShort")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-09-20")
    args = parser.parse_args()

    scope = base.load_scope()
    cfgs = {
        str(item.get("platform")): item
        for item in scope.get("platforms") or []
        if isinstance(item, dict)
    }
    data_dir = base.DATA_ROOT / args.date
    evidence_dir = base.EVIDENCE_ROOT / args.date
    audit_dir = OUT_ROOT / args.date / "phase_c"
    data_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for platform in PLATFORMS:
                cfg = cfgs[platform]
                result = base.collect_one(browser, cfg, args.date, evidence_dir)
                payload = result.as_dict()
                (data_dir / f"{platform}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                direct_primary = (
                    result.strategy == "verified_parser"
                    and result.extraction_status == "PASS_VERIFIED"
                    and str((result.evidence or {}).get("collectorVersion") or "")
                    == "exact-direct-http-v1"
                )
                results.append(
                    {
                        "platform": platform,
                        "status": result.extraction_status,
                        "strategy": result.strategy,
                        "collectorVersion": (result.evidence or {}).get("collectorVersion"),
                        "rowCount": len(result.rows),
                        "directPrimaryActive": direct_primary,
                        "fallbackUsed": result.strategy != "verified_parser",
                    }
                )
        finally:
            browser.close()

    payload = {
        "collectionDate": args.date,
        "generatedAt": datetime.now(TZ).isoformat(),
        "productionWrite": False,
        "platforms": results,
        "allDirectPrimaryActive": all(x["directPrimaryActive"] for x in results),
    }
    (audit_dir / "selected_path_collection.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
