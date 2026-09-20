from __future__ import annotations

import json
import pathlib
import unittest
from datetime import datetime, timedelta, timezone

from collector_import import CollectorImportError, validate_and_normalize
from drama_identity import normalize_title
from research_guard import assess_research_task
from source_evidence_guard import audit_source_evidence, gate_collector_status


ROOT = pathlib.Path(__file__).resolve().parent


def app_payload(rows, *, top_n=10, source_type='SHORT_DRAMA_APP'):
    return {
        'platform': 'ReelShort',
        'source_type': source_type,
        'source_id': 'test_reelshort',
        'target_key': 'daily_top_all',
        'ranking_type': 'TOP',
        'collection_method': 'IMPORT',
        'collection_date': '2026-09-20',
        'top_n': top_n,
        'batch_complete': True,
        'rows': rows,
    }


def ten_rows():
    return [{'rank': i, 'title': f'Drama {i}'} for i in range(1, 11)]


class CollectorFaultGateTests(unittest.TestCase):
    def assert_rejected(self, rows, top_n=10):
        with self.assertRaises(CollectorImportError):
            validate_and_normalize(app_payload(rows, top_n=top_n), known_titles=[])

    def test_missing_row_fails_closed(self):
        self.assert_rejected(ten_rows()[:-1])

    def test_duplicate_rank_fails_closed(self):
        rows = ten_rows()
        rows[1]['rank'] = 1
        self.assert_rejected(rows)

    def test_duplicate_title_fails_closed(self):
        rows = ten_rows()
        rows[1]['title'] = rows[0]['title']
        self.assert_rejected(rows)

    def test_empty_title_fails_closed(self):
        rows = ten_rows()
        rows[0]['title'] = ''
        self.assert_rejected(rows)

    def test_unexpected_extra_row_fails_closed(self):
        rows = ten_rows() + [{'rank': 11, 'title': 'Injected Extra Row'}]
        self.assert_rejected(rows, top_n=10)

    def test_official_web_never_creates_app_new_titles(self):
        result = validate_and_normalize(
            app_payload(ten_rows(), source_type='OFFICIAL_WEB'),
            known_titles=[],
        )
        self.assertEqual(result['newTitleCount'], 0)
        self.assertEqual(result['status'], '已采集')
        self.assertTrue(all(row['newness'] == 'observed' for row in result['result']['rows']))
        self.assertTrue(all(row['pendingChecks'] == [] for row in result['result']['rows']))

    def test_historical_dubbed_collision_is_now_one_identity(self):
        self.assertEqual(
            normalize_title('Serendipitous Love （DUBBED)'),
            normalize_title('Serendipitous Love'),
        )
        self.assertEqual(
            normalize_title('[Dubbed]Last Shelter:The Awakened Zoo'),
            normalize_title('Last Shelter:The Awakened Zoo'),
        )


class SourceEvidenceFaultGateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
        self.url = 'https://www.example.com/ranking'
        self.valid = {
            'httpStatus': 200,
            'pageUrl': 'https://example.com/ranking',
            'semanticVerified': True,
            'fetchedAt': self.now.isoformat(),
        }

    def test_valid_source_keeps_candidate_pass(self):
        control = audit_source_evidence(expected_url=self.url, evidence=self.valid, now=self.now)
        self.assertTrue(control['pass'])
        self.assertEqual(gate_collector_status('PASS_CANDIDATE', control), 'PASS_CANDIDATE')

    def test_http_503_turns_candidate_into_fail(self):
        control = audit_source_evidence(
            expected_url=self.url,
            evidence={**self.valid, 'httpStatus': 503},
            now=self.now,
        )
        self.assertFalse(control['pass'])
        self.assertIn('HTTP_STATUS_INVALID', control['errors'])
        self.assertEqual(gate_collector_status('PASS_CANDIDATE', control), 'FAIL')

    def test_cross_host_redirect_turns_candidate_into_fail(self):
        control = audit_source_evidence(
            expected_url=self.url,
            evidence={**self.valid, 'pageUrl': 'https://example.invalid/control'},
            now=self.now,
        )
        self.assertFalse(control['pass'])
        self.assertIn('OFFICIAL_HOST_MISMATCH', control['errors'])
        self.assertEqual(gate_collector_status('PASS_VERIFIED', control), 'FAIL')

    def test_semantic_replacement_turns_candidate_into_fail(self):
        control = audit_source_evidence(
            expected_url=self.url,
            evidence={**self.valid, 'semanticVerified': False},
            now=self.now,
        )
        self.assertFalse(control['pass'])
        self.assertIn('TARGET_SEMANTIC_UNVERIFIED', control['errors'])
        self.assertEqual(gate_collector_status('PASS_CANDIDATE', control), 'FAIL')

    def test_stale_replay_turns_candidate_into_fail(self):
        control = audit_source_evidence(
            expected_url=self.url,
            evidence={**self.valid, 'fetchedAt': (self.now - timedelta(hours=1)).isoformat()},
            now=self.now,
        )
        self.assertFalse(control['pass'])
        self.assertIn('FETCH_EVIDENCE_STALE', control['errors'])


class HistoricalOfficialWebIncidentReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(
            (ROOT / 'tests' / 'fixtures' / 'incident_20260916_official_web_pending.json').read_text(encoding='utf-8')
        )

    def test_historical_fixture_still_represents_115_tasks(self):
        self.assertEqual(self.fixture['totalPendingTasks'], 115)
        self.assertEqual(sum(self.fixture['platforms'].values()), 115)
        self.assertEqual(sum(run['taskCount'] for run in self.fixture['originRuns']), 115)
        self.assertEqual(len(self.fixture['originRuns']), 8)

    def test_all_115_historical_tasks_are_blocked_before_research_api(self):
        runs = []
        tasks = []
        task_index = 0
        for run in self.fixture['originRuns']:
            rows = []
            for rank in range(1, int(run['taskCount']) + 1):
                task_index += 1
                title = f"Historical Incident Task {task_index}"
                rows.append({'rank': rank, 'title': title})
                tasks.append({
                    'analysis_run_id': run['id'],
                    'collection_date': '2026-09-16',
                    'platform': run['platform'],
                    'rank': rank,
                    'title': title,
                })
            runs.append({
                'id': run['id'],
                'collection_date': '2026-09-16',
                'platform': run['platform'],
                'updated_at': '2026-09-16T13:32:39+00:00',
                'result_json': {
                    'batchComplete': bool(run['batchComplete']),
                    'collector': {
                        'sourceType': run['sourceType'],
                        'targetKey': run['targetKey'],
                        'topN': int(run['taskCount']),
                    },
                    'rows': rows,
                },
            })

        self.assertEqual(len(tasks), 115)
        outcomes = [assess_research_task(task, runs) for task in tasks]
        self.assertEqual(sum(not item['current'] for item in outcomes), 115)
        self.assertEqual(
            {item['code'] for item in outcomes},
            {'INVALID_RESEARCH_SOURCE_TYPE'},
        )


if __name__ == '__main__':
    unittest.main()
