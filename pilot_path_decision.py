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


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=local_today())
    args = parser.parse_args()
    day = DATA_ROOT / args.date

    summary = load_json(day / "summary.json", {"platforms": []})
    discovery = load_json(day / "api_discovery.json", {"platforms": {}})
    dmap = discovery.get("platforms") or {}

    alt = load_json(day / "alternate_sources.json", {"sources": []})
    amap = {str(x.get("platform")): x for x in (alt.get("sources") or []) if isinstance(x, dict)}

    dw_probe = load_json(day / "dramawave_rank_probe.json", {})
    dw_probe_complete = bool(dw_probe.get("top10_complete")) and not (dw_probe.get("rank_conflicts") or {})
    dw_probe_count = len(dw_probe.get("explicit_ranks") or [])
    dw_probe_status = dw_probe.get("status")

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

        rank_probe = None
        if platform == "DramaWave":
            rank_probe = {
                "status": dw_probe_status,
                "explicit_rank_count": dw_probe_count,
                "top10_complete": dw_probe_complete,
                "missing_top10_ranks": dw_probe.get("missing_top10_ranks") or [],
                "rank_conflicts": dw_probe.get("rank_conflicts") or {},
                "source_type": dw_probe.get("source_type"),
                "target": dw_probe.get("target"),
            }

        if status in {"PASS_VERIFIED", "PASS_CANDIDATE"} and rows == 10:
            cls = "WEB_TOP10_VALID"
            next_step = "CONTINUE_STABILITY_VALIDATION"
        elif platform == "DramaWave" and dw_probe_complete:
            cls = "OFFICIAL_H5_EXPLICIT_TOP10_CANDIDATE"
            next_step = "REQUIRE_USER_SCOPE_CONFIRMATION_BEFORE_PROMOTION"
        elif (
            platform == "DramaWave"
            and rows == 5
            and dw_probe_status == "NO_EXPLICIT_RANKS"
            and alt_rows == 10
            and alt_semantics == "ordered_shelf_not_explicit_rank"
        ):
            cls = "WEB_EXPLICIT_TOP5_H5_NO_EQUIVALENT_TOP10"
            next_step = "EVALUATE_SPECIAL_TOP5_SCOPE_OR_OTHER_OFFICIAL_RANKING_ENTRY"
        elif platform == "DramaWave" and dw_probe_count:
            cls = "OFFICIAL_H5_EXPLICIT_RANK_PARTIAL"
            next_step = "CONTINUE_OFFICIAL_H5_RANK_DISCOVERY"
        elif page_status == 403:
            cls = "WEB_CLOUD_EGRESS_BLOCKED"
            next_step = "REQUIRE_ALTERNATE_CLOUD_EGRESS_OR_APP_FALLBACK"
        elif alt_rows == 10 and alt_semantics:
            cls = "ALTERNATE_API_COMPLETE_SEMANTIC_MISMATCH"
            next_step = "CONTINUE_ALT_STABILITY_BUT_DO_NOT_PROMOTE_WITHOUT_SCOPE_CONFIRMATION"
        elif 0 < rows < 10 and candidate_count == 0:
            cls = "WEB_SOURCE_EXPOSES_LT_TOP10"
            next_step = "CONTINUE_OFFICIAL_SOURCE_DISCOVERY"
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
            "rank_probe": rank_probe,
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
