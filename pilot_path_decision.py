from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "pilot_data"
TZ = ZoneInfo("Asia/Shanghai")


def local_today() -> str:
    return datetime.now(TZ).date().isoformat()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=local_today())
    args = parser.parse_args()
    day = DATA_ROOT / args.date

    summary = json.loads((day / "summary.json").read_text(encoding="utf-8"))
    discovery_path = day / "api_discovery.json"
    discovery = json.loads(discovery_path.read_text(encoding="utf-8")) if discovery_path.exists() else {"platforms": {}}
    dmap = discovery.get("platforms") or {}

    alt_path = day / "alternate_sources.json"
    alt = json.loads(alt_path.read_text(encoding="utf-8")) if alt_path.exists() else {"sources": []}
    amap = {str(x.get("platform")): x for x in (alt.get("sources") or []) if isinstance(x, dict)}

    decisions = []
    for item in summary.get("platforms") or []:
        platform = item.get("platform")
        status = item.get("status")
        rows = int(item.get("row_count") or 0)
        disc = dmap.get(platform) or {}
        page_status = disc.get("page_status")
        candidate_count = int(disc.get("candidate_count") or 0)
        alternate = amap.get(str(platform)) or {}
        alt_rows = int(alternate.get("row_count") or 0)
        alt_semantics = alternate.get("semantic_type")
        alt_target = alternate.get("target")

        if status in {"PASS_VERIFIED", "PASS_CANDIDATE"} and rows == 10:
            cls = "WEB_TOP10_VALID"
            next_step = "CONTINUE_STABILITY_VALIDATION"
        elif page_status == 403:
            cls = "WEB_CLOUD_EGRESS_BLOCKED"
            next_step = "REQUIRE_ALTERNATE_CLOUD_EGRESS_OR_APP_FALLBACK"
        elif alt_rows == 10 and alt_semantics:
            cls = "ALTERNATE_API_COMPLETE_SEMANTIC_MISMATCH"
            next_step = "CONTINUE_ALT_STABILITY_BUT_DO_NOT_PROMOTE_WITHOUT_SCOPE_CONFIRMATION"
        elif 0 < rows < 10 and candidate_count == 0:
            cls = "WEB_SOURCE_EXPOSES_LT_TOP10"
            next_step = "REQUIRE_CLOUD_APP_OR_OFFICIAL_API_FALLBACK"
        elif rows == 0 and candidate_count == 0:
            cls = "WEB_PATH_UNRESOLVED"
            next_step = "CONTINUE_CLOUD_DISCOVERY"
        else:
            cls = "WEB_PATH_NEEDS_REVIEW"
            next_step = "REVIEW_DISCOVERED_API_CANDIDATES"

        decisions.append({
            "platform": platform,
            "class": cls,
            "web_rows": rows,
            "web_status": status,
            "page_status": page_status,
            "api_candidate_count": candidate_count,
            "alternate_rows": alt_rows,
            "alternate_semantic_type": alt_semantics,
            "alternate_target": alt_target,
            "next_step": next_step,
            "production_write": False,
        })

    payload = {
        "collection_date": args.date,
        "generated_at": datetime.now(TZ).isoformat(),
        "promotion_requires_user_confirmation": True,
        "production_write": False,
        "decisions": decisions,
    }
    (day / "path_decisions.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({x["platform"]: x["class"] for x in decisions}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
