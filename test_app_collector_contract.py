from __future__ import annotations

import pathlib
import unittest

from app_collector_evidence import app_collector_evidence_error
from collector_import import CollectorImportError, validate_and_normalize


ROOT = pathlib.Path(__file__).resolve().parent
COLLECTOR_DIR = ROOT / 'windows' / 'collectors'


def rows():
    return [{'rank': i, 'title': f'Drama {i}'} for i in range(1, 11)]


def valid_payload(method='APP_UI_XML'):
    evidence = {
        'originalSourceType': 'SHORT_DRAMA_APP',
        'semanticVerified': True,
        'appFocusVerified': True,
        'targetLabel': 'Rankings',
    }
    if method == 'APP_UI_XML':
        evidence['ui_xml'] = r'D:\\ShortDramaCollector\\evidence.xml'
    else:
        evidence['ui_xml_pages'] = [
            r'D:\\ShortDramaCollector\\page1.xml',
            r'D:\\ShortDramaCollector\\page2.xml',
        ]
    return {
        'platform': 'NetShort' if method == 'APP_UI_XML' else 'MoboReels',
        'source_type': 'SHORT_DRAMA_APP',
        'source_id': 'shortapp_netshort' if method == 'APP_UI_XML' else 'shortapp_moboreels',
        'target_key': 'daily_top_all',
        'ranking_type': 'Top Trending',
        'category': 'All',
        'collection_method': method,
        'collector_version': 'integration-test',
        'collection_date': '2026-09-20',
        'collected_at': '2026-09-20T08:00:00',
        'adb_serial': '127.0.0.1:5555',
        'batch_complete': True,
        'missing_ranks': [],
        'duplicate_ranks': [],
        'duplicate_titles': [],
        'rank_conflicts': [],
        'evidence': evidence,
        'rows': rows(),
    }


class StructuredAppEvidenceTests(unittest.TestCase):
    def test_valid_xml_evidence_passes(self):
        payload = valid_payload('APP_UI_XML')
        self.assertEqual(app_collector_evidence_error(payload), '')
        self.assertEqual(validate_and_normalize(payload, [])['rowCount'], 10)

    def test_valid_scroll_evidence_passes(self):
        payload = valid_payload('APP_UI_XML_SCROLL')
        self.assertEqual(app_collector_evidence_error(payload), '')
        self.assertEqual(validate_and_normalize(payload, [])['rowCount'], 10)

    def test_missing_semantic_verification_fails_closed(self):
        payload = valid_payload()
        payload['evidence']['semanticVerified'] = False
        with self.assertRaisesRegex(CollectorImportError, 'APP_EVIDENCE_TARGET_SEMANTIC_UNVERIFIED'):
            validate_and_normalize(payload, [])

    def test_rank_conflict_fails_closed(self):
        payload = valid_payload('APP_UI_XML_SCROLL')
        payload['rank_conflicts'] = [{'rank': 4, 'first_title': 'A', 'later_title': 'B'}]
        with self.assertRaisesRegex(CollectorImportError, 'APP_EVIDENCE_RANK_CONFLICT'):
            validate_and_normalize(payload, [])

    def test_missing_rank_conflict_audit_fails_closed(self):
        payload = valid_payload('APP_UI_XML')
        payload.pop('rank_conflicts')
        with self.assertRaisesRegex(CollectorImportError, 'APP_EVIDENCE_RANK_CONFLICT_AUDIT_MISSING'):
            validate_and_normalize(payload, [])


class PowerShellCollectorStaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.net = (COLLECTOR_DIR / 'netshort_collector_v6.ps1').read_text(encoding='utf-8')
        cls.mobo = (COLLECTOR_DIR / 'moboreels_collector_v3.ps1').read_text(encoding='utf-8')
        cls.net_runner = (COLLECTOR_DIR / 'run_netshort_collector_v6.bat').read_text(encoding='utf-8')
        cls.mobo_runner = (COLLECTOR_DIR / 'run_moboreels_collector_v3.bat').read_text(encoding='utf-8')

    def test_no_arbitrary_adb_device_fallback(self):
        for script in (self.net, self.mobo):
            self.assertIn('NO_BLUESTACKS_ADB_DEVICE', script)
            self.assertIn('MULTIPLE_BLUESTACKS_DEVICES', script)
            self.assertIn('REQUESTED_ADB_DEVICE_NOT_AVAILABLE', script)

    def test_app_focus_and_semantics_are_explicit(self):
        self.assertIn('Assert-App-Focus', self.net)
        self.assertIn('semanticVerified = $true', self.net)
        self.assertIn('appFocusVerified = $true', self.net)
        self.assertIn('Assert-App-Focus', self.mobo)
        self.assertIn('TARGET_SEMANTIC_LOST_PAGE', self.mobo)
        self.assertIn('semanticVerified = $true', self.mobo)
        self.assertIn('appFocusVerified = $true', self.mobo)

    def test_moboreels_detects_rank_conflicts(self):
        self.assertIn('$rankConflicts', self.mobo)
        self.assertIn('Rank conflict #', self.mobo)
        self.assertIn('rank_conflicts = @($rankConflicts)', self.mobo)

    def test_ui_evidence_commands_have_hard_timeout(self):
        for script in (self.net, self.mobo):
            self.assertIn('UiDumpTimeoutSec = 10', script)
            self.assertIn('function Invoke-AdbWithTimeout', script)
            self.assertIn('-TimeoutSec $UiDumpTimeoutSec', script)
            self.assertIn('$dumpResult.TimedOut', script)
            self.assertIn('$pullResult.TimedOut', script)
            self.assertIn('$dumpResult.ExitCode -ne 0', script)
            self.assertIn('$pullResult.ExitCode -ne 0', script)

    def test_runtime_banner_matches_hardened_version(self):
        self.assertIn('NetShort Collector V6 complete', self.net)
        self.assertNotIn('NetShort Collector V5 complete', self.net)
        self.assertIn('MoboReels Collector V3 complete', self.mobo)
        self.assertNotIn('MoboReels Collector V2 complete', self.mobo)

    def test_audit_failure_returns_nonzero(self):
        for script in (self.net, self.mobo):
            self.assertIn('RESULT: AUDIT_FAILED', script)
            self.assertIn('exit 2', script)
            self.assertIn('exit 0', script)

    def test_batch_runners_propagate_exit_code_and_do_not_use_noexit(self):
        for runner in (self.net_runner, self.mobo_runner):
            self.assertNotIn('-NoExit', runner)
            self.assertIn('exit /b %EXITCODE%', runner)


if __name__ == '__main__':
    unittest.main()
