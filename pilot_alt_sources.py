from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pilot_dramawave_api as dw
import pilot_shortmax_api as sm

ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "pilot_data"
TZ = ZoneInfo("Asia/Shanghai")


def local_today() -> str:
    return datetime.now(TZ).date().isoformat()


def load_web(day: Path, platform: str) -> list[dict]:
    path = day / f"{platform}.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("rows") or []
    except Exception:
        return []


def overlap(a: list[dict], b: list[dict]) -> dict:
    na = {str(x.get("title") or "").strip().casefold() for x in a if x.get("title")}
    nb = {str(x.get("title") or "").strip().casefold() for x in b if x.get("title")}
    inter = sorted(na & nb)
    return {"count": len(inter), "titles": inter}


def collect_dramawave() -> dict:
    device_id = uuid.uuid4().hex
    login = dw.request("/h5-api/anonymous/login", method="POST", body={"device_id": device_id}, device_id=device_id)
    data = login.get("data") or {}
    auth_key = str(data.get("auth_key") or "")
    auth_secret = str(data.get("auth_secret") or "")
    if not auth_key or not auth_secret:
        raise RuntimeError("DramaWave anonymous login missing auth fields")
    auth = dw.auth_header(auth_key, auth_secret)

    tabs = dw.request("/h5-api/homepage/v2/tab/list", auth=auth, device_id=device_id)
    tab_list = (tabs.get("data") or {}).get("list") or []
    tab = next((x for x in tab_list if isinstance(x, dict) and x.get("business_name") == "popular"), None)
    if not tab:
        raise RuntimeError("DramaWave popular tab not found")
    tab_key = tab.get("tab_key")
    pidx = tab.get("position_index", 0)
    idx = dw.request(
        f"/h5-api/homepage/v2/tab/index?tab_key={tab_key}&position_index={pidx}&first=",
        auth=auth,
        device_id=device_id,
    )
    modules = (idx.get("data") or {}).get("items") or []
    mod = next(
        (
            m for m in modules
            if isinstance(m, dict)
            and (
                str(m.get("module_name") or "").casefold() == "popular choices"
                or (m.get("type") == "recommend" and isinstance(m.get("items"), list))
            )
        ),
        None,
    )
    if not mod:
        raise RuntimeError("DramaWave Popular Choices module not found")
    rows = []
    for i, item in enumerate((mod.get("items") or [])[:10], 1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("series_name") or item.get("name") or "").strip()
        if not title:
            continue
        rows.append({
            "position": i,
            "title": title,
            "series_key": item.get("key") or item.get("series_id") or item.get("id"),
        })
    return {
        "platform": "DramaWave",
        "source_type": "OFFICIAL_H5_API_PILOT",
        "semantic_type": "ordered_shelf_not_explicit_rank",
        "target": "Popular Choices",
        "row_count": len(rows),
        "rows": rows,
        "production_write": False,
        "notes": "Anonymous H5 API exposes 10 ordered Popular Choices items; this is not the same as the webpage's explicit Most Trending labels.",
    }


def collect_shortmax() -> dict:
    page = sm.post(
        "/cmsShortPlay/queryPage",
        {"pageNo": 1, "pageSize": 100, "labelId": "", "classId": ""},
    )
    pdata = page.get("data") or {}
    items = [x for x in (pdata.get("list") or []) if isinstance(x, dict) and sm.title(x)]
    ordered = sorted(
        items,
        key=lambda x: (
            x.get("playNum") or 0,
            x.get("collectNum") or 0,
            x.get("shortPlayId") or 0,
        ),
        reverse=True,
    )
    rows = [
        {
            "position": i,
            "title": sm.title(item),
            "short_play_id": item.get("shortPlayId"),
            "play_num": item.get("playNum"),
            "collect_num": item.get("collectNum"),
        }
        for i, item in enumerate(ordered[:10], 1)
    ]
    return {
        "platform": "ShortMax",
        "source_type": "OFFICIAL_WEB_API_PILOT",
        "semantic_type": "derived_metric_play_num_not_official_shelf_rank",
        "target": "Catalog sorted by playNum desc",
        "catalog_total": pdata.get("total"),
        "row_count": len(rows),
        "rows": rows,
        "production_write": False,
        "notes": "The official API exposes a catalog and playNum metrics, but no verified Most Popular rank endpoint. Sorting by playNum is a derived metric and must not replace the Web shelf without confirmation.",
    }


def main() -> int:
    collection_date = local_today()
    day = DATA_ROOT / collection_date
    day.mkdir(parents=True, exist_ok=True)
    payload = {
        "collection_date": collection_date,
        "generated_at": datetime.now(TZ).isoformat(),
        "production_write": False,
        "promotion_requires_user_confirmation": True,
        "sources": [],
    }

    for collector, platform in ((collect_dramawave, "DramaWave"), (collect_shortmax, "ShortMax")):
        try:
            item = collector()
            item["web_overlap"] = overlap(load_web(day, platform), item.get("rows") or [])
            payload["sources"].append(item)
        except Exception as exc:
            payload["sources"].append({
                "platform": platform,
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "production_write": False,
            })

    (day / "alternate_sources.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        x["platform"]: {
            "row_count": x.get("row_count", 0),
            "semantic_type": x.get("semantic_type"),
            "web_overlap": (x.get("web_overlap") or {}).get("count"),
            "error": x.get("error"),
        }
        for x in payload["sources"]
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
