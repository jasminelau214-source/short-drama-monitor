from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent
WINDOWS = ROOT / 'windows'


class AppCollectSyncContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = (WINDOWS / 'run_app_collect_and_sync.ps1').read_text(encoding='utf-8')
        cls.bat = (WINDOWS / 'run_app_collect_and_sync.bat').read_text(encoding='utf-8')
        cls.sync = (WINDOWS / 'collector_sync.ps1').read_text(encoding='utf-8')

    def test_runner_executes_both_hardened_collectors(self):
        self.assertIn('netshort_collector_v6.ps1', self.runner)
        self.assertIn('moboreels_collector_v3.ps1', self.runner)
        self.assertIn('netshort-v6-integration', self.runner)
        self.assertIn('moboreels-v3-integration', self.runner)

    def test_runner_only_accepts_current_run_output(self):
        self.assertIn('$freshCutoff = $started.AddSeconds(-2)', self.runner)
        self.assertIn('CURRENT_RUN_OUTPUT_NOT_FOUND', self.runner)
        self.assertIn('OUTPUT_BATCH_INCOMPLETE', self.runner)
        self.assertIn('OUTPUT_SOURCE_TYPE_INVALID', self.runner)
        self.assertIn('OUTPUT_METHOD_MISMATCH', self.runner)
        self.assertIn('OUTPUT_TARGET_MISMATCH', self.runner)
        self.assertIn('OUTPUT_VERSION_MISMATCH', self.runner)

    def test_exact_manifest_is_written_and_used_for_sync(self):
        self.assertIn('succeeded = @($succeeded)', self.runner)
        self.assertIn('failed = @($failed)', self.runner)
        self.assertIn('-ManifestPath $manifestPath', self.runner)
        self.assertIn('COLLECTOR_MANIFEST_HAS_NO_SUCCEEDED_FILES', self.sync)

    def test_partial_platform_failure_does_not_use_old_file_as_replacement(self):
        self.assertIn('if ($succeeded.Count -eq 0)', self.runner)
        self.assertIn('if ($failed.Count -gt 0)', self.runner)
        self.assertIn('exit 2', self.runner)
        self.assertNotIn('Get-CollectorJsonFiles', self.runner)

    def test_integration_app_sync_is_local_by_default(self):
        self.assertIn('http://127.0.0.1:4173', self.runner)
        self.assertIn('AllowProductionWrite', self.runner)
        self.assertIn('productionWriteRequested', self.runner)

    def test_double_click_runner_propagates_exit_code(self):
        self.assertNotIn('-NoExit', self.bat)
        self.assertIn('exit /b %EXITCODE%', self.bat)


if __name__ == '__main__':
    unittest.main()
