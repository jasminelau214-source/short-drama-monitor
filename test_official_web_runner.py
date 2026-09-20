import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from run_official_web_collect import TARGETS, audit_payload, write_payload


class OfficialWebRunnerTests(unittest.TestCase):
    def payload(self):
        return {
            'platform': 'ShortMax',
            'source_type': 'OFFICIAL_WEB',
            'source_id': 'officialweb_shortmax',
            'target_key': 'web_most_popular_all',
            'ranking_type': 'Most Popular',
            'collection_method': 'WEB_SCRAPE',
            'collection_date': '2026-09-20',
            'top_n': 2,
            'batch_complete': True,
            'collector_version': 'test-web-v1',
            'collected_at': datetime.now(timezone.utc).isoformat(),
            'evidence': {
                'requestedUrl': 'https://www.shorttv.live/',
                'url': 'https://www.shorttv.live/',
                'httpStatus': 200,
                'pageUrl': 'https://www.shorttv.live/',
                'fetchedAt': datetime.now(timezone.utc).isoformat(),
                'semanticVerified': True,
            },
            'rows': [
                {'rank': 1, 'title': 'Alpha'},
                {'rank': 2, 'title': 'Beta'},
            ],
        }

    def test_audit_accepts_complete_official_web_payload(self):
        result = audit_payload(self.payload())
        self.assertEqual(result['topN'], 2)
        self.assertEqual(result['rowCount'], 2)
        self.assertEqual(result['status'], '已采集')
        self.assertEqual(result['newTitleCount'], 0)

    def test_stale_live_web_payload_is_rejected(self):
        payload = self.payload()
        payload['evidence']['fetchedAt'] = '2026-09-20T00:00:00+00:00'
        with self.assertRaises(Exception):
            audit_payload(payload)

    def test_write_payload_uses_shared_date_platform_spool(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_payload(Path(tmp), self.payload())
            self.assertEqual(
                path,
                Path(tmp) / '2026-09-20' / 'ShortMax' / 'web_most_popular_all.json',
            )
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(saved['target_key'], 'web_most_popular_all')
            self.assertEqual(len(saved['rows']), 2)

    def test_default_web_plan_contains_verified_targets(self):
        self.assertEqual(len(TARGETS), 8)
        self.assertEqual(TARGETS['shortmax_most_popular']['target_key'], 'web_most_popular_all')
        self.assertEqual(TARGETS['shortmax_war_god']['target_key'], 'web_category_war_god')
        self.assertEqual(TARGETS['shortmax_tycoon_life']['target_key'], 'web_category_tycoon_life')
        self.assertEqual(TARGETS['shortmax_apocalypse']['target_key'], 'web_category_apocalypse')
        self.assertEqual(TARGETS['shortmax_dragon_clan']['target_key'], 'web_category_dragon_clan')
        self.assertEqual(TARGETS['dramabox_trending']['target_key'], 'web_trending_all')
        self.assertEqual(TARGETS['goodshort_top']['target_key'], 'web_top_goodshort_pilot')
        self.assertEqual(TARGETS['reelshort_top']['target_key'], 'web_top_shelf_all')


if __name__ == '__main__':
    unittest.main()
