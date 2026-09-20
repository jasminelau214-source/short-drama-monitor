from __future__ import annotations

import unittest

import research_worker


class ResearchWorkerExecutionOrderTests(unittest.TestCase):
    def setUp(self):
        self.originals = {
            'claim': research_worker.persistence.claim_research_tasks,
            'runs': research_worker.persistence.list_analysis_runs,
            'update': research_worker.persistence.update_research_task,
            'research_task': research_worker.research_task,
            'sleep': research_worker.time.sleep,
        }
        research_worker.time.sleep = lambda _: None

    def tearDown(self):
        research_worker.persistence.claim_research_tasks = self.originals['claim']
        research_worker.persistence.list_analysis_runs = self.originals['runs']
        research_worker.persistence.update_research_task = self.originals['update']
        research_worker.research_task = self.originals['research_task']
        research_worker.time.sleep = self.originals['sleep']
        research_worker._WORKER_RUNNING = False

    def _one_task_claim(self, task):
        state = {'claimed': False}
        def claim(limit):
            if state['claimed']:
                return []
            state['claimed'] = True
            return [task]
        return claim

    def test_official_web_is_blocked_before_research_provider_call(self):
        task = {
            'id': 'task-web',
            'analysis_run_id': 'run-web',
            'collection_date': '2026-09-16',
            'platform': 'DramaBox',
            'rank': 1,
            'title': 'Historical Web Drama',
            'missing_fields': [],
        }
        run = {
            'id': 'run-web',
            'collection_date': '2026-09-16',
            'platform': 'DramaBox',
            'updated_at': '2026-09-16T13:32:39Z',
            'result_json': {
                'batchComplete': True,
                'collector': {
                    'sourceType': 'OFFICIAL_WEB',
                    'targetKey': 'web_trending_all',
                    'topN': 1,
                },
                'rows': [{'rank': 1, 'title': 'Historical Web Drama'}],
            },
        }

        provider_calls = []
        updates = []
        research_worker.persistence.claim_research_tasks = self._one_task_claim(task)
        research_worker.persistence.list_analysis_runs = lambda limit: [run]
        research_worker.persistence.update_research_task = lambda task_id, **kwargs: updates.append((task_id, kwargs))
        research_worker.research_task = lambda task: provider_calls.append(task) or {
            'status': 'COMPLETE',
            'research': {},
        }

        applied = []
        research_worker._run(apply_research=lambda task, result: applied.append((task, result)), max_tasks=1)

        self.assertEqual(provider_calls, [])
        self.assertEqual(applied, [])
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][1]['status'], 'REVIEW_REQUIRED')
        self.assertEqual(updates[0][1]['error'], 'INVALID_RESEARCH_SOURCE_TYPE')

    def test_superseded_task_is_blocked_before_research_provider_call(self):
        task = {
            'id': 'task-stale',
            'analysis_run_id': 'run-old',
            'collection_date': '2026-09-20',
            'platform': 'ReelShort',
            'rank': 2,
            'title': 'Dropped Drama',
            'missing_fields': [],
        }
        runs = [
            {
                'id': 'run-old',
                'collection_date': '2026-09-20',
                'platform': 'ReelShort',
                'updated_at': '2026-09-20T01:00:00Z',
                'result_json': {
                    'batchComplete': True,
                    'collector': {'sourceType': 'SHORT_DRAMA_APP', 'targetKey': 'daily_top_all', 'topN': 2},
                    'rows': [
                        {'rank': 1, 'title': 'Keep Me'},
                        {'rank': 2, 'title': 'Dropped Drama'},
                    ],
                },
            },
            {
                'id': 'run-new',
                'collection_date': '2026-09-20',
                'platform': 'ReelShort',
                'updated_at': '2026-09-20T02:00:00Z',
                'result_json': {
                    'batchComplete': True,
                    'collector': {'sourceType': 'SHORT_DRAMA_APP', 'targetKey': 'daily_top_all', 'topN': 2},
                    'rows': [
                        {'rank': 1, 'title': 'Keep Me'},
                        {'rank': 2, 'title': 'Replacement Drama'},
                    ],
                },
            },
        ]

        provider_calls = []
        updates = []
        research_worker.persistence.claim_research_tasks = self._one_task_claim(task)
        research_worker.persistence.list_analysis_runs = lambda limit: runs
        research_worker.persistence.update_research_task = lambda task_id, **kwargs: updates.append((task_id, kwargs))
        research_worker.research_task = lambda task: provider_calls.append(task) or {}

        research_worker._run(apply_research=lambda task, result: None, max_tasks=1)

        self.assertEqual(provider_calls, [])
        self.assertEqual(updates[0][1]['status'], 'REVIEW_REQUIRED')
        self.assertEqual(updates[0][1]['error'], 'SUPERSEDED_BY_LATER_RUN')

    def test_malformed_complete_cannot_write_override(self):
        task = {
            'id': 'task-valid-origin',
            'analysis_run_id': 'run-current',
            'collection_date': '2026-09-20',
            'platform': 'ReelShort',
            'rank': 1,
            'title': 'Current Drama',
            'missing_fields': [],
        }
        run = {
            'id': 'run-current',
            'collection_date': '2026-09-20',
            'platform': 'ReelShort',
            'updated_at': '2026-09-20T02:00:00Z',
            'result_json': {
                'batchComplete': True,
                'collector': {'sourceType': 'SHORT_DRAMA_APP', 'targetKey': 'daily_top_all', 'topN': 1},
                'rows': [{'rank': 1, 'title': 'Current Drama'}],
            },
        }

        updates = []
        applied = []
        research_worker.persistence.claim_research_tasks = self._one_task_claim(task)
        research_worker.persistence.list_analysis_runs = lambda limit: [run]
        research_worker.persistence.update_research_task = lambda task_id, **kwargs: updates.append((task_id, kwargs))
        research_worker.research_task = lambda task: {
            'status': 'COMPLETE',
            'research': {
                'canonicalTitle': 'Current Drama',
                'newnessResolution': 'new',
                'confidence': 'medium',
                'missingFields': [],
                'auditNotes': [],
                'sourceUrls': ['https://example.com/current'],
                'needsGPT': False,
            },
            'sources': [{'url': 'https://example.com/current'}],
            'confidence': 'medium',
            'missingFields': [],
            'error': '',
        }

        research_worker._run(
            apply_research=lambda task, result: applied.append((task, result)),
            max_tasks=1,
        )

        self.assertEqual(applied, [])
        self.assertEqual(updates[0][1]['status'], 'REVIEW_REQUIRED')
        self.assertTrue(updates[0][1]['error'].startswith('RESEARCH_SCHEMA_INVALID:'))


if __name__ == '__main__':
    unittest.main()
