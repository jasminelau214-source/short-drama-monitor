from __future__ import annotations

import pathlib
import unittest

from collector_import import validate_and_normalize
from state_semantics import (
    ANALYSIS_RUN_ANALYZING,
    ANALYSIS_RUN_COLLECTED,
    ANALYSIS_RUN_COMPLETE,
    ANALYSIS_RUN_FAILED,
    ANALYSIS_RUN_RESEARCH_PENDING,
    ANALYSIS_RUN_STATUSES,
    COLLECTION_JOB_STATUSES,
    RESEARCH_TASK_STATUSES,
    analysis_run_status_for,
    analysis_run_read_semantics,
    is_analysis_run_terminal,
    is_legacy_incomplete_analysis_status,
    require_stage_state,
)


ROOT = pathlib.Path(__file__).resolve().parent


def rows():
    return [{"rank": i, "title": f"Drama {i}"} for i in range(1, 11)]


def payload(source_type: str):
    return {
        "platform": "MoboReels",
        "source_type": source_type,
        "source_id": "shortapp_moboreels" if source_type == "SHORT_DRAMA_APP" else "web_moboreels",
        "target_key": "daily_top_all",
        "ranking_type": "Trending Series",
        "category": "All",
        "collection_method": "APP_UI_XML_SCROLL" if source_type == "SHORT_DRAMA_APP" else "OFFICIAL_WEB",
        "collector_version": "state-test",
        "collection_date": "2026-09-21",
        "collected_at": "2026-09-21T10:00:00",
        "batch_complete": True,
        "missing_ranks": [],
        "duplicate_ranks": [],
        "duplicate_titles": [],
        "rank_conflicts": [],
        "evidence": {
            "originalSourceType": source_type,
            "semanticVerified": True,
            "appFocusVerified": True,
            "targetLabel": "Trending Series",
            "ui_xml_pages": ["D:/fixture/page1.xml"],
        },
        "rows": rows(),
    }


class StateSemanticsTests(unittest.TestCase):
    def test_stage_sets_are_separate(self):
        self.assertIn(ANALYSIS_RUN_ANALYZING, ANALYSIS_RUN_STATUSES)
        self.assertIn("COMPLETE", RESEARCH_TASK_STATUSES)
        self.assertIn("SUCCEEDED", COLLECTION_JOB_STATUSES)
        self.assertNotIn("COMPLETE", ANALYSIS_RUN_STATUSES)
        self.assertNotIn(ANALYSIS_RUN_COMPLETE, RESEARCH_TASK_STATUSES)

    def test_legacy_free_text_states_are_read_only_and_non_authoritative(self):
        for raw, expected in (
            ("分析中-待复核", "LEGACY_REVIEW_REQUIRED"),
            ("分析中-待补齐", "LEGACY_INCOMPLETE"),
        ):
            semantic = analysis_run_read_semantics(raw)
            self.assertTrue(semantic["known"])
            self.assertTrue(semantic["legacy"])
            self.assertFalse(semantic["writeAllowed"])
            self.assertFalse(semantic["authoritativeEligibleByState"])
            self.assertEqual(semantic["state"], expected)
            self.assertTrue(is_legacy_incomplete_analysis_status(raw))
            with self.assertRaisesRegex(ValueError, "INVALID_ANALYSIS_RUN_STATE"):
                require_stage_state("analysis_run", raw)

    def test_unknown_historical_state_is_not_silently_mapped(self):
        semantic = analysis_run_read_semantics("神秘状态")
        self.assertFalse(semantic["known"])
        self.assertTrue(semantic["legacy"])
        self.assertFalse(semantic["writeAllowed"])
        self.assertFalse(semantic["authoritativeEligibleByState"])
        self.assertEqual(semantic["state"], "UNKNOWN")

    def test_unknown_state_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "INVALID_ANALYSIS_RUN_STATE"):
            require_stage_state("analysis_run", "SUCCESS")
        with self.assertRaisesRegex(ValueError, "UNKNOWN_STATE_STAGE"):
            require_stage_state("global", "COMPLETE")

    def test_analysis_run_state_projection(self):
        self.assertEqual(
            analysis_run_status_for(source_type="OFFICIAL_WEB", has_new_titles=True),
            ANALYSIS_RUN_COLLECTED,
        )
        self.assertEqual(
            analysis_run_status_for(source_type="SHORT_DRAMA_APP", has_new_titles=True),
            ANALYSIS_RUN_RESEARCH_PENDING,
        )
        self.assertEqual(
            analysis_run_status_for(source_type="SHORT_DRAMA_APP", has_new_titles=False),
            ANALYSIS_RUN_COMPLETE,
        )

    def test_terminal_semantics_are_explicit(self):
        self.assertFalse(is_analysis_run_terminal(ANALYSIS_RUN_ANALYZING))
        for state in (
            ANALYSIS_RUN_COLLECTED,
            ANALYSIS_RUN_RESEARCH_PENDING,
            ANALYSIS_RUN_COMPLETE,
            ANALYSIS_RUN_FAILED,
        ):
            self.assertTrue(is_analysis_run_terminal(state))

    def test_collector_import_uses_stage_contract(self):
        # Official-Web projection is covered directly above. Full Web imports
        # additionally require Source Promotion evidence and should not be
        # weakened merely to exercise the state mapper here.
        app_new = validate_and_normalize(payload("SHORT_DRAMA_APP"), [])
        self.assertEqual(app_new["status"], ANALYSIS_RUN_RESEARCH_PENDING)

        app_old = validate_and_normalize(
            payload("SHORT_DRAMA_APP"),
            [f"Drama {i}" for i in range(1, 11)],
        )
        self.assertEqual(app_old["status"], ANALYSIS_RUN_COMPLETE)

    def test_app_write_boundary_imports_state_contract(self):
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("require_stage_state('analysis_run', status)", app)
        self.assertIn("ANALYSIS_RUN_RESEARCH_PENDING", app)
        self.assertIn("ANALYSIS_RUN_COMPLETE", app)
        self.assertIn("ANALYSIS_RUN_FAILED", app)


if __name__ == "__main__":
    unittest.main()
