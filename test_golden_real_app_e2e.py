from __future__ import annotations

import json
import pathlib
import unittest

from collector_import import validate_and_normalize
from research_guard import assess_research_task
from research_pipeline import CORE_FIELDS, OPTIONAL_FIELDS, classify_research_status
from research_validation import validate_research_payload
from research_writeback import select_same_platform_record


ROOT = pathlib.Path(__file__).resolve().parent


def valid_research(title: str) -> dict:
    payload = {field: 'verified' for field in CORE_FIELDS}
    payload['genre'] = '现代都市'
    payload['audience'] = '泛受众'
    payload.update({field: '' for field in OPTIONAL_FIELDS})
    payload.update({
        'canonicalTitle': title,
        'newnessResolution': 'new',
        'confidence': 'medium',
        'missingFields': [],
        'auditNotes': [],
        'sourceUrls': ['https://example.com/evidence'],
        'needsGPT': False,
    })
    return payload


class GoldenRealAppE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(
            (ROOT / 'tests' / 'fixtures' / 'golden_real_app_20260917.json').read_text(encoding='utf-8')
        )

    def normalize_platform(self, platform: str) -> dict:
        item = self.fixture['platforms'][platform]
        payload = {
            'platform': platform,
            'source_type': item['sourceType'],
            'source_id': item['sourceId'],
            'target_key': item['targetKey'],
            'ranking_type': item['rankingType'],
            'category': 'All',
            'collection_method': item['collectionMethod'],
            'collection_date': self.fixture['collectionDate'],
            'top_n': item['topN'],
            'batch_complete': True,
            'rows': item['rows'],
            'provider': 'golden-real-app-fixture',
            'collector_version': 'golden-fixture-v1',
            'collected_at': '2026-09-17T09:22:00',
            'rank_conflicts': [],
            'evidence': {
                'fixture': 'golden_real_app_20260917',
                'originalSourceType': item['sourceType'],
                'semanticVerified': True,
                'appFocusVerified': True,
                **(
                    {'ui_xml': 'fixture://golden/netshort.xml'}
                    if item['collectionMethod'] == 'APP_UI_XML'
                    else {'ui_xml_pages': ['fixture://golden/moboreels-page1.xml']}
                ),
            },
        }
        return validate_and_normalize(payload, item['priorAppTitles'])

    def test_real_app_source_semantics_are_preserved(self):
        for platform in ('MoboReels', 'NetShort'):
            run = self.normalize_platform(platform)
            collector = run['result']['collector']
            self.assertEqual(collector['sourceType'], 'SHORT_DRAMA_APP')
            self.assertIn(collector['collectionMethod'], {'APP_UI_XML', 'APP_UI_XML_SCROLL'})
            self.assertEqual(run['rowCount'], 10)

    def test_real_newness_matches_locked_business_contract(self):
        expected_counts = {
            'MoboReels': (9, 1),
            'NetShort': (0, 10),
        }
        for platform, (new_count, old_count) in expected_counts.items():
            run = self.normalize_platform(platform)
            rows = run['result']['rows']
            new_titles = [row['title'] for row in rows if row['newness'] == 'new']
            old_titles = [row['title'] for row in rows if row['newness'] == 'old']
            self.assertEqual(len(new_titles), new_count)
            self.assertEqual(len(old_titles), old_count)
            self.assertEqual(
                set(new_titles),
                set(self.fixture['platforms'][platform]['expectedNewTitles']),
            )
            self.assertEqual(
                set(old_titles),
                set(self.fixture['platforms'][platform]['expectedOldTitles']),
            )

    def test_key_newness_counterexamples(self):
        mobo = self.normalize_platform('MoboReels')
        by_title = {row['title']: row for row in mobo['result']['rows']}
        self.assertEqual(by_title['Left at the Altar, Married Power']['newness'], 'new')
        self.assertEqual(by_title["The Trophy Wife's War"]['newness'], 'old')

    def test_exact_replay_has_same_run_id(self):
        for platform in ('MoboReels', 'NetShort'):
            first = self.normalize_platform(platform)
            second = self.normalize_platform(platform)
            self.assertEqual(first['runId'], second['runId'])

    def test_only_real_app_new_titles_enter_research_eligibility(self):
        mobo = self.normalize_platform('MoboReels')
        app_run = {
            'id': mobo['runId'],
            'collection_date': mobo['collectionDate'],
            'platform': mobo['platform'],
            'updated_at': '2026-09-17T09:22:39+00:00',
            'result_json': mobo['result'],
        }
        tasks = [
            {
                'analysis_run_id': mobo['runId'],
                'collection_date': mobo['collectionDate'],
                'platform': mobo['platform'],
                'rank': row['rank'],
                'title': row['title'],
                'normalized_title': row['title'],
            }
            for row in mobo['result']['rows']
            if row['newness'] == 'new'
        ]
        self.assertEqual(len(tasks), 9)
        for task in tasks:
            decision = assess_research_task(task, [app_run])
            self.assertTrue(decision['current'], task['title'])
            self.assertEqual(decision['sourceType'], 'SHORT_DRAMA_APP')

        netshort = self.normalize_platform('NetShort')
        self.assertEqual(netshort['newTitleCount'], 0)

    def test_valid_research_can_cross_complete_gate_but_only_same_platform_writeback(self):
        mobo = self.normalize_platform('MoboReels')
        title = mobo['newTitles'][0]
        research = valid_research(title)

        validation = validate_research_payload(
            research,
            allowed_source_urls={'https://example.com/evidence'},
        )
        self.assertTrue(validation['ok'])

        status, missing = classify_research_status(
            requested_title=title,
            result=research,
            search_meta={'officialCount': 1, 'sanitizedSources': 0},
        )
        self.assertEqual(status, 'COMPLETE')
        self.assertEqual(missing, [])

        records = [
            {'id': 'wrong-platform', 'title': title, 'app': 'NetShort', 'history': []},
            {'id': 'correct-platform', 'title': title, 'app': 'MoboReels', 'history': []},
        ]
        target = select_same_platform_record(records, platform='MoboReels', title=title)
        self.assertIsNotNone(target)
        self.assertEqual(target['id'], 'correct-platform')

        blocked = select_same_platform_record(
            [{'id': 'wrong-only', 'title': title, 'app': 'NetShort', 'history': []}],
            platform='MoboReels',
            title=title,
        )
        self.assertIsNone(blocked)


if __name__ == '__main__':
    unittest.main()
