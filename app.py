from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
import sqlite3
import threading
import webbrowser
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "short_drama.sqlite3"
BASE_DATA = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
INDEX_HTML = (ROOT / "index.html").read_text(encoding="utf-8")
REVIEW_HTML = (ROOT / "review.html").read_text(encoding="utf-8")
PLATFORM_ORDER = ["NetShort", "DramaWave", "MoboReels", "ReelShort"]
EDITABLE_FIELDS = {
    "genre", "lane", "audience", "storyCore", "storySkin", "conflict",
    "payoff", "openingSummary", "openingType", "payEpisode",
    "paywallSummary", "paywallType", "localizationLevel",
    "localizationJudgment", "mismatch",
}


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS drama_overrides (
        drama_id TEXT PRIMARY KEY,
        fields_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
        )"""
    )
    connection.commit()
    return connection


def split_lane(value: str) -> list[str]:
    normalized = str(value or "")
    for separator in ["/", "、", ",", "，", ";", "；", "|"]:
        normalized = normalized.replace(separator, "\n")
    return [item.strip() for item in normalized.splitlines() if item.strip()]


def count_by(records: list[dict], field: str) -> list[dict]:
    counts = Counter(str(record.get(field) or "").strip() for record in records)
    counts.pop("", None)
    counts.pop("未采集", None)
    return [
        {"name": name, "value": value}
        for name, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def build_summary(records: list[dict]) -> dict:
    summary = dict(BASE_DATA["summary"])
    dates = sorted(
        {
            event.get("date")
            for record in records
            for event in record.get("history", [])
            if event.get("date")
        }
    )
    latest_date = dates[-1] if dates else summary.get("collectionDate", "")
    latest_records = []
    for record in records:
        event = next(
            (item for item in record.get("history", []) if item.get("date") == latest_date),
            None,
        )
        if not event:
            continue
        current = dict(record)
        current.update(
            {
                "date": event.get("date", latest_date),
                "app": event.get("app") or record.get("app", ""),
                "rank": event.get("rank", record.get("rank", 0)),
                "heat": event.get("heat") or "",
                "tags": event.get("tags", record.get("tags", "")),
                "platformMetrics": event.get("metrics") or {},
            }
        )
        latest_records.append(current)

    platforms = [
        {"name": app, "value": sum(1 for record in latest_records if record.get("app") == app)}
        for app in PLATFORM_ORDER
    ]
    lane_counts = Counter(
        lane for record in latest_records for lane in split_lane(record.get("lane", ""))
    )
    top_by_platform = []
    for platform in platforms:
        candidates = [record for record in latest_records if record.get("app") == platform["name"]]
        if candidates:
            top_by_platform.append(min(candidates, key=lambda record: record.get("rank", 999)))

    summary.update(
        {
            "collectionDate": latest_date,
            "dates": dates,
            "dateCount": len(dates),
            "totalRows": len(latest_records),
            "uniqueTitles": len({record.get("title") for record in latest_records}),
            "totalUniqueTitles": len(records),
            "platforms": platforms,
            "genres": count_by(latest_records, "genre"),
            "audiences": count_by(latest_records, "audience"),
            "lanes": [
                {"name": name, "value": value}
                for name, value in sorted(lane_counts.items(), key=lambda item: (-item[1], item[0]))[:12]
            ],
            "topByPlatform": top_by_platform,
        }
    )
    return summary


def public_data() -> dict:
    with connect() as connection:
        rows = connection.execute("SELECT drama_id, fields_json FROM drama_overrides").fetchall()
    overrides = {}
    for drama_id, fields_json in rows:
        try:
            overrides[drama_id] = json.loads(fields_json)
        except json.JSONDecodeError:
            overrides[drama_id] = {}
    records = []
    for base_record in BASE_DATA["records"]:
        record = dict(base_record)
        record.update(overrides.get(record["id"], {}))
        record["laneTerms"] = split_lane(record.get("lane", ""))
        records.append(record)
    return {"records": records, "summary": build_summary(records)}


class Handler(BaseHTTPRequestHandler):
    server_version = "ShortDramaResearch/1.0"

    def send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value: object, status: int = 200) -> None:
        self.send_bytes(
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def authorized(self) -> bool:
        expected_password = os.environ.get("ADMIN_PASSWORD", "")
        if not expected_password:
            return True
        expected_user = os.environ.get("ADMIN_USER", "admin")
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Basic "):
            try:
                decoded = base64.b64decode(authorization[6:]).decode("utf-8")
                username, password = decoded.split(":", 1)
                if hmac.compare_digest(username, expected_user) and hmac.compare_digest(
                    password, expected_password
                ):
                    return True
            except (ValueError, UnicodeDecodeError):
                pass
        self.send_bytes(
            "需要管理员登录。".encode("utf-8"),
            "text/plain; charset=utf-8",
            401,
            {"WWW-Authenticate": 'Basic realm="Short Drama Editor"'},
        )
        return False

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/review":
            if not self.authorized():
                return
            self.send_bytes(REVIEW_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/data":
            self.send_json(public_data())
        elif path == "/api/review-data":
            if not self.authorized():
                return
            self.send_json({**public_data(), "accountEmail": "本地管理员"})
        elif path == "/health":
            data = public_data()
            self.send_json({"ok": True, "records": len(data["records"])})
        else:
            self.send_json({"error": "页面不存在"}, 404)

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        prefix = "/api/reviews/"
        if not path.startswith(prefix):
            self.send_json({"error": "页面不存在"}, 404)
            return
        if not self.authorized():
            return
        drama_id = unquote(path[len(prefix):])
        allowed_ids = {record["id"] for record in BASE_DATA["records"]}
        if drama_id not in allowed_ids:
            self.send_json({"error": "剧目不存在"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 100_000:
                raise ValueError("提交内容大小不正确")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            raw_fields = payload.get("fields") or {}
            fields = {
                key: value.strip()[:6000]
                for key, value in raw_fields.items()
                if key in EDITABLE_FIELDS and isinstance(value, str)
            }
            if not fields:
                raise ValueError("没有可保存的字段")
            updated_at = datetime.now(timezone.utc).isoformat()
            with connect() as connection:
                connection.execute(
                    """INSERT INTO drama_overrides (drama_id, fields_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(drama_id) DO UPDATE SET
                    fields_json=excluded.fields_json,
                    updated_at=excluded.updated_at""",
                    (drama_id, json.dumps(fields, ensure_ascii=False), updated_at),
                )
                connection.commit()
            self.send_json(
                {"saved": True, "dramaId": drama_id, "fields": fields, "updatedAt": updated_at}
            )
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)

    def log_message(self, format: str, *args: object) -> None:
        print("[%s] %s" % (self.log_date_time_string(), format % args))


def main() -> None:
    parser = argparse.ArgumentParser(description="短剧热播榜研究工具")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "4173")))
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    connect().close()
    url = f"http://127.0.0.1:{args.port}/"
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"短剧研究工具已启动：{url}")
    print("按 Ctrl+C 停止。")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
