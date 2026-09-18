import unittest

from collector_import import _norm_title
from research_pipeline import CORE_FIELDS, classify_research_status
from shadow_e2e.run_shadow_e2e import (
    normalize_all,
    research_preflight,
    shadow_persistence_idempotency,
    task_rows,
    title_normalization_audit,
    unique_titles,
)


class ShadowE2E7PlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payloads, cls.runs = normalize_all("all_verified")

    def test_verified_input_is_7x10_and_unique(self):
        self.assertEqual(len(self.payloads), 7)
        self.assertEqual(sum(x["rowCount"] for x in self.runs), 70)
        self.assertEqual(len(unique_titles(self.runs)), 70)
        self.assertTrue(all(x["collectionDate"] == "2026-09-18" for x in self.runs))

    def test_collector_contract_semantics(self):
        for run in self.runs:
            collector = run["result"]["collector"]
            self.assertEqual(collector["sourceType"], "SHORT_DRAMA_APP")
            self.assertEqual(collector["collectionMethod"], "WEB_SCRAPE")
            self.assertEqual(collector["topN"], 10)

    def test_business_correct_newness_funnel(self):
        self.assertEqual(sum(x["newTitleCount"] for x in self.runs), 27)
        self.assertEqual(len(task_rows(self.runs)), 27)

    def test_current_history_semantics_overgenerate_newness(self):
        _, current = normalize_all("current_app")
        self.assertEqual(sum(x["newTitleCount"] for x in current), 55)

    def test_exact_replay_is_idempotent_in_shadow_store(self):
        result = shadow_persistence_idempotency(self.runs)
        self.assertTrue(result["pass"])
        self.assertEqual(result["rowsAfterReplay"], 7)

    def test_title_normalizer_strips_anchored_dub_markers(self):
        audit = title_normalization_audit()
        self.assertTrue(audit["supportsCaseVariation"])
        self.assertTrue(audit["supportsPunctuationVariation"])
        self.assertTrue(audit["supportsDubbedMarkerRemoval"])
        self.assertEqual(_norm_title("Ruling Over All I See (DUBBED)"), _norm_title("Ruling Over All I See"))
        self.assertEqual(_norm_title("(DUBBED)Justice in Blood"), _norm_title("Justice in Blood"))
        self.assertEqual(_norm_title("[ENG DUB] Flash Marriage CEO Spoils Me a Lot"), _norm_title("Flash Marriage CEO Spoils Me a Lot"))

    def test_research_preflight_allows_all_27_tasks(self):
        preflight = research_preflight(task_rows(self.runs))
        self.assertEqual(preflight["taskCount"], 27)
        self.assertEqual(preflight["allowedTaskCount"], 27)
        self.assertEqual(preflight["blockedTaskCount"], 0)
        self.assertTrue(all(x["officialDomainMapped"] for x in preflight["tasks"]))

    def test_research_status_gate_blocks_incomplete_or_conflicted_results(self):
        base = {field: "verified" for field in CORE_FIELDS}
        base.update({
            "canonicalTitle": "Example Drama",
            "newnessResolution": "new",
            "confidence": "medium",
            "missingFields": [],
            "auditNotes": [],
            "sourceUrls": ["https://example.com/drama"],
            "needsGPT": False,
        })
        status, missing = classify_research_status(
            requested_title="Example Drama",
            result=base,
            search_meta={"officialCount": 1, "sanitizedSources": 0},
        )
        self.assertEqual(status, "COMPLETE")
        self.assertEqual(missing, [])

        incomplete = dict(base)
        incomplete["synopsis"] = ""
        status, missing = classify_research_status(
            requested_title="Example Drama",
            result=incomplete,
            search_meta={"officialCount": 1, "sanitizedSources": 0},
        )
        self.assertEqual(status, "NEEDS_GPT")
        self.assertIn("synopsis", missing)

        conflicted = dict(base)
        conflicted["auditNotes"] = ["source conflict on identity"]
        status, _ = classify_research_status(
            requested_title="Example Drama",
            result=conflicted,
            search_meta={"officialCount": 1, "sanitizedSources": 0},
        )
        self.assertEqual(status, "REVIEW_REQUIRED")


if __name__ == "__main__":
    unittest.main()
