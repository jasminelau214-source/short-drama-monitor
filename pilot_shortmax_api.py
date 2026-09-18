from __future__ import annotations

import base64
import json
import urllib.request
from typing import Any

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

API_BASE = "https://shortweb.shorttv.live/app-api/app"
KEY = b"shortwebapiaesen"
HEADERS = {
    "Content-Type": "application/json",
    "X-Encrypted": "true",
    "Language-Code": "en",
}


def encrypt_payload(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    cipher = AES.new(KEY, AES.MODE_CBC, iv=KEY)
    return base64.b64encode(cipher.encrypt(pad(raw, AES.block_size)))


def decrypt_response(text: str) -> dict[str, Any]:
    cipher = AES.new(KEY, AES.MODE_CBC, iv=KEY)
    raw = base64.b64decode(text.strip())
    plain = unpad(cipher.decrypt(raw), AES.block_size)
    return json.loads(plain.decode("utf-8"))


def post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        API_BASE + path,
        data=encrypt_payload(payload),
        headers=HEADERS,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    return decrypt_response(text)


def title(item: dict[str, Any]) -> str:
    return str(item.get("lanShortPlayName") or item.get("shortPlayName") or item.get("rawName") or "").strip()


def main() -> int:
    payload = {
        "pageNo": 1,
        "pageSize": 100,
        "labelId": "",
        "classId": "",
    }
    data = post("/cmsShortPlay/queryPage", payload)
    page = data.get("data") or {}
    items = [x for x in (page.get("list") or []) if isinstance(x, dict) and title(x)]
    api_order = [
        {
            "index": i,
            "id": x.get("shortPlayId"),
            "title": title(x),
            "playNum": x.get("playNum"),
            "collectNum": x.get("collectNum"),
        }
        for i, x in enumerate(items[:15], 1)
    ]
    by_play = sorted(
        items,
        key=lambda x: (
            x.get("playNum") or 0,
            x.get("collectNum") or 0,
            x.get("shortPlayId") or 0,
        ),
        reverse=True,
    )
    top_play = [
        {
            "rank": i,
            "id": x.get("shortPlayId"),
            "title": title(x),
            "playNum": x.get("playNum"),
            "collectNum": x.get("collectNum"),
        }
        for i, x in enumerate(by_play[:15], 1)
    ]
    print(json.dumps({
        "total": page.get("total"),
        "page_count": len(items),
        "api_order_first15": api_order,
        "play_desc_first15": top_play,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
