from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TARGETS = [
    "https://www.dramaboxdb.com/channel/trending",
    "https://www.dramaboxdb.com/ja/channel/trending",
]
STATE = {"status": "starting", "updated_at": None, "results": []}


def probe_once() -> None:
    global STATE
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    }
    for url in TARGETS:
        row = {"url": url}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                body = resp.read(1_500_000).decode("utf-8", errors="replace")
                row["http_status"] = int(resp.status)
                row["content_type"] = resp.headers.get("content-type")
                row["bytes_read"] = len(body.encode("utf-8", errors="ignore"))
                row["has_trending"] = "Trending" in body
                title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
                row["title"] = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
                names = re.findall(r"/movie/\d+/[^\"']+[\"'][^>]*>([^<]{3,120})</a>", body, re.I)
                dedup = []
                seen = set()
                for name in names:
                    name = re.sub(r"\s+", " ", name).strip()
                    key = name.casefold()
                    if name and key not in seen:
                        seen.add(key)
                        dedup.append(name)
                    if len(dedup) >= 10:
                        break
                row["sample_titles"] = dedup
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
