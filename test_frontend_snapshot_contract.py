from __future__ import annotations

import json
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
        cls.html = (ROOT / "test_frontend.html").read_text(encoding="utf-8")

    def test_snapshot_counts(self):
        self.assertEqual(len(self.runs), 36)
        self.assertEqual(len(self.tasks), 162)
        self.assertEqual(len(self.overrides), 104)
        self.assertEqual(len(self.jobs), 15)
        self.assertEqual(len(self.targets), 20)
        self.assertEqual(len(self.sources), 16)

    def test_historical_web_pending_visible_but_not_app(self):
        run_by_id = {str(r.get("id") or ""): r for r in self.runs}
        pending = [t for t in self.tasks if t.get("status") == "PENDING"]
        self.assertEqual(len(pending), 115)
        self.assertTrue(all(source_type(run_by_id[str(t["analysis_run_id"])]) == "OFFICIAL_WEB" for t in pending))

    def test_authoritative_app_runs_fail_closed(self):
        app_runs = [r for r in self.runs if source_type(r) == "SHORT_DRAMA_APP"]
        self.assertTrue(any(not valid_batch(r) for r in app_runs))
        latest = {}
        for run in app_runs:
            if not valid_batch(run):
                continue
            key = (str(run.get("collection_date")), str(run.get("platform")), source_type(run), target_key(run))
            old = latest.get(key)
            if old is None or str(run.get("updated_at") or "") > str(old.get("updated_at") or ""):
                latest[key] = run
        self.assertTrue(latest)
        self.assertTrue(all(valid_batch(r) for r in latest.values()))

    def test_frontend_sections_and_state_semantics(self):
        for marker in [
            "市场总览", "今日榜单", "历史趋势", "剧目库", "题材 / 剧目研究",
            "Official Web", "Research Queue", "数据审计",
            "PENDING", "RESEARCHING", "COMPLETE", "NEEDS_GPT", "REVIEW_REQUIRED", "FAILED",
        ]:
            self.assertIn(marker, self.html)

    def test_newness_and_poster_safety(self):
        self.assertIn("没有 previous rank ≠ NEW", self.html)
        self.assertIn("r.posterUrl", self.html)
        self.assertNotIn("r.poster || r.cover", self.html)
        self.assertNotIn("title-only", self.html)
        self.assertIn("不跨平台合并 Drama Identity", self.html)

    def test_identity_matches_integration_contract(self):
        self.assertEqual(norm("(DUBBED) Justice in Blood"), norm("Justice in Blood"))
        self.assertEqual(norm("Ruling Over All I See (DUBBED)"), norm("Ruling Over All I See"))
        self.assertEqual(norm("English Dub Flash Marriage CEO Spoils Me a Lot"), norm("Flash Marriage CEO Spoils Me a Lot"))
        self.assertNotEqual(norm("The Boy Dubbed King"), norm("The Boy King"))
        self.assertIn("function stripReleaseMarkers(v)", self.html)

    def test_research_projection_is_scope_safe(self):
        self.assertIn("String(t.platform||'')+'::'+tk+'::'+norm", self.html)
        self.assertIn("String(t.analysis_run_id)+'::'+norm", self.html)
        self.assertIn("Research 投影绑定 platform + targetKey + title", self.html)

    def test_full_raw_datasets_and_field_matrix_are_visible(self):
        for marker in [
            "Frontend Field Coverage Matrix",
            "analysis_runs · 全量 36",
            "collection_jobs · 全量 15",
            "collection_targets · 全量 20",
            "source_registry · 全量 16",
            "drama_overrides · 全量 104",
            "完整 JSON",
        ]:
            self.assertIn(marker, self.html)

if __name__ == "__main__":
    unittest.main()
