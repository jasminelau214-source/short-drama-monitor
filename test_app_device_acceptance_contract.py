from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent
SCRIPT = (ROOT / 'windows' / 'run_app_device_acceptance.ps1').read_text(encoding='utf-8')
RUNNER = (ROOT / 'windows' / 'run_app_device_acceptance.bat').read_text(encoding='utf-8')


class AppDeviceAcceptanceContractTests(unittest.TestCase):
    def test_acceptance_runs_both_hardened_collectors(self):
        self.assertIn('netshort_collector_v6.ps1', SCRIPT)
        self.assertIn('moboreels_collector_v3.ps1', SCRIPT)
        self.assertIn('netshort-v6-integration', SCRIPT)
        self.assertIn('moboreels-v3-integration', SCRIPT)

    def test_acceptance_is_explicitly_non_production(self):
        self.assertIn('productionWrite = $false', SCRIPT)
        self.assertNotIn('SUPABASE_PERSISTENCE_URL', SCRIPT)
        self.assertNotIn('MONITOR_PERSISTENCE_TOKEN', SCRIPT)
        self.assertNotIn('AllowProductionWrite', SCRIPT)
        self.assertNotIn('collector_sync.ps1', SCRIPT)

    def test_acceptance_requires_current_run_outputs_and_evidence(self):
        for token in (
            'CURRENT_RUN_OUTPUT_NOT_FOUND',
            'TARGET_SEMANTIC_UNVERIFIED',
            'APP_FOCUS_UNVERIFIED',
            'EVIDENCE_FILE_MISSING',
            'TOP10_INCOMPLETE',
            'RANK_SEQUENCE_INVALID',
            'DUPLICATE_TITLE',
        ):
            self.assertIn(token, SCRIPT)

    def test_acceptance_manifest_is_fail_closed(self):
        self.assertIn('acceptanceVersion = "app-device-acceptance-v1"', SCRIPT)
        self.assertIn('if ($Manifest.summary.status -eq "PASS")', SCRIPT)
        self.assertIn('exit 2', SCRIPT)

    def test_batch_runner_propagates_exit_code_without_noexit(self):
        self.assertNotIn('-NoExit', RUNNER)
        self.assertIn('exit /b %EXITCODE%', RUNNER)
        self.assertIn('does NOT sync or write to production', RUNNER)


if __name__ == '__main__':
    unittest.main()
