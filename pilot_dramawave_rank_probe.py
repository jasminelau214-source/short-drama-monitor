from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pilot_dramawave_api as dw

ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "pilot_data"
TZ = ZoneInfo("Asia/Shanghai")


def local_today() -> str:
    return datetime.now(TZ).date().isoformat()


def merge_rank(target: dict[int, dict], conflicts: dict[int, list[dict]], row: dict, source: dict) -> None:
    rank = int(row.get("rank") or 0)
    if not 1 <= rank <= 30:
        return
    normalized = {
        "rank": rank,
        "title": row.get("title"),
        "series_key": row.get("series_key"),
        "source": source,
    }
    existing = target.get(rank)
    if existing is None:
        target[rank] = normalized
        return
    if (existing.get("title"), existing.get("series_key")) != (normalized.get("title"), normalized.get("series_key")):
        conflicts.setdefault(rank, [existing]).append(normalized)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=local_today())
    parser.add_argument("--max-pages", type=int, default=60)
    args = parser.parse_args()

    day = DATA_ROOT / args.date
    day.mkdir(parents=True, exist_ok=True)
    output = {
        "platform": "DramaWave",
        "collection_date": args.date,
        "generated_at": datetime.now(TZ).isoformat(),
        "source_type": "OFFICIAL_H5_API_RANK_PROBE",
        "target": "explicit Most Trending labels",
        "production_write": False,
        "promotion_requires_user_confirmation": True,
        "status": "ERROR",
        "top10_complete": False,
        "explicit_ranks": [],
        "missing_top10_ranks": list(range(1, 11)),
        "rank_conflicts": {},
        "tabs": [],
        "error": "",
        "notes": (
            "Evidence-only probe. It accepts only explicit ordinal labels such as "
            "'1st Most Trending'; ordered shelves without explicit ranking semantics "
            "are never promoted by this probe."
        ),
    }

    try:
        device_id = uuid.uuid4().hex
        login = dw.request(
            "/h5-api/anonymous/login",
            method="POST",
            body={"device_id": device_id},
            device_id=device_id,
        )
        data = login.get("data") or {}
        auth_key = str(data.get("auth_key") or "")
        auth_secret = str(data.get("auth_secret") or "")
        if not auth_key or not auth_secret:
            raise RuntimeError("DramaWave anonymous login missing auth fields")
        auth = dw.auth_header(auth_key, auth_secret)

        tabs = dw.request("/h5-api/homepage/v2/tab/list", auth=auth, device_id=device_id)
        tab_list = (tabs.get("data") or {}).get("list") or []

        by_rank: dict[int, dict] = {}
        conflicts: dict[int, list[dict]] = {}

        for tab in tab_list[:20]:
            if not isinstance(tab, dict):
                continue
            tab_key = tab.get("tab_key")
            if not tab_key:
                continue
            pidx = tab.get("position_index", 0)
            idx = dw.request(
                f"/h5-api/homepage/v2/tab/index?tab_key={tab_key}&position_index={pidx}&first=",
                auth=auth,
                device_id=device_id,
            )
            idata = idx.get("data") or {}
            modules = idata.get("items") if isinstance(idata.get("items"), list) else []
            tab_record = {
                "tab_key": tab_key,
                "position_index": pidx,
                "business_name": tab.get("business_name"),
                "title": tab.get("title") or tab.get("name"),
                "module_count": len(modules),
                "modules": [],
            }

            for module in modules:
                if not isinstance(module, dict):
                    continue
                module_record = dw.clean_module(module)
                items = module.get("items") if isinstance(module.get("items"), list) else []
                initial = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    rank = dw.explicit_trending_rank(item)
                    if not rank:
                        continue
                    title = str(item.get("title") or item.get("series_name") or item.get("name") or "").strip()
                    if not title:
                        continue
                    row = {
                        "rank": rank,
                        "title": title,
                        "series_key": item.get("key") or item.get("series_id") or item.get("id"),
                    }
                    initial.append(row)
                    merge_rank(
                        by_rank,
                        conflicts,
                        row,
                        {
                            "tab_key": tab_key,
                            "business_name": tab.get("business_name"),
                            "module_key": module.get("module_key"),
                            "module_name": module.get("module_name") or module.get("title") or module.get("name"),
                            "phase": "initial",
                        },
                    )

                module_record["initial_explicit_ranks"] = initial
                if module.get("type") == "recommend" and isinstance(module.get("items"), list):
                    scan = dw.scan_recommend_feed(
                        module=module,
                        page_info=idata.get("page_info") or {},
                        auth=auth,
                        device_id=device_id,
                        max_pages=args.max_pages,
                    )
                    module_record["recommend_feed_rank_scan"] = scan
                    for row in scan.get("explicit_ranks") or []:
                        merge_rank(
                            by_rank,
                            conflicts,
                            row,
                            {
                                "tab_key": tab_key,
                                "business_name": tab.get("business_name"),
                                "module_key": module.get("module_key"),
                                "module_name": module.get("module_name") or module.get("title") or module.get("name"),
                                "phase": "feed",
                                "pages_scanned": scan.get("pages_scanned"),
                                "items_scanned": scan.get("items_scanned"),
                            },
                        )
                tab_record["modules"].append(module_record)
            output["tabs"].append(tab_record)

        ranks = [by_rank[r] for r in sorted(by_rank)]
        missing = [r for r in range(1, 11) if r not in by_rank]
        output["explicit_ranks"] = ranks
        output["missing_top10_ranks"] = missing
        output["top10_complete"] = not missing
        output["rank_conflicts"] = {str(k): v for k, v in sorted(conflicts.items())}
        output["status"] = "PASS_EXPLICIT_TOP10" if not missing and not conflicts else ("PARTIAL" if ranks else "NO_EXPLICIT_RANKS")
    except Exception as exc:
        output["error"] = f"{type(exc).__name__}: {exc}"
        output["status"] = "ERROR"

    (day / "dramawave_rank_probe.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": output["status"],
        "top10_complete": output["top10_complete"],
        "explicit_rank_count": len(output["explicit_ranks"]),
        "missing_top10_ranks": output["missing_top10_ranks"],
        "rank_conflict_count": len(output["rank_conflicts"]),
        "error": output["error"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
