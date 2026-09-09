from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import uuid
import webbrowser
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT))
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "short_drama.sqlite3"
PLATFORM_ORDER = ["NetShort", "DramaWave", "MoboReels", "ReelShort"]
EDITABLE_FIELDS = {
    "synopsis", "genre", "lane", "audience", "storyCore", "storySkin", "conflict", "payoff",
    "openingSummary", "openingType", "payEpisode", "paywallSummary", "paywallType",
    "localizationLevel", "localizationJudgment", "mismatch",
}


def clean(value, limit=6000):
    return str(value or "").strip()[:limit]


def split_lane(value):
    s = str(value or "")
    for sep in ["/", "、", ",", "，", ";", "；", "|"]:
        s = s.replace(sep, "\n")
    return [x.strip() for x in s.splitlines() if x.strip()]


def load_base_data():
    data = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
    records = data.setdefault("records", [])
    by_id = {r.get("id"): r for r in records if r.get("id")}
    by_title = {r.get("title"): r for r in records if r.get("title")}
    update_dir = ROOT / "updates"

    if update_dir.exists():
        for path in sorted(update_dir.glob("*.json")):
            patch = json.loads(path.read_text(encoding="utf-8"))

            for drama_id, fields in (patch.get("record_patches") or {}).items():
                if drama_id in by_id and isinstance(fields, dict):
                    by_id[drama_id].update(fields)

            for new_record in patch.get("new_records") or []:
                if not isinstance(new_record, dict) or not new_record.get("id"):
                    continue
                if new_record["id"] not in by_id:
                    r = dict(new_record)
                    records.append(r)
                    by_id[r["id"]] = r
                    by_title[r.get("title")] = r

            for obs in patch.get("observations") or []:
                r = by_id.get(obs.get("id")) or by_title.get(obs.get("title"))
                if not r:
                    continue
                date = clean(obs.get("date"), 20)
                event = {
                    "date": date,
                    "app": clean(obs.get("app") or r.get("app"), 40),
                    "rank": int(obs.get("rank") or 0),
                    "heat": clean(obs.get("heat"), 100),
                    "tags": clean(obs.get("tags"), 1000),
                    "metrics": obs.get("metrics") if isinstance(obs.get("metrics"), dict) else {},
                    "source": clean(obs.get("source"), 500),
                }
                history = [h for h in (r.get("history") or []) if h.get("date") != date]
                history.append(event)
                history.sort(key=lambda h: (h.get("date", ""), h.get("app", ""), h.get("rank", 999)))
                dates = sorted({h.get("date") for h in history if h.get("date")})
                ranks = [int(h.get("rank")) for h in history if h.get("rank") not in (None, "")]
                r.update({
                    "app": event["app"],
                    "date": date,
                    "rank": event["rank"],
                    "heat": event["heat"],
                    "tags": event["tags"],
                    "platformMetrics": event["metrics"],
                    "history": history,
                    "recordedDates": dates,
                    "daysOnChart": len(dates),
                    "firstDate": dates[0] if dates else date,
                    "lastDate": dates[-1] if dates else date,
                    "highestRank": min(ranks) if ranks else event["rank"],
                })
    return data


BASE_DATA = load_base_data()
INDEX_HTML = (ROOT / "index.html").read_text(encoding="utf-8")
REVIEW_HTML = (ROOT / "review.html").read_text(encoding="utf-8")
COLLECT_HTML = (ROOT / "collect.html").read_text(encoding="utf-8")
RESEARCH_META = (
    json.loads((ROOT / "research_meta.json").read_text(encoding="utf-8"))
    if (ROOT / "research_meta.json").exists()
    else {}
)


