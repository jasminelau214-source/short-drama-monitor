from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")

REQUIRED = [
    "function rankingScopeKey(item)",
    "function rankingScopeCompare(a, b)",
    "function scopedHistory(item)",
    "function scopedPeakRank(item)",
    "function rankingScopes(items)",
    "function representativeSampleCompare(a,b)",
    "各平台榜单",
    "data-ranking-platform",
    "测试环境本地回退",
    "生产只读数据",
    "function sourceTypeOf(item)",
    "2026-10-01",
    "<th>榜单</th>",
    "本榜最高",
]

FORBIDDEN = [
    "今日榜单 TOP10",
    "const top10=dayItems.slice().sort((a,b)=>a.rank-b.rank",
    "是今天最明显的排名信号",
    "let score=(r.firstDate===activeDate?40:0)",
    "最佳历史排名",
    "a.highestRank - b.highestRank",
    "a.highestRank-b.highestRank",
    "item.history.map(h =>",
]

for token in REQUIRED:
    assert token in HTML, f"missing required P0-1 marker: {token}"

for token in FORBIDDEN:
    assert token not in HTML, f"forbidden cross-scope ranking pattern remains: {token}"

assert re.search(r"<th>平台</th><th>榜单</th>", HTML), "ranking scope column missing next to platform"
assert "排名只在同一平台、同一来源、同一榜单口径内比较" in HTML
assert "不能直接替代正式新剧判定" in HTML
assert "REMOTE_READ_ONLY_CACHE" in HTML
assert "LOCAL_FALLBACK" in HTML


def scope_key(row: dict) -> tuple[str, str, str]:
    return (
        row.get("app", ""),
        row.get("sourceType") or "SHORT_DRAMA_APP",
        row.get("targetKey") or "daily_top_all",
    )


def group_scopes(rows: list[dict]) -> dict[tuple[str, str, str], list[dict]]:
    out: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        out.setdefault(scope_key(row), []).append(row)
    for values in out.values():
        values.sort(key=lambda r: (r["rank"], r["title"]))
    return out


def scoped_history(item: dict) -> list[dict]:
    app, source_type, target = scope_key(item)
    return [
        event
        for event in item.get("history", [])
        if (event.get("app") or app) == app
        and (event.get("sourceType") or source_type) == source_type
        and (event.get("targetKey") or target) == target
    ]


def movement(item: dict, date: str):
    history = sorted(scoped_history(item), key=lambda x: x["date"])
    current = next((x for x in history if x["date"] == date), None)
    previous = [x for x in history if x["date"] < date]
    if not current or not previous:
        return None
    return int(previous[-1]["rank"]) - int(current["rank"])


# Incident fixture 1: two platforms may both have #1; there must be no synthetic #1/#2 market order.
rows = [
    {"app": "NetShort", "sourceType": "SHORT_DRAMA_APP", "targetKey": "daily_top_all", "title": "A", "rank": 1},
    {"app": "ReelShort", "sourceType": "SHORT_DRAMA_APP", "targetKey": "daily_top_all", "title": "B", "rank": 1},
]
scopes = group_scopes(rows)
assert len(scopes) == 2
assert [x["rank"] for x in scopes[("NetShort", "SHORT_DRAMA_APP", "daily_top_all")]] == [1]
assert [x["rank"] for x in scopes[("ReelShort", "SHORT_DRAMA_APP", "daily_top_all")]] == [1]

# Incident fixture 2: same platform, different ranking targets may both have #1.
rows = [
    {"app": "DramaBox", "sourceType": "SHORT_DRAMA_APP", "targetKey": "daily_trending_all", "title": "T", "rank": 1},
    {"app": "DramaBox", "sourceType": "SHORT_DRAMA_APP", "targetKey": "daily_must_sees_all", "title": "M", "rank": 1},
]
scopes = group_scopes(rows)
assert len(scopes) == 2
assert all(values[0]["rank"] == 1 for values in scopes.values())

# Incident fixture 3: movement must not cross targetKey.
item = {
    "app": "DramaBox",
    "sourceType": "OFFICIAL_WEB",
    "targetKey": "web_pilot_trending_top10",
    "history": [
        {"date": "2026-09-18", "app": "DramaBox", "sourceType": "OFFICIAL_WEB", "targetKey": "web_pilot_trending_top10", "rank": 8},
        {"date": "2026-09-19", "app": "DramaBox", "sourceType": "SHORT_DRAMA_APP", "targetKey": "web_pilot_trending_top10", "rank": 1},
        {"date": "2026-09-20", "app": "DramaBox", "sourceType": "OFFICIAL_WEB", "targetKey": "web_pilot_trending_top10", "rank": 3},
    ],
}
assert movement(item, "2026-09-20") == 5

# Incident fixture 4: same platform/target but different source must remain separate scopes.
rows = [
    {"app": "NetShort", "sourceType": "SHORT_DRAMA_APP", "targetKey": "daily_top_all", "title": "A", "rank": 2},
    {"app": "NetShort", "sourceType": "OFFICIAL_WEB", "targetKey": "daily_top_all", "title": "A", "rank": 5},
]
scopes = group_scopes(rows)
assert len(scopes) == 2

print("P0-1 frontend ranking-scope contract: PASS")
