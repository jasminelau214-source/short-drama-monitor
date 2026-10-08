import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class CoreContractV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads((ROOT / "core_contract_v2.json").read_text(encoding="utf-8"))
        cls.manifest = json.loads((ROOT / "promotion_manifest_v2.json").read_text(encoding="utf-8"))

    def test_contract_is_locked_but_not_implemented(self):
        self.assertEqual(self.contract["contractVersion"], "2.0")
        self.assertEqual(self.contract["status"], "LOCKED_NOT_IMPLEMENTED")

    def test_verified_source_first_seen_is_global_research_trigger(self):
        source = self.contract["sourceSemantics"]
        identity = self.contract["identityAndNewness"]
        self.assertTrue(source["verifiedSourceFirstSeenTriggersResearch"])
        self.assertEqual(identity["firstSeenTrigger"], "first_seen_any_verified_source")
        self.assertEqual(identity["researchSubjectScope"], "GLOBAL_CANONICAL_DRAMA")
        self.assertTrue(identity["dedupeAcrossPlatformsAndSources"])

    def test_official_web_scope_and_paused_platforms_are_explicit(self):
        source = self.contract["sourceSemantics"]
        self.assertEqual(
            set(source["activeOfficialWebPlatforms"]),
            {"DramaBox", "FlexTV", "GoodShort", "MoboReels", "NetShort", "ReelShort"},
        )
        self.assertEqual(set(source["pausedPlatforms"]), {"DramaWave", "ShortMax"})
        self.assertEqual(source["collectionProfile"]["language"], "English")
        self.assertEqual(source["collectionProfile"]["locale"], "en-US")
        self.assertEqual(source["collectionProfile"]["region"], "US")

    def test_platform_failure_is_isolated_without_stale_copy(self):
        isolation = self.contract["platformFailureIsolation"]
        self.assertTrue(isolation["independentPlatformCommit"])
        self.assertTrue(isolation["successfulPlatformsProceedImmediately"])
        self.assertFalse(isolation["copyPriorDayIntoCurrentDate"])
        self.assertTrue(isolation["crossPlatformCompositeMustExposeCoverage"])

    def test_research_complete_and_writeback_fail_closed(self):
        complete = self.contract["researchComplete"]
        writeback = self.contract["writeback"]
        self.assertTrue(complete["deterministicSchemaValidationRequired"])
        self.assertTrue(complete["coreFieldsRequired"])
        self.assertTrue(complete["evidenceUrlRequired"])
        self.assertEqual(complete["minimumConfidence"], "medium")
        self.assertTrue(writeback["samePlatformRecordRequired"])
        self.assertTrue(writeback["crossPlatformFallbackForbidden"])

    def test_zero_paid_cost_budget_is_hard_contract(self):
        budget = self.contract["researchBudget"]
        self.assertEqual(budget["mode"], "FREE_ONLY")
        self.assertEqual(budget["paidBudgetUsd"], 0)
        self.assertFalse(budget["automaticPaidFallback"])
        self.assertIsNone(budget["maxQueuedResearchTasks"])
        self.assertEqual(budget["defaultConcurrency"], 2)
        self.assertEqual(budget["technicalRetriesAfterInitialAttempt"], 2)
        self.assertEqual(budget["maxProviderAttemptsPerStage"], 3)
        self.assertTrue(budget["wholeTaskMechanicalRetryForbiddenAfterProviderSpend"])
        self.assertEqual(budget["maxExternalSearchRoundsPerDrama"], 3)
        self.assertEqual(budget["taskTimeoutSeconds"], 300)
        self.assertEqual(budget["quotaExhaustedStatus"], "DEFERRED_FREE_QUOTA")
        self.assertFalse(budget["quotaExhaustionIsFailure"])

    def test_stability_observation_does_not_delay_daily_pipeline(self):
        stability = self.contract["stabilityAndPromotion"]
        self.assertEqual(stability["preProductionFormalWindows"], 3)
        self.assertEqual(stability["postProductionObservationDays"], 7)
        self.assertFalse(stability["stabilityObservationBlocksDailyProcessing"])
        self.assertFalse(stability["ciGreenAloneIsProductionReady"])

    def test_step_a_manifest_is_non_production_and_contract_only(self):
        manifest = self.manifest
        self.assertEqual(manifest["base"]["branch"], "main")
        self.assertEqual(
            manifest["base"]["sha"],
            "a206bc9c21138a9559aecf5a7a34069167268cfc",
        )
        self.assertFalse(manifest["integration"]["wholeBranchPromotionAllowed"])
        self.assertEqual(manifest["promotionGate"]["currentStatus"], "BLOCK_PROMOTION")
        self.assertTrue(all(value is False for value in manifest["authorization"].values()))
        self.assertFalse(manifest["stepA"]["runtimeCodeChangeAllowed"])
        self.assertFalse(manifest["stepA"]["schemaChangeAllowed"])
        self.assertFalse(manifest["stepA"]["schedulerChangeAllowed"])
        self.assertFalse(manifest["stepA"]["deploymentChangeAllowed"])

    def test_step_a_changed_surface_is_exact_and_non_runtime(self):
        expected = {
            "core_contract_v2.json",
            "docs/CORE_CONTRACT_V2.md",
            "promotion_manifest_v2.json",
            "test_core_contract_v2.py",
        }
        self.assertEqual(set(self.manifest["stepA"]["allowedPaths"]), expected)
        forbidden_prefixes = (
            "supabase/",
            "windows/",
            ".github/workflows/",
        )
        for path in expected:
            self.assertFalse(path.startswith(forbidden_prefixes))
            self.assertNotIn(path, {"app.py", "persistence.py", "research_worker.py", "render.yaml"})

    def test_legacy_branches_are_evidence_only(self):
        legacy = self.manifest["legacyEvidence"]
        self.assertFalse(legacy["integration/core-contract-v1-2026-09-20"]["wholeBranchMergeAllowed"])
        self.assertEqual(
            legacy["integration/core-contract-v1-2026-09-20"]["role"],
            "EVIDENCE_AND_CANDIDATE_PATCH_SOURCE_ONLY",
        )


if __name__ == "__main__":
    unittest.main()
