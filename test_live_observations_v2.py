import json
import sqlite3
import unittest

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


if __name__ == '__main__':
    unittest.main()