def connect():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS drama_overrides(
          drama_id TEXT PRIMARY KEY, fields_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS collection_uploads(
          id TEXT PRIMARY KEY, collection_date TEXT NOT NULL, platform TEXT NOT NULL,
          filename TEXT NOT NULL, mime_type TEXT NOT NULL, storage_path TEXT NOT NULL,
          sha256 TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """
    )
    c.commit()
    return c


def count_by(records, field):
    counts = Counter(clean(r.get(field)) for r in records)
    counts.pop("", None)
    counts.pop("未采集", None)
    return [{"name": k, "value": v} for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]


def build_summary(records):
    dates = sorted({h.get("date") for r in records for h in (r.get("history") or []) if h.get("date")})
    latest = dates[-1] if dates else ""
    current = []

    for r in records:
        h = next((x for x in (r.get("history") or []) if x.get("date") == latest), None)
        if not h:
            continue
        x = dict(r)
        x.update({
            "date": latest,
            "app": h.get("app") or r.get("app", ""),
            "rank": h.get("rank", r.get("rank", 0)),
            "heat": h.get("heat") or "",
            "tags": h.get("tags", r.get("tags", "")),
            "platformMetrics": h.get("metrics") or {},
        })
        current.append(x)

    platforms = [{"name": p, "value": sum(1 for r in current if r.get("app") == p)} for p in PLATFORM_ORDER]
    lane_counts = Counter(t for r in current for t in split_lane(r.get("lane")))
    leaders = []
    for p in PLATFORM_ORDER:
        xs = [r for r in current if r.get("app") == p]
        if xs:
            leaders.append(min(xs, key=lambda r: r.get("rank", 999)))

    return {
        "collectionDate": latest,
        "dates": dates,
        "dateCount": len(dates),
        "totalRows": len(current),
        "uniqueTitles": len({r.get("title") for r in current}),
        "totalUniqueTitles": len(records),
        "newTitles": sum(1 for r in current if r.get("firstDate") == latest),
        "continuingTitles": sum(1 for r in current if r.get("firstDate") != latest),
        "platforms": platforms,
        "genres": count_by(current, "genre"),
        "audiences": count_by(current, "audience"),
        "lanes": [{"name": k, "value": v} for k, v in sorted(lane_counts.items(), key=lambda x: (-x[1], x[0]))[:12]],
        "topByPlatform": leaders,
        "dateStats": [
            {
                "date": d,
                "rows": sum(1 for r in records if any(h.get("date") == d for h in (r.get("history") or []))),
                "uniqueTitles": len({r.get("title") for r in records if any(h.get("date") == d for h in (r.get("history") or []))}),
            }
            for d in dates
        ],
    }


def public_data():
    with connect() as c:
        rows = c.execute("SELECT drama_id,fields_json FROM drama_overrides").fetchall()

    overrides = {}
    for row in rows:
        try:
            overrides[row["drama_id"]] = json.loads(row["fields_json"])
        except json.JSONDecodeError:
            pass

    records = []
    for base in BASE_DATA["records"]:
        r = dict(base)
        r.update(overrides.get(r.get("id"), {}))
        r["laneTerms"] = split_lane(r.get("lane"))
        records.append(r)
    return {"records": records, "summary": build_summary(records)}


def render_index_html():
    data = public_data()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</script>", "<\\/script>")
    html = re.sub(
        r"const DATA\s*=\s*\{.*?\};\s*\n\s*const records",
        f"const DATA = {payload};\n    const records",
        INDEX_HTML,
        count=1,
        flags=re.S,
    )
    if "榜单采集中心" not in html:
        html = html.replace(
            '<button data-view-button="lifecycle">历史生命周期</button>',
            '<button data-view-button="lifecycle">历史生命周期</button>\n        <button type="button" onclick="location.href=\'/collect\'">榜单采集中心</button>',
            1,
        )
    dates = data["summary"].get("dates") or []
    if dates:
        html = re.sub(
            r"已纳入真实采集日：[^。]+。趋势和生命周期只使用截图采集记录。",
            f"已纳入真实采集日：{'、'.join(dates)}。趋势和生命周期只使用截图采集记录。",
            html,
            count=1,
        )
        html = re.sub(
            r'<div class="date-pill">\d+个真实采集日</div>',
            f'<div class="date-pill">{len(dates)}个真实采集日</div>',
            html,
            count=1,
        )
    return html


class Handler(BaseHTTPRequestHandler):
    server_version = "ShortDramaMonitor/1.2"

    def send_bytes(self, body, content_type, status=200, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value, status=200):
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    @staticmethod
    def admin_configured():
        return bool(os.environ.get("ADMIN_PASSWORD", "").strip())

    def send_admin_setup_page(self):
        html = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>管理员入口未初始化</title>
<style>body{margin:0;background:#f5f6f2;color:#173c37;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}.wrap{max-width:720px;margin:10vh auto;padding:24px}.card{background:#fff;border:1px solid #dfe5dd;border-radius:14px;padding:24px}h1{margin-top:0}code{background:#f0f2ea;padding:2px 6px;border-radius:5px}.muted{color:#667068;line-height:1.7}.actions{margin-top:20px}.actions a{color:#0f7b68;text-decoration:none}</style></head>
<body><main class="wrap"><section class="card"><h1>管理员入口尚未初始化</h1><p class="muted">当前 Render 服务没有检测到 <code>ADMIN_PASSWORD</code>。这不是页面损坏，而是后台安全配置尚未完成。</p><p class="muted">请在 Render 的 <strong>short-drama-monitor</strong> 服务中进入 <strong>Environment → Environment Variables</strong>，新增 <code>ADMIN_PASSWORD</code>，保存并重新部署。用户名保持 <code>admin</code>。</p><p class="muted">不要把管理员密码写入 GitHub，也不要在聊天中发送密码。</p><div class="actions"><a href="/">← 返回市场仪表盘</a></div></section></main></body></html>"""
        self.send_bytes(html.encode("utf-8"), "text/html; charset=utf-8", 503)

    def authorized(self, html_page=False):
        pw = os.environ.get("ADMIN_PASSWORD", "").strip()
        if not pw:
            if html_page:
                self.send_admin_setup_page()
            else:
                self.send_json({
                    "error": "ADMIN_PASSWORD_NOT_CONFIGURED",
                    "message": "管理员入口尚未配置密码",
                }, 503)
            return False

        user = os.environ.get("ADMIN_USER", "admin")
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                u, p = base64.b64decode(auth[6:]).decode().split(":", 1)
                if hmac.compare_digest(u, user) and hmac.compare_digest(p, pw):
                    return True
            except (ValueError, UnicodeDecodeError, binascii.Error):
                pass

        self.send_bytes(
            "需要管理员登录。".encode("utf-8"),
            "text/plain; charset=utf-8",
            401,
            {"WWW-Authenticate": 'Basic realm="Short Drama Admin"'},
        )
        return False

    def read_json(self, limit=32_000_000):
        n = int(self.headers.get("Content-Length", "0"))
        if n <= 0 or n > limit:
            raise ValueError("提交内容大小不正确")
        return json.loads(self.rfile.read(n).decode())

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self.send_bytes(render_index_html().encode(), "text/html; charset=utf-8")
        elif path in ("/collect", "/collect.html"):
            if self.authorized(html_page=True):
                self.send_bytes(COLLECT_HTML.encode(), "text/html; charset=utf-8")
        elif path == "/review":
            if self.authorized(html_page=True):
                self.send_bytes(REVIEW_HTML.encode(), "text/html; charset=utf-8")
        elif path == "/api/data":
            self.send_json(public_data())
        elif path == "/api/review-data":
            if self.authorized():
                self.send_json({**public_data(), "researchMeta": RESEARCH_META, "accountEmail": "管理员"})
        elif path == "/api/admin/uploads":
            if self.authorized():
                with connect() as c:
                    rows = c.execute(
                        "SELECT id,collection_date,platform,filename,status,created_at "
                        "FROM collection_uploads ORDER BY created_at DESC LIMIT 100"
                    ).fetchall()
                self.send_json({"uploads": [dict(r) for r in rows]})
        elif path == "/health":
            d = public_data()
            self.send_json({
                "ok": True,
                "records": len(d["records"]),
                "collectionDate": d["summary"].get("collectionDate"),
                "latestRows": d["summary"].get("totalRows"),
                "newTitles": d["summary"].get("newTitles"),
                "adminConfigured": self.admin_configured(),
                "version": "1.2-beta",
                "service": "short-drama-monitor",
            })
        else:
            self.send_json({"error": "页面不存在"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/admin/uploads":
            self.send_json({"error": "页面不存在"}, 404)
            return
        if not self.authorized():
            return

        try:
            p = self.read_json()
            platform = clean(p.get("platform"), 40)
            date = clean(p.get("date"), 20)
            filename = Path(clean(p.get("filename"), 300)).name
            mime = clean(p.get("mimeType"), 100)
            if platform not in PLATFORM_ORDER:
                raise ValueError("平台不正确")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                raise ValueError("采集日期不正确")
            if mime not in {"image/jpeg", "image/png", "image/webp"}:
                raise ValueError("仅支持JPG、PNG或WebP截图")

            raw = base64.b64decode(clean(p.get("dataBase64"), 31_000_000), validate=True)
            if not raw or len(raw) > 15 * 1024 * 1024:
                raise ValueError("截图为空或超过15MB")

            uid = uuid.uuid4().hex
            ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[mime]
            target = UPLOAD_DIR / f"{date}_{platform.lower()}_{uid}{ext}"
            target.write_bytes(raw)
            now = datetime.now(timezone.utc).isoformat()
            with connect() as c:
                c.execute(
                    "INSERT INTO collection_uploads VALUES(?,?,?,?,?,?,?,?,?)",
                    (uid, date, platform, filename, mime, str(target), hashlib.sha256(raw).hexdigest(), "待分析", now),
                )
                c.commit()
            self.send_json({
                "uploaded": True,
                "id": uid,
                "status": "待分析",
                "note": "截图已保存；当前自动OCR/新剧研究仍由ChatGPT人工链路执行。",
            }, 201)
        except (ValueError, json.JSONDecodeError, binascii.Error) as e:
            self.send_json({"error": str(e)}, 400)

    def do_PUT(self):
        path = urlparse(self.path).path
        prefix = "/api/reviews/"
        if not path.startswith(prefix):
            self.send_json({"error": "页面不存在"}, 404)
            return
        if not self.authorized():
            return

        try:
            drama_id = unquote(path[len(prefix):])
            payload = self.read_json(200_000)
            raw = payload.get("fields") or {}
            fields = {
                k: clean(v)
                for k, v in raw.items()
                if k in EDITABLE_FIELDS and isinstance(v, str)
            }
            if not fields:
                raise ValueError("没有可保存的字段")

            with connect() as c:
                old = c.execute("SELECT fields_json FROM drama_overrides WHERE drama_id=?", (drama_id,)).fetchone()
                merged = json.loads(old["fields_json"]) if old else {}
                merged.update(fields)
                now = datetime.now(timezone.utc).isoformat()
                c.execute(
                    "INSERT INTO drama_overrides VALUES(?,?,?) "
                    "ON CONFLICT(drama_id) DO UPDATE SET fields_json=excluded.fields_json,updated_at=excluded.updated_at",
                    (drama_id, json.dumps(merged, ensure_ascii=False), now),
                )
                c.commit()
            self.send_json({"saved": True, "dramaId": drama_id, "fields": fields, "updatedAt": now})
        except (ValueError, json.JSONDecodeError) as e:
            self.send_json({"error": str(e)}, 400)

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "4173")))
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    connect().close()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print("短剧研究工具 V1.2 Beta：" + url)
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
