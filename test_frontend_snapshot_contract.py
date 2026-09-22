from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from drama_identity import normalize_title

ROOT = Path(__file__).resolve().parent


def load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def norm(value):
    return normalize_title(value)


def collector(run):
    result = run.get("result_json") or {}
    return result.get("collector") if isinstance(result.get("collector"), dict) else {}


def source_type(run):
    return str(collector(run).get("sourceType") or "SHORT_DRAMA_APP")


def target_key(run):
    return str(collector(run).get("targetKey") or "daily_top_all")


def top_n(run):
    rows = (run.get("result_json") or {}).get("rows") or []
    return int(collector(run).get("topN") or len(rows) or 10)


def valid_batch(run):
    result = run.get("result_json") or {}
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    n = top_n(run)
    if result.get("batchComplete") is not True or len(rows) != n or n < 1:
        return False
    ranks, titles = set(), set()
    for row in rows:
        try:
            rank = int(row.get("rank"))
        except (TypeError, ValueError):
            return False
        title = norm(row.get("title"))
        if not (1 <= rank <= n) or rank in ranks or not title or title in titles:
            return False
        ranks.add(rank)
        titles.add(title)
    return ranks == set(range(1, n + 1))


class FrontendSnapshotContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = load("testdata/analysis_runs.json")
        cls.tasks = load("testdata/research_tasks.json")
        cls.overrides = load("testdata/drama_overrides.json")
        cls.jobs = load("testdata/collection_jobs.json")
        cls.targets = load("testdata/collection_targets.json")
        cls.sources = load("testdata/source_registry.json")
        cls.web_backfill = [
            load("testdata/web_backfill_2026-09-18.json"),
            load("testdata/web_backfill_2026-09-19.json"),
            load("testdata/web_backfill_2026-09-20.json"),
            load("testdata/web_backfill_2026-09-21.json"),
        ]
        cls.html = (ROOT / "test_frontend.html").read_text(encoding="utf-8")

    def test_snapshot_counts(self):
        self.assertEqual(len(self.runs), 36)
        self.assertEqual(len(self.tasks), 162)
        self.assertEqual(len(self.overrides), 104)
        self.assertEqual(len(self.jobs), 15)
        self.assertEqual(len(self.targets), 20)
        self.assertEqual(len(self.sources), 16)

    def test_sep18_21_web_backfill_is_staging_only(self):
        self.assertEqual(
            [x["collectionDate"] for x in self.web_backfill],
            ["2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21"],
        )
        self.assertEqual(sum(x["acceptedBatchCount"] for x in self.web_backfill), 27)
        self.assertEqual(sum(x["acceptedRowCount"] for x in self.web_backfill), 270)
        self.assertEqual(sum(x["rejectedBatchCount"] for x in self.web_backfill), 5)

        rejected = {
            (x["collectionDate"], r["platform"])
            for x in self.web_backfill
            for r in x["rejectedBatches"]
        }
        self.assertEqual(
            rejected,
            {
                ("2026-09-18", "DramaWave"),
                ("2026-09-19", "DramaWave"),
                ("2026-09-20", "DramaWave"),
                ("2026-09-21", "DramaWave"),
                ("2026-09-21", "ShortMax"),
            },
        )

        for dataset in self.web_backfill:
            self.assertEqual(dataset["purpose"], "UI_STAGING_ONLY")
            self.assertFalse(dataset["productionWrite"])
            self.assertFalse(dataset["researchEligible"])
            self.assertEqual(dataset["sourceSemantics"], "OFFICIAL_WEB_OBSERVATION")
            for batch in dataset["acceptedBatches"]:
                self.assertEqual(batch["sourceType"], "OFFICIAL_WEB")
                self.assertEqual(batch["originalSourceType"], "OFFICIAL_WEB_PILOT")
                self.assertTrue(batch["batchComplete"])
                self.assertFalse(batch["productionWrite"])
                self.assertFalse(batch["researchEligible"])
                self.assertEqual(len(batch["rows"]), batch["topN"])
                self.assertEqual(
                    sorted(int(row["rank"]) for row in batch["rows"]),
                    list(range(1, batch["topN"] + 1)),
                )

    def test_historical_web_pending_remains_non_app(self):
        run_by_id = {str(r.get("id") or ""): r for r in self.runs}
        pending = [t for t in self.tasks if t.get("status") == "PENDING"]
        self.assertEqual(len(pending), 115)
        self.assertTrue(
            all(source_type(run_by_id[str(t["analysis_run_id"])]) == "OFFICIAL_WEB" for t in pending)
        )

    def test_authoritative_app_runs_fail_closed(self):
        app_runs = [r for r in self.runs if source_type(r) == "SHORT_DRAMA_APP"]
        self.assertTrue(any(not valid_batch(r) for r in app_runs))
        latest = {}
        for run in app_runs:
            if not valid_batch(run):
                continue
            key = (
                str(run.get("collection_date")),
                str(run.get("platform")),
                source_type(run),
                target_key(run),
            )
            old = latest.get(key)
            if old is None or str(run.get("updated_at") or "") > str(old.get("updated_at") or ""):
                latest[key] = run
        self.assertTrue(latest)
        self.assertTrue(all(valid_batch(r) for r in latest.values()))

    def test_v3_primary_information_architecture(self):
        nav_match = re.search(r'<nav class="nav" id="nav">(.*?)</nav>', self.html, re.S)
        self.assertIsNotNone(nav_match)
        nav = nav_match.group(1)
        buttons = re.findall(r'data-view="([^"]+)"[^>]*>([^<]+)</button>', nav)
        self.assertEqual(
            buttons,
            [
                ("dashboard", "市场总览"),
                ("ranking", "今日榜单分析"),
                ("history", "历史趋势分析"),
                ("research", "题材 / 剧目研究"),
                ("settings", "系统设置"),
            ],
        )
        self.assertIn("历史剧目库", self.html)
        self.assertIn('data-view="library"', self.html)

    def test_business_frontend_uses_chinese_labels(self):
        for marker in [
            "今日核心情报",
            "各平台重点剧目",
            "当日题材信号",
            "建议关注剧目",
            "平台表现",
            "近 7 个真实采集日",
            "题材占比趋势",
            "单剧生命周期",
            "官方网页",
            "最新市场日期",
            "深度研究状态",
            "研究置信度",
            "异常记录",
            "9/16 异常深研任务",
        ]:
            self.assertIn(marker, self.html)

        for forbidden in [
            "Official Web",
            "Research Queue",
            "READ ONLY",
            "WEB OBSERVATION ONLY",
            "Web rows",
            "Backfill rows",
            "PENDING tasks",
            "Research State",
            "Confidence",
            "Evidence",
            "Missing Fields",
            "NO POSTER",
            "污染",
        ]:
            self.assertNotIn(forbidden, self.html)

    def test_business_frontend_hides_raw_audit_dump(self):
        for forbidden in [
            "Frontend Field Coverage Matrix",
            "analysis_runs · 全量 36",
            "collection_jobs · 全量 15",
            "collection_targets · 全量 20",
            "source_registry · 全量 16",
            "drama_overrides · 全量 104",
            "完整 JSON",
        ]:
            self.assertNotIn(forbidden, self.html)
        self.assertIn("详细底层原因、契约检查和修复建议在后台审计或项目对话中说明", self.html)

    def test_sep18_21_web_data_participates_in_unified_market_analysis(self):
        for path in [
            "testdata/web_backfill_2026-09-18.json",
            "testdata/web_backfill_2026-09-19.json",
            "testdata/web_backfill_2026-09-20.json",
            "testdata/web_backfill_2026-09-21.json",
        ]:
            self.assertIn(path, self.html)
        for marker in [
            "function buildBackfillWeb",
            "function buildMarketObservations",
            "function buildMarketPresence",
            "function buildMarketTitles",
            "STATE.marketObservations=buildMarketObservations()",
            "所有通过校验的 App 与网页数据共同进入趋势",
            "9/18–9/21 的网页采集已直接进入这条趋势时间轴",
            "最新市场日期",
        ]:
            self.assertIn(marker, self.html)
        self.assertIn("researchEligible:false", self.html)
        self.assertNotIn("应用榜单与官方网页观察分别计算", self.html)

    def test_october_source_display_rule_is_locked(self):
        self.assertIn("String(date||'')<'2026-10-01'", self.html)
        self.assertIn("10 月起", self.html)
        self.assertIn("网页统一主采集，不逐条标来源", self.html)
        self.assertIn("App 验证/补充数据", self.html)
        self.assertIn("底层来源字段", self.html)

    def test_chinese_presentation_layer_covers_english_research_fields(self):
        self.assertIn("CHINESE_PRESENTATION", self.html)
        for drama_id in [
            "auto-moboreels-07fe5f7dc204",
            "auto-moboreels-2b0091c98556",
            "auto-netshort-0eb61961887f",
            "auto-netshort-f93c6f4f4bd5",
        ]:
            self.assertIn(drama_id, self.html)
        self.assertIn("applyChinesePresentation(r)", self.html)

    def test_newness_wording_is_non_overclaiming(self):
        self.assertIn("首次记录上榜", self.html)
        self.assertIn("不替代最终新剧业务定义", self.html)
        self.assertNotIn(">NEW<", self.html)

    def test_poster_and_identity_safety(self):
        self.assertIn("r.posterUrl", self.html)
        self.assertNotIn("r.poster || r.cover", self.html)
        self.assertIn("不同来源或不同榜单口径的排名只并列展示", self.html)

    def test_identity_matches_integration_contract(self):
        self.assertEqual(norm("(DUBBED) Justice in Blood"), norm("Justice in Blood"))
        self.assertEqual(norm("Ruling Over All I See (DUBBED)"), norm("Ruling Over All I See"))
        self.assertEqual(
            norm("English Dub Flash Marriage CEO Spoils Me a Lot"),
            norm("Flash Marriage CEO Spoils Me a Lot"),
        )
        self.assertNotEqual(norm("The Boy Dubbed King"), norm("The Boy King"))
        self.assertIn("function stripReleaseMarkers(v)", self.html)

    def test_research_projection_is_scope_safe(self):
        self.assertIn("String(t.platform||'')+'::'+tk+'::'+norm", self.html)
        self.assertIn("String(t.analysis_run_id)+'::'+norm", self.html)

    def test_visual_direction_is_red_and_white_table_first(self):
        self.assertIn("--red:#c92d3c", self.html)
        self.assertIn(".table-wrap{overflow:auto;border:1px solid var(--line);border-radius:10px;background:#fff}", self.html)
        self.assertIn("tr:hover td{background:#fff9fa}", self.html)


if __name__ == "__main__":
    unittest.main()
