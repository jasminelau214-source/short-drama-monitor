from __future__ import annotations

import json
import pathlib
import unittest

from collector_import import validate_and_normalize


ROOT = pathlib.Path(__file__).resolve().parent
FIXTURES = ROOT / 'tests' / 'fixtures'


class AppCollectorOutputBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        golden = json.loads((FIXTURES / 'golden_real_app_20260917.json').read_text(encoding='utf-8'))
        cls.cases = {
            'NetShort': (
                FIXTURES / 'collector_output_netshort_v6_20260917.json',
                golden['platforms']['NetShort']['priorAppTitles'],
                0,
                10,
            ),
            'MoboReels': (
                FIXTURES / 'collector_output_moboreels_v3_20260917.json',
                golden['platforms']['MoboReels']['priorAppTitles'],
                9,
                1,
            ),
        }

    def test_serialized_collector_outputs_cross_import_boundary(self):
        for platform, (path, known, expected_new, expected_old) in self.cases.items():
            payload = json.loads(path.read_text(encoding='utf-8'))
            normalized = validate_and_normalize(payload, known)
            self.assertEqual(normalized['platform'], platform)
            self.assertEqual(normalized['rowCount'], 10)
            self.assertEqual(normalized['newTitleCount'], expected_new)
            rows = normalized['result']['rows']
            self.assertEqual(sum(x['newness'] == 'old' for x in rows), expected_old)
            self.assertEqual(
                normalized['result']['collector']['collectorVersion'],
                payload['collector_version'],
            )
            self.assertTrue(
                normalized['result']['collector']['evidence']['semanticVerified']
            )
            self.assertTrue(
                normalized['result']['collector']['evidence']['appFocusVerified']
            )

    def test_moboreels_rank_conflict_serialized_output_is_rejected(self):
        path = FIXTURES / 'collector_output_moboreels_v3_20260917.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['rank_conflicts'] = [{
            'rank': 4,
            'first_title': "The King's Physician Bride",
            'later_title': 'Different Title',
            'page': 2,
        }]
        with self.assertRaisesRegex(Exception, 'APP_EVIDENCE_RANK_CONFLICT'):
            validate_and_normalize(
                payload,
                self.cases['MoboReels'][1],
            )


if __name__ == '__main__':
    unittest.main()
