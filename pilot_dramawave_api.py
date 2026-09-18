from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import uuid
import urllib.request
from typing import Any

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

API_BASE = "https://api.mydramawave.com"
AES_KEY = b"2r36789f45q01ae5"
SIGN_SECRET = "8IAcbWyCsVhYv82S2eofRqK1DF3nNDAv"


def encrypt_json(obj: Any) -> bytes:
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    iv = os.urandom(16)
    cipher = AES.new(AES_KEY, AES.MODE_CBC, iv)
    return base64.b64encode(iv + cipher.encrypt(pad(raw, AES.block_size)))


def decrypt_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").strip()
    if text.startswith("{") or text.startswith("["):
        return text
    blob = base64.b64decode(text)
    iv, ct = blob[:16], blob[16:]
    cipher = AES.new(AES_KEY, AES.MODE_CBC, iv)
    return unpad(cipher.decrypt(ct), AES.block_size).decode("utf-8")


def request(path: str, *, method: str = "GET", body: Any | None = None, auth: str = "", device_id: str) -> dict[str, Any]:
    url = API_BASE + path
    headers = {
        "User-Agent": "Mozilla/5.0 Chrome/120",
        "app-name": "com.dramawave.h5",
        "app-version": "1.2.20",
        "device-hash": device_id,
        "device-id": device_id,
        "device": "h5",
        "Origin": "https://mydramawave.com",
        "Referer": "https://mydramawave.com/",
        "Accept": "application/json,text/plain,*/*",
        "language": "en",
        "language_code": "en-US",
        "country_code": "US",
    }
    data = None
    if auth:
        headers["authorization"] = auth
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = encrypt_json(body)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read()
    return json.loads(decrypt_text(raw))


def auth_header(auth_key: str, auth_secret: str) -> str:
    sig = hashlib.md5(f"{SIGN_SECRET}&{auth_secret}".encode()).hexdigest()
    return f"oauth_signature={sig},oauth_token={auth_key},ts={int(time.time()*1000)}"


def suspicious_fields(item: dict[str, Any]) -> dict[str, Any]:
    words = ("rank", "trend", "badge", "label", "tag", "corner", "mark", "hot", "popular", "sort", "position")
    out: dict[str, Any] = {}
    for key, value in item.items():
        if not any(word in str(key).casefold() for word in words):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, list):
            out[key] = value[:8]
        elif isinstance(value, dict):
            out[key] = {
                k: v for k, v in list(value.items())[:12]
                if isinstance(v, (str, int, float, bool)) or v is None
            }
    return out


def clean_module(m: dict[str, Any]) -> dict[str, Any]:
    keep = {}
    for k in ("type", "module_key", "title", "name", "display_name", "module_name", "style", "sub_title"):
        if k in m and isinstance(m.get(k), (str, int, float, bool)):
            keep[k] = m.get(k)
    keep["module_suspicious_fields"] = suspicious_fields(m)
    items = m.get("items") if isinstance(m.get("items"), list) else []
    keep["item_count"] = len(items)
    sample = []
    for idx, it in enumerate(items[:15], 1):
        if not isinstance(it, dict):
            continue
        sample.append({
            "position": idx,
            "title": it.get("title") or it.get("series_name") or it.get("name"),
            "key": it.get("key") or it.get("series_id") or it.get("id"),
            "rank": it.get("rank") or it.get("position") or it.get("ranking"),
            "episode_info": bool(it.get("episode_info")),
            "r_info": it.get("r_info"),
            "r_info1": it.get("r_info1"),
            "view_count": it.get("view_count"),
            "follow_count": it.get("follow_count"),
            "link": it.get("link"),
            "tag": it.get("tag"),
            "series_tag": it.get("series_tag"),
            "suspicious_fields": suspicious_fields(it),
            "all_keys": sorted(str(k) for k in it.keys()),
        })
    keep["sample"] = sample
    return keep



TRENDING_RE = re.compile(r"\\b(\\d{1,2})(?:st|nd|rd|th)\\s+Most\\s+Trending\\b", re.I)


def explicit_trending_rank(item: dict[str, Any]) -> int | None:
    candidates: list[Any] = []
    for key in ("content_tags", "series_tag", "tag", "content_detail_tags"):
        value = item.get(key)
        if isinstance(value, list):
            candidates.extend(value)
        elif value is not None:
            candidates.append(value)
    for value in candidates:
        if not isinstance(value, str):
            continue
        match = TRENDING_RE.search(value)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                return None
    return None


