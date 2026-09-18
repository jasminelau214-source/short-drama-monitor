import unittest

from research_guard import assess_research_task


def run(run_id, updated_at, titles, *, target='daily_top_all', source='SHORT_DRAMA_APP'):
    return {
        'id': run_id,
        'collection_date': '2026-09-17',
        'platform': 'MoboReels',
        'updated_at': updated_at,
        'result_json': {
            'batchComplete': True,
            'collector': {'sourceType': source, 'targetKey': target, 'topN': len(titles)},
            'rows': [{'rank': i + 1, 'title': title} for i, title in enumerate(titles)],
        },
    }


class ResearchGuardTests(unittest.TestCase):
    def test_dropped_morning_title_is_superseded(self):
        morning = run('morning', '2026-09-17T01:35:00Z', [
            'Married My Ex\'s Dad, Now I\'m Mafia Queen',
            'His Neglected Wife Is The Top Scientist',
        ])
        afternoon = run('afternoon', '2026-09-17T09:22:00Z', [
            'Married My Ex\'s Dad, Now I\'m Mafia Queen',
            'Weak Yesterday, Unstoppable Today',
        ])
        task = {
            'analysis_run_id': 'morning',
            'collection_date': '2026-09-17',
            'platform': 'MoboReels',
            'title': 'His Neglected Wife Is The Top Scientist',
        }
        result = assess_research_task(task, [morning, afternoon])
        self.assertFalse(result['current'])
        self.assertEqual(result['code'], 'SUPERSEDED_BY_LATER_RUN')
        self.assertEqual(result['latestRunId'], 'afternoon')

    def test_title_still_in_latest_run_is_current(self):
        morning = run('morning', '2026-09-17T01:35:00Z', ['A', 'B'])
        afternoon = run('afternoon', '2026-09-17T09:22:00Z', ['A', 'C'])
        task = {
            'analysis_run_id': 'morning',
            'collection_date': '2026-09-17',
            'platform': 'MoboReels',
            'title': 'A',
        }
        result = assess_research_task(task, [morning, afternoon])
        self.assertTrue(result['current'])
        self.assertEqual(result['latestRunId'], 'afternoon')

    def test_different_target_does_not_supersede(self):
        primary = run('primary', '2026-09-17T01:00:00Z', ['A'], target='daily_top_all')
        category = run('category', '2026-09-17T10:00:00Z', ['B'], target='male_trending')
        task = {
            'analysis_run_id': 'primary',
            'collection_date': '2026-09-17',
            'platform': 'MoboReels',
            'title': 'A',
        }
        result = assess_research_task(task, [primary, category])
        self.assertTrue(result['current'])
        self.assertEqual(result['latestRunId'], 'primary')

    def test_official_web_task_is_rejected(self):
        web = run('web', '2026-09-17T01:00:00Z', ['A'], source='OFFICIAL_WEB')
        task = {
            'analysis_run_id': 'web',
            'collection_date': '2026-09-17',
            'platform': 'MoboReels',
            'title': 'A',
        }
        result = assess_research_task(task, [web])
        self.assertFalse(result['current'])
        self.assertEqual(result['code'], 'INVALID_RESEARCH_SOURCE_TYPE')


if __name__ == '__main__':
    unittest.main()
