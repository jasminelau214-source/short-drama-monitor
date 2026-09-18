from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
SCOPE = ROOT / "pilot_scope.json"
DATA_ROOT = ROOT / "pilot_data"
TZ = ZoneInfo("Asia/Shanghai")
TARGETS = {"DramaBox", "DramaWave", "ShortMax"}
TITLE_KEYS = ("title", "name", "bookName", "book_name", "dramaName", "drama_name", "bookTitle", "book_title")
RANK_KEYS = ("rank", "ranking", "position", "index", "sort", "order")


def clean(value: Any, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def get_title(obj: dict[str, Any]) -> str:
    for key in TITLE_KEYS:
        value = obj.get(key)
        if isinstance(value, str) and len(clean(value)) >= 2:
            return clean(value)
    return ""


def rank_field(obj: dict[str, Any]) -> str:
    for key in RANK_KEYS:
        if key in obj and isinstance(obj.get(key), (int, float, str)):
            return key
    return ""


def walk_arrays(value: Any, path: str = "$") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, list):
        dict_items = [x for x in value if isinstance(x, dict)]
        titled = [(x, get_title(x)) for x in dict_items]
        titled = [(x, t) for x, t in titled if t]
        if len(titled) >= 5:
            sample = []
            rank_keys = []
            for obj, title in titled[:3]:
                rk = rank_field(obj)
                if rk:
                    rank_keys.append(rk)
                sample.append({"title": title, "rank_key": rk, "rank_value": obj.get(rk) if rk else None})
            found.append({
                "path": path,
                "array_len": len(value),
                "titled_items": len(titled),
                "sample": sample,
                "rank_keys_seen": sorted(set(rank_keys)),
            })
        for idx, item in enumerate(value[:60]):
            found.extend(walk_arrays(item, f"{path}[{idx}]"))
    elif isinstance(value, dict):
        for key, item in list(value.items())[:120]:
            found.extend(walk_arrays(item, f"{path}.{key}"))
    return found


def local_today() -> str:
    return datetime.now(TZ).date().isoformat()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=local_today())
    args = parser.parse_args()

    scope = json.loads(SCOPE.read_text(encoding="utf-8"))
    out_dir = DATA_ROOT / args.date
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {
        "collection_date": args.date,
        "generated_at": datetime.now(TZ).isoformat(),
        "production_write": False,
        "note": "Discovery metadata only. Candidate APIs are never promoted automatically.",
        "platforms": {},
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for cfg in scope.get("platforms") or []:
                platform = str(cfg.get("platform") or "")
                if platform not in TARGETS:
                    continue

                page = browser.new_page(
                    viewport={"width": 412, "height": 915},
                    locale="en-US",
                    user_agent=(
                        "Mozilla/5.0 (Linux; Android 15; Pixel 9 Pro) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Mobile Safari/537.36"
                    ),
                    is_mobile=True,
                    has_touch=True,
                )
                candidates: list[dict[str, Any]] = []
                seen_urls: set[str] = set()

                def on_response(response):
                    try:
                        url = response.url
                        if url in seen_urls:
                            return
                        ctype = (response.headers.get("content-type") or "").lower()
                        rtype = response.request.resource_type
                        if "json" not in ctype and rtype not in {"xhr", "fetch"}:
                            return
                        seen_urls.add(url)
                        if response.status >= 400:
                            return
                        body = response.json()
                        arrays = walk_arrays(body)
                        if not arrays:
                            return
                        arrays.sort(key=lambda x: (x["titled_items"], x["array_len"]), reverse=True)
                        candidates.append({
                            "url": url,
                            "status": response.status,
                            "content_type": ctype[:120],
                            "resource_type": rtype,
                            "arrays": arrays[:6],
                        })
                    except Exception:
                        return

                page.on("response", on_response)
                page_error = ""
                status = None
                try:
                    response = page.goto(cfg["url"], wait_until="domcontentloaded", timeout=90000)
                    status = response.status if response else None
                    page.wait_for_timeout(5000)
                    for _ in range(10):
                        page.mouse.wheel(0, 1400)
                        page.wait_for_timeout(400)
                    page.wait_for_timeout(1500)
                except Exception as exc:
                    page_error = f"{type(exc).__name__}: {exc}"
                finally:
                    page.close()

                candidates.sort(
                    key=lambda x: max((a.get("titled_items", 0) for a in x.get("arrays", [])), default=0),
                    reverse=True,
                )
                results["platforms"][platform] = {
                    "page_url": cfg.get("url"),
                    "page_status": status,
                    "page_error": page_error,
                    "candidate_count": len(candidates),
                    "candidates": candidates[:15],
                }
        finally:
            browser.close()

    (out_dir / "api_discovery.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        k: {
            "page_status": v.get("page_status"),
            "candidate_count": v.get("candidate_count"),
            "best_titled_items": max(
                (a.get("titled_items", 0) for c in v.get("candidates", []) for a in c.get("arrays", [])),
                default=0,
            ),
        }
        for k, v in results["platforms"].items()
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
