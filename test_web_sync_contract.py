from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent
WINDOWS = ROOT / 'windows'


class ScheduledWebSyncContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.collect_and_sync = (WINDOWS / 'run_web_collect_and_sync.ps1').read_text(encoding='utf-8')
        cls.sync = (WINDOWS / 'collector_sync.ps1').read_text(encoding='utf-8')
        cls.scheduled = (WINDOWS / 'scheduled_web_collect.ps1').read_text(encoding='utf-8')
        cls.installer = (WINDOWS / 'install_web_collect_task.ps1').read_text(encoding='utf-8')
        cls.runner = (ROOT / 'run_official_web_collect.py').read_text(encoding='utf-8')

    def test_collection_emits_exact_run_manifest(self):
        self.assertIn("'--manifest'", self.runner)
        self.assertIn("'runId': run_id", self.runner)
        self.assertIn("'succeeded': results", self.runner)

    def test_scheduled_web_path_passes_manifest_to_sync(self):
        self.assertIn('--manifest $manifestPath', self.collect_and_sync)
        self.assertIn('-ManifestPath $manifestPath', self.collect_and_sync)

    def test_sync_manifest_mode_does_not_fallback_to_directory_scan(self):
        self.assertIn('if ($ManifestPath)', self.sync)
        self.assertIn('COLLECTOR_MANIFEST_HAS_NO_SUCCEEDED_FILES', self.sync)
        self.assertIn('COLLECTOR_MANIFEST_FILE_MISSING', self.sync)
        self.assertIn('COLLECTOR_MANIFEST_DATE_MISMATCH', self.sync)

    def test_integration_sync_is_local_by_default_and_prod_requires_opt_in(self):
        for script in (self.sync, self.collect_and_sync, self.scheduled):
            self.assertIn('http://127.0.0.1:4173', script)
            self.assertIn('AllowProductionWrite', script)
        self.assertIn('PRODUCTION_WRITE_BLOCKED', self.sync)
        self.assertIn('short-drama-monitor.onrender.com', self.sync)

    def test_directory_scan_rejects_stale_collector_outputs(self):
        self.assertIn('MaxCollectorAgeMinutes = 90', self.sync)
        self.assertIn('COLLECTOR_MAX_AGE_INVALID', self.sync)
        self.assertIn('Skip stale collector JSON', self.sync)
        self.assertIn('$freshCutoff', self.sync)

    def test_partial_collection_keeps_failure_signal(self):
        self.assertIn('exit 2', self.collect_and_sync)
        self.assertIn('PARTIAL:', self.scheduled)
        self.assertIn('SCHEDULED_COLLECTION_EXHAUSTED', self.scheduled)

    def test_collect_only_mode_skips_backend_login_and_sync(self):
        for script in (self.collect_and_sync, self.scheduled, self.installer):
            self.assertIn('[switch]$CollectOnly', script)
        self.assertIn('if ($CollectOnly.IsPresent)', self.collect_and_sync)
        self.assertIn('no backend sync was attempted', self.collect_and_sync)
        self.assertIn('if (-not $CollectOnly.IsPresent)', self.scheduled)
        self.assertIn('-CollectOnly', self.scheduled)
        self.assertIn('backend sync was skipped', self.scheduled)
        self.assertIn('$savedErrorActionPreference', self.scheduled)
        self.assertIn('PARTIAL_EXHAUSTED', self.scheduled)
        self.assertIn('if (-not $CollectOnly.IsPresent)', self.installer)


if __name__ == '__main__':
    unittest.main()
