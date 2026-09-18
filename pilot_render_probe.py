from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

from official_web_collectors import collect_dramabox_channel

TZ = ZoneInfo("Asia/Shanghai")
TARGETS = [
    "https://www.dramaboxdb.com/channel/trending",
    "https://www.dramaboxdb.com/ja/channel/trending",
]
STATE = {"status": "starting", "updated_at": None, "results": [], "dramabox_top10": None}


def collection_date() -> str:
    return datetime.now(TZ).date().isoformat()


def fetch_html(url: str) -> tuple[int, str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read(2_500_000)
        return int(resp.status), resp.headers.get("content-type") or "", raw.decode("utf-8", errors="replace")


def probe_once() -> None:
    global STATE
    results = []
    top10 = None

    for url in TARGETS:
        row = {"url": url}
        try:
            status, content_type, body = fetch_html(url)
            row["http_status"] = status
            row["content_type"] = content_type
            row["bytes_read"] = len(body.encode("utf-8", errors="ignore"))
            row["raw_html_sha256"] = hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()

            if url.endswith("/channel/trending"):
                payload = collect_dramabox_channel(
                    channel="trending",
                    collection_date=collection_date(),
                    top_n=10,
                    document=body,
                )
                rows = payload.get("rows") or []
                row["parsed_row_count"] = len(rows)
                row["parsed_titles"] = [x.get("title") for x in rows[:10] if isinstance(x, dict)]
                if len(rows) == 10:
                    top10 = {
                        "platform": "DramaBox",
                        "collection_date": collection_date(),
                        "source_type": "OFFICIAL_WEB_PILOT",
                        "target_key": "web_pilot_trending_top10",
                        "ranking_type": "Trending",
                        "source_url": url,
                        "row_count": 10,
                        "rows": rows[:10],
                        "evidence": {
                            "egress": "render-singapore",
                            "raw_html_sha256": row["raw_html_sha256"],
                            "http_status": status,
                            "collector_version": payload.get("collector_version"),
                            "collected_at": payload.get("collected_at"),
                        },
                        "production_write": False,
                    }
        except urllib.error.HTTPError as exc:
            row["http_status"] = int(exc.code)
            row["error"] = f"HTTPError {exc.code}"
            row["content_type"] = exc.headers.get("content-type")
        except Exception as exc:
            row["http_status"] = None
            row["error"] = f"{type(exc).__name__}: {exc}"
        results.append(row)

    STATE = {
        "status": "ok",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": results,
        "dramabox_top10": top10,
        "production_write": False,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/refresh":
            probe_once()
        body = json.dumps(STATE, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    probe_once()
    port = int(os.environ.get("PORT", "10000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
