from __future__ import annotations

import json
import pathlib
import sqlite3
import tempfile
import unittest

from collector_import import validate_and_normalize
from drama_identity import normalize_title
from live_observations_v2 import build_live_summary, merge_analysis_records


ROOT = pathlib.Path(__file__).resolve().parent


class GoldenPublicationE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(
            (ROOT / 'tests' / 'fixtures' / 'golden_real_app_20260917.json').read_text(encoding='utf-8')
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / 'golden.sqlite3'
        with self.connect() as conn:
            conn.execute(
                '''create table analysis_runs(
                    id text primary key,
                    collection_date text not null,
                    platform text not null,
                    status text not null,
                    result_json text not null,
                    updated_at text not null
                )'''
            )
            conn.commit()

    def tearDown(self):
        self.temp.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def split_lane(value):
        return [x.strip() for x in str(value or '').replace('/', '\n').splitlines() if x.strip()]

    @staticmethod
    def clean(value):
        return str(value or '').strip()

    def normalized_run(self, platform):
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

    def insert_run(self, run, updated_at):
        with self.connect() as conn:
            conn.execute(
                'insert into analysis_runs values(?,?,?,?,?,?)',
                (
                    run['runId'],
                    run['collectionDate'],
                    run['platform'],
                    run['status'],
                    json.dumps(run['result'], ensure_ascii=False),
                    updated_at,
                ),
            )
            conn.commit()

    def test_real_app_runs_publish_twenty_ranking_rows(self):
        mobo = self.normalized_run('MoboReels')
        net = self.normalized_run('NetShort')
        self.insert_run(mobo, '2026-09-17T09:22:39+00:00')
        self.insert_run(net, '2026-09-17T09:22:43+00:00')

        records = merge_analysis_records(
            [],
            self.connect,
            normalize_title,
            self.split_lane,
        )
        self.assertEqual(len(records), 20)
        self.assertEqual(sum(r['app'] == 'MoboReels' for r in records), 10)
        self.assertEqual(sum(r['app'] == 'NetShort' for r in records), 10)
        self.assertTrue(all(r['sourceType'] == 'SHORT_DRAMA_APP' for r in records))

        mobo_rows = sorted(
            [r for r in records if r['app'] == 'MoboReels'],
            key=lambda r: r['rank'],
        )
        self.assertEqual(mobo_rows[0]['title'], "Married My Ex's Dad, Now I'm Mafia Queen")
        self.assertEqual(mobo_rows[2]['title'], 'Left at the Altar, Married Power')
        self.assertEqual(mobo_rows[2]['newness'], 'new')
        self.assertEqual(mobo_rows[4]['title'], "The Trophy Wife's War")
        self.assertEqual(mobo_rows[4]['newness'], 'old')

    def test_official_web_run_cannot_enter_app_publication_layer(self):
        mobo = self.normalized_run('MoboReels')
        self.insert_run(mobo, '2026-09-17T09:22:39+00:00')

        web_result = {
            'batchComplete': True,
            'collector': {
                'sourceType': 'OFFICIAL_WEB',
                'targetKey': 'web_test',
                'rankingType': 'Web Trending',
                'topN': 1,
            },
            'rows': [{
                'rank': 1,
                'title': 'WEB GHOST MUST NOT PUBLISH',
                'newness': 'observed',
                'pendingChecks': [],
            }],
        }
        with self.connect() as conn:
            conn.execute(
                'insert into analysis_runs values(?,?,?,?,?,?)',
                (
                    'web-ghost',
                    '2026-09-17',
                    'MoboReels',
                    '已采集',
                    json.dumps(web_result, ensure_ascii=False),
                    '2026-09-17T10:00:00+00:00',
                ),
            )
            conn.commit()

        records = merge_analysis_records(
            [],
            self.connect,
            normalize_title,
            self.split_lane,
        )
        titles = {r['title'] for r in records}
        self.assertNotIn('WEB GHOST MUST NOT PUBLISH', titles)
        self.assertEqual(len(records), 10)

    def test_summary_keeps_real_app_platform_counts(self):
        for platform, ts in (
            ('MoboReels', '2026-09-17T09:22:39+00:00'),
            ('NetShort', '2026-09-17T09:22:43+00:00'),
        ):
            self.insert_run(self.normalized_run(platform), ts)

        records = merge_analysis_records(
            [],
            self.connect,
            normalize_title,
            self.split_lane,
        )
        summary = build_live_summary(
            records,
            ['MoboReels', 'NetShort'],
            self.clean,
            self.split_lane,
        )
        self.assertEqual(summary['collectionDate'], '2026-09-17')
        self.assertEqual(summary['totalRows'], 20)
        counts = {x['name']: x['value'] for x in summary['platforms']}
        self.assertEqual(counts, {'MoboReels': 10, 'NetShort': 10})


if __name__ == '__main__':
    unittest.main()
