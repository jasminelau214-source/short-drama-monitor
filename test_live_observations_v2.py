import hashlib
import json
import sqlite3
import unittest

from live_observations import merge_analysis_records as merge_live_records
from live_observations_v2 import build_live_summary, merge_analysis_records


def normalize_title(value):
    import re
    return re.sub(r'[^a-z0-9]+', '', str(value or '').casefold())


def split_lane(value):
    return [x.strip() for x in str(value or '').replace('/', '\n').splitlines() if x.strip()]


def clean(value, limit=6000):
    return str(value or '').strip()[:limit]


class LiveObservationsV2Tests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('''create table analysis_runs(
            id text primary key, collection_date text, platform text, status text,
            result_json text, updated_at text
        )''')
        self.db.execute('''create table drama_overrides(
            drama_id text primary key, fields_json text not null, updated_at text not null
        )''')

    def connect(self):
        return self.db

    def add_run(self, run_id, platform, top_n=20, source_type='SHORT_DRAMA_APP', target='daily_top_all'):
        rows = [
            {'rank': i, 'title': f'{platform} Title {i}', 'tags': ['Fantasy'], 'metrics': {'views': f'{i}K'}}
            for i in range(1, top_n + 1)
        ]
        result = {
            'batchComplete': True,
            'rows': rows,
            'collector': {
                'sourceType': source_type,
                'targetKey': target,
                'rankingType': 'Trending',
                'topN': top_n,
            },
        }
        self.db.execute(
            'insert into analysis_runs values(?,?,?,?,?,?)',
            (run_id, '2026-09-16', platform, '已分析', json.dumps(result), '2026-09-16T10:00:00Z'),
        )
        self.db.commit()

    def test_top20_app_run_is_published(self):
        self.add_run('r1', 'DramaBox', top_n=20)
        records = merge_analysis_records([], self.connect, normalize_title, split_lane)
        self.assertEqual(len(records), 20)
        self.assertEqual(max(r['rank'] for r in records), 20)
        self.assertEqual(records[0]['topN'], 20)
        self.assertEqual(records[0]['platformMetrics']['views'], '1K')

    def test_official_web_is_not_mixed_into_app_dashboard(self):
        self.add_run('r1', 'ShortMax', top_n=8, source_type='OFFICIAL_WEB', target='web_most_popular_all')
        records = merge_analysis_records([], self.connect, normalize_title, split_lane)
        self.assertEqual(records, [])

    def test_targets_are_isolated(self):
        self.add_run('r1', 'DramaBox', top_n=3, target='daily_top_all')
        rows = [
            {'rank': i, 'title': f'DramaBox Title {i}', 'tags': []}
            for i in range(1, 4)
        ]
        result = {
            'batchComplete': True,
            'rows': rows,
            'collector': {
                'sourceType': 'SHORT_DRAMA_APP',
                'targetKey': 'male_trending',
                'rankingType': 'Male Trending',
                'topN': 3,
            },
        }
        self.db.execute(
            'insert into analysis_runs values(?,?,?,?,?,?)',
            ('r2', '2026-09-16', 'DramaBox', '已分析', json.dumps(result), '2026-09-16T10:01:00Z'),
        )
        self.db.commit()
        records = merge_analysis_records([], self.connect, normalize_title, split_lane)
        self.assertEqual(len(records), 6)
        self.assertEqual({r['targetKey'] for r in records}, {'daily_top_all', 'male_trending'})

    def test_summary_adds_unlisted_platform_dynamically(self):
        self.add_run('r1', 'GoodShort', top_n=3)
        records = merge_analysis_records([], self.connect, normalize_title, split_lane)
        summary = build_live_summary(records, ['NetShort', 'MoboReels'], clean, split_lane)
        self.assertEqual(summary['platformCount'], 1)
        self.assertEqual(summary['platforms'][0]['name'], 'GoodShort')
        self.assertEqual(summary['targetCount'], 1)
        self.assertEqual(summary['targets'][0]['rows'], 3)

    def test_legacy_research_override_survives_v2_id_change(self):
        platform = 'NetShort'
        title = "The Quarterback's Comeback"
        result = {
            'batchComplete': True,
            'rows': [{
                'rank': 1,
                'title': title,
                'tags': ['Rebirth'],
                'newness': 'new',
                'pendingChecks': ['待深度研究'],
            }],
            'collector': {
                'sourceType': 'SHORT_DRAMA_APP',
                'targetKey': 'daily_top_all',
                'rankingType': 'Top Trending',
                'topN': 1,
            },
        }
        self.db.execute(
            'insert into analysis_runs values(?,?,?,?,?,?)',
            ('legacy-run', '2026-09-15', platform, '已分析', json.dumps(result), '2026-09-15T10:00:00Z'),
        )
        norm = normalize_title(title)
        legacy_id = f"auto-netshort-{hashlib.sha1(norm.encode('utf-8')).hexdigest()[:12]}"
        legacy_fields = {
            'researchStatus': '已研究',
            'genre': '校园青春',
            'audience': '男频',
            'synopsis': 'legacy research restored',
            'lane': '逆袭',
        }
        self.db.execute(
            'insert into drama_overrides values(?,?,?)',
            (legacy_id, json.dumps(legacy_fields, ensure_ascii=False), '2026-09-15T11:00:00Z'),
        )
        self.db.commit()

        records = merge_live_records([], self.connect, normalize_title, split_lane)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertNotEqual(record['id'], legacy_id)
        self.assertEqual(record['researchStatus'], '已研究')
        self.assertEqual(record['genre'], '校园青春')
        self.assertEqual(record['audience'], '男频')
        self.assertEqual(record['synopsis'], 'legacy research restored')
        self.assertEqual(record['laneTerms'], ['逆袭'])


if __name__ == '__main__':
    unittest.main()
