from __future__ import annotations

import unittest

from ranking_lifecycle import (
    app_newness,
    event_counts_as_app_ranking,
    known_app_ranked_titles,
)


class AppRankingLifecycleContractTests(unittest.TestCase):
    def test_modern_app_event_counts(self):
        self.assertTrue(event_counts_as_app_ranking({
            'date': '2026-09-17',
            'sourceType': 'SHORT_DRAMA_APP',
            'source': 'analysis_run:app',
        }))

    def test_official_web_event_does_not_consume_app_newness(self):
        self.assertFalse(event_counts_as_app_ranking({
            'date': '2026-09-16',
            'sourceType': 'OFFICIAL_WEB',
            'source': 'analysis_run:web',
        }))

    def test_legacy_reviewed_ranking_history_remains_valid_app_baseline(self):
        self.assertTrue(event_counts_as_app_ranking({
            'date': '2026-09-04',
            'app': 'MoboReels',
            'rank': 3,
            'heat': '',
            'tags': 'Revenge',
            'metrics': {},
        }))

    def test_unknown_newer_source_is_not_silently_treated_as_app(self):
        self.assertFalse(event_counts_as_app_ranking({
            'date': '2026-09-16',
            'source': 'some-new-pipeline:123',
        }))

    def test_web_only_discovery_does_not_make_first_app_ranking_old(self):
        records = [{
            'title': 'Left at the Altar, Married Power',
            'history': [{
                'date': '2026-09-16',
                'sourceType': 'OFFICIAL_WEB',
                'source': 'analysis_run:web-intake-moboreels',
                'rank': 2,
            }],
        }]
        prior = known_app_ranked_titles(records, '2026-09-17')
        self.assertNotIn('Left at the Altar, Married Power', prior)
        self.assertEqual(
            app_newness('Left at the Altar, Married Power', prior),
            'new',
        )

    def test_real_legacy_app_history_keeps_trophy_wife_old(self):
        records = [{
            'title': "The Trophy Wife's War",
            'history': [
                {
                    'date': '2026-09-04',
                    'app': 'MoboReels',
                    'rank': 3,
                    'metrics': {},
                },
                {
                    'date': '2026-09-16',
                    'sourceType': 'OFFICIAL_WEB',
                    'source': 'analysis_run:web-intake-moboreels',
                    'rank': 7,
                },
            ],
        }]
        prior = known_app_ranked_titles(records, '2026-09-17')
        self.assertIn("The Trophy Wife's War", prior)
        self.assertEqual(app_newness("The Trophy Wife's War", prior), 'old')

    def test_same_day_event_does_not_preconsume_first_ranked_status(self):
        records = [{
            'title': 'Brand New Drama',
            'history': [{
                'date': '2026-09-17',
                'sourceType': 'SHORT_DRAMA_APP',
                'source': 'analysis_run:current',
                'rank': 1,
            }],
        }]
        prior = known_app_ranked_titles(records, '2026-09-17')
        self.assertEqual(prior, [])
        self.assertEqual(app_newness('Brand New Drama', prior), 'new')


if __name__ == '__main__':
    unittest.main()