def scan_recommend_feed(
    *,
    module: dict[str, Any],
    page_info: dict[str, Any],
    auth: str,
    device_id: str,
    max_pages: int = 40,
) -> dict[str, Any]:
    by_rank: dict[int, dict[str, Any]] = {}
    seen_keys: set[str] = set()
    scanned_items = 0
    pages = 0

    def consume(items: Any) -> None:
        nonlocal scanned_items
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            scanned_items += 1
            rank = explicit_trending_rank(item)
            if not rank or not 1 <= rank <= 30:
                continue
            title = str(item.get("title") or item.get("series_name") or item.get("name") or "").strip()
            if not title:
                continue
            key = str(item.get("key") or item.get("series_id") or item.get("id") or "")
            if key and key in seen_keys:
                continue
            if key:
                seen_keys.add(key)
            by_rank.setdefault(rank, {
                "rank": rank,
                "title": title,
                "series_key": key or None,
                "content_tags": item.get("content_tags"),
                "series_tag": item.get("series_tag"),
                "tag": item.get("tag"),
            })

    consume(module.get("items") or [])
    module_key = str(module.get("module_key") or "")
    next_token = str(page_info.get("next") or "")
    has_more = bool(page_info.get("has_more"))
    visited: set[str] = set()

    while module_key and has_more and next_token and pages < max_pages:
        if next_token in visited:
            break
        visited.add(next_token)
        response = request(
            "/h5-api/homepage/v2/tab/feed",
            method="POST",
            body={"module_key": module_key, "next": next_token},
            auth=auth,
            device_id=device_id,
        )
        data = response.get("data") or {}
        consume(data.get("items") or [])
        info = data.get("page_info") or {}
        next_token = str(info.get("next") or "")
        has_more = bool(info.get("has_more"))
        pages += 1
        if all(rank in by_rank for rank in range(1, 11)):
            break

    return {
        "module_key": module_key,
        "pages_scanned": pages,
        "items_scanned": scanned_items,
        "explicit_ranks": [by_rank[r] for r in sorted(by_rank)],
        "top10_complete": all(rank in by_rank for rank in range(1, 11)),
        "top10": [by_rank[r] for r in range(1, 11) if r in by_rank],
        "missing_top10_ranks": [r for r in range(1, 11) if r not in by_rank],
        "next_token": next_token,
        "has_more": has_more,
    }


def main() -> int:
    device_id = uuid.uuid4().hex
    login = request("/h5-api/anonymous/login", method="POST", body={"device_id": device_id}, device_id=device_id)
    data = login.get("data") or {}
    auth_key = data.get("auth_key")
    auth_secret = data.get("auth_secret")
    if not auth_key or not auth_secret:
        raise RuntimeError("anonymous login missing auth fields")
    auth = auth_header(str(auth_key), str(auth_secret))

    tabs = request("/h5-api/homepage/v2/tab/list", auth=auth, device_id=device_id)
    tab_list = (tabs.get("data") or {}).get("list") or []
    out = {"tabs": [], "indexes": []}
    for tab in tab_list[:10]:
        if not isinstance(tab, dict):
            continue
        out["tabs"].append({
            k: tab.get(k) for k in ("tab_key", "position_index", "business_name", "title", "name") if k in tab
        })
        tab_key = tab.get("tab_key")
        pidx = tab.get("position_index", 0)
        if not tab_key:
            continue
        idx = request(
            f"/h5-api/homepage/v2/tab/index?tab_key={tab_key}&position_index={pidx}&first=",
            auth=auth,
            device_id=device_id,
        )
        idata = idx.get("data") or {}
        modules = idata.get("items") if isinstance(idata.get("items"), list) else []
        index_record = {
            "tab_key": tab_key,
            "position_index": pidx,
            "business_name": tab.get("business_name"),
            "page_info": idata.get("page_info"),
            "modules": [clean_module(m) for m in modules if isinstance(m, dict)],
        }
        recommend = next(
            (
                m for m in modules
                if isinstance(m, dict)
                and m.get("type") == "recommend"
                and isinstance(m.get("items"), list)
            ),
            None,
        )
        if recommend:
            index_record["recommend_feed_rank_scan"] = scan_recommend_feed(
                module=recommend,
                page_info=idata.get("page_info") or {},
                auth=auth,
                device_id=device_id,
            )
        out["indexes"].append(index_record)

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
