from __future__ import annotations

import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent
MANIFEST = json.loads(
    (ROOT / "production_candidate_manifest.json").read_text(encoding="utf-8")
)


class ProductionCandidateManifestTests(unittest.TestCase):
    def setUp(self):
        units = MANIFEST["promotionUnits"]
        nonruntime = MANIFEST["nonRuntimeAssets"]
        self.backend = set(units["backendCoreRuntime"])
        self.collectors = set(units["collectorSchedulerRuntime"])
        self.ui = set(units["uiV2Runtime"])
        self.docs = set(nonruntime["docs"])
        self.validation = set(nonruntime["validationOnly"])
        self.tests = set(nonruntime["testsAndFixtures"])

    def test_all_groups_are_disjoint(self):
        groups = {
            "backend": self.backend,
            "collectors": self.collectors,
            "ui": self.ui,
            "docs": self.docs,
            "validation": self.validation,
            "tests": self.tests,
        }
        names = list(groups)
        for i, left in enumerate(names):
            for right in names[i + 1:]:
                overlap = groups[left] & groups[right]
                self.assertEqual(
                    overlap,
                    set(),
                    f"{left} and {right} overlap: {sorted(overlap)}",
                )

    def test_manifest_accounts_for_audited_surface(self):
        all_paths = (
            self.backend
            | self.collectors
            | self.ui
            | self.docs
            | self.validation
            | self.tests
        )
        self.assertEqual(
            len(all_paths),
            MANIFEST["integration"]["changedFilesAtAudit"],
        )

    def test_backend_candidate_excludes_ui_and_validation_scaffolding(self):
        candidate = self.backend | self.collectors
        forbidden = self.ui | self.validation | self.tests
        self.assertFalse(candidate & forbidden)
        self.assertNotIn("index.html", candidate)
        self.assertNotIn("runtime_ui_patch.py", candidate)
        self.assertFalse(
            any(path.startswith("integration_") for path in candidate),
            sorted(path for path in candidate if path.startswith("integration_")),
        )
        self.assertFalse(
            any(path.startswith(".github/workflows/integration-") for path in candidate)
        )
        self.assertFalse(any(path.startswith("tests/fixtures/") for path in candidate))

    def test_backend_candidate_contains_critical_contract_runtime(self):
        required = {
            "app.py",
            "collector_import.py",
            "drama_identity.py",
            "ranking_lifecycle.py",
            "research_guard.py",
            "research_validation.py",
            "research_writeback.py",
            "source_semantics.py",
            "state_semantics.py",
            "persistence.py",
            "render.yaml",
            "supabase/migrations/20260920_01_drama_identity_contract_v1.sql",
        }
        self.assertTrue(required <= (self.backend | self.collectors))

    def test_collector_candidate_contains_hardened_app_collectors(self):
        required = {
            "windows/collectors/netshort_collector_v6.ps1",
            "windows/collectors/moboreels_collector_v3.ps1",
            "windows/run_app_collect_and_sync.ps1",
            "windows/run_app_device_acceptance.ps1",
        }
        self.assertTrue(required <= self.collectors)

    def test_ui_is_explicitly_separate(self):
        candidate_b = MANIFEST["candidateDefinitions"]["uiCandidateB"]
        self.assertTrue(candidate_b["mayShipSeparately"])
        self.assertEqual(candidate_b["includeUnits"], ["uiV2Runtime"])
        self.assertEqual(self.ui, {"index.html", "runtime_ui_patch.py"})

    def test_production_mutations_remain_unapproved(self):
        policy = MANIFEST["policy"]
        self.assertFalse(policy["wholeIntegrationMergeAllowed"])
        self.assertFalse(policy["productionWriteAuthorized"])
        self.assertFalse(policy["productionDbMigrationAuthorized"])
        self.assertFalse(policy["uiV2BundledByDefault"])
        self.assertTrue(
            MANIFEST["candidateDefinitions"]["backendCandidateA"][
                "productionDbMigrationRequiresExplicitApproval"
            ]
        )
        self.assertTrue(
            MANIFEST["candidateDefinitions"]["backendCandidateA"][
                "realDeviceAcceptanceRequiredBeforePromotion"
            ]
        )

    def test_every_manifest_path_exists_on_integration(self):
        all_paths = (
            self.backend
            | self.collectors
            | self.ui
            | self.docs
            | self.validation
            | self.tests
        )
        missing = [path for path in sorted(all_paths) if not (ROOT / path).exists()]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
