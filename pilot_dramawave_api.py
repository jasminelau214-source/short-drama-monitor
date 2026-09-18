from __future__ import annotations

import base64
import hashlib
import json
import os
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
            "suspicious_fields": suspicious_fields(it),
            "all_keys": sorted(str(k) for k in it.keys()),
        })
    keep["sample"] = sample
    return keep


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
        out["indexes"].append({
            "tab_key": tab_key,
            "position_index": pidx,
            "business_name": tab.get("business_name"),
            "page_info": idata.get("page_info"),
            "modules": [clean_module(m) for m in modules if isinstance(m, dict)],
        })

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
