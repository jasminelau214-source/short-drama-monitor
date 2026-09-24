import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import run_official_web_collect as runner
from collection_policy import ACTIVE, PAUSED, PLATFORM_POLICY, collection_scope_manifest
from run_official_web_collect import PAUSED_TARGETS, TARGETS, audit_payload, write_payload


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
            'locale': 'en-US',
            'region': 'US',
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

    def test_non_english_or_non_us_profile_is_rejected(self):
        payload = self.payload()
        payload['locale'] = 'es-US'
        with self.assertRaises(Exception):
            audit_payload(payload)

        payload = self.payload()
        payload['region'] = 'CA'
        with self.assertRaises(Exception):
            audit_payload(payload)

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

    def test_default_web_plan_contains_only_six_active_primary_targets(self):
        self.assertEqual(
            set(TARGETS),
            {
                'dramabox_trending',
                'goodshort_top',
                'reelshort_top',
                'moboreels_popular',
                'netshort_trending',
                'flextv_top',
            },
        )
        self.assertEqual(TARGETS['dramabox_trending']['target_key'], 'web_trending_top10')
        self.assertEqual(TARGETS['goodshort_top']['target_key'], 'web_top_goodshort_pilot')
        self.assertEqual(TARGETS['reelshort_top']['target_key'], 'web_top_shelf_all')
        self.assertEqual(TARGETS['moboreels_popular']['target_key'], 'web_popular_series_all')
        self.assertEqual(TARGETS['netshort_trending']['target_key'], 'web_trending_now_all')
        self.assertEqual(TARGETS['flextv_top']['target_key'], 'web_top_in_flextv_all')
        self.assertTrue(all(target['top_n'] == 10 for target in TARGETS.values()))

    def test_dramawave_and_shortmax_are_explicitly_paused(self):
        self.assertEqual(PLATFORM_POLICY['DramaWave']['state'], PAUSED)
        self.assertEqual(PLATFORM_POLICY['ShortMax']['state'], PAUSED)
        self.assertNotIn('DramaWave', {target['platform'] for target in TARGETS.values()})
        self.assertNotIn('ShortMax', {target['platform'] for target in TARGETS.values()})
        self.assertEqual(
            set(PAUSED_TARGETS),
            {
                'shortmax_most_popular',
                'shortmax_war_god',
                'shortmax_tycoon_life',
                'shortmax_apocalypse',
                'shortmax_dragon_clan',
            },
        )

    def test_scope_manifest_reports_paused_platforms_without_failures(self):
        scope = collection_scope_manifest()
        self.assertEqual(scope['policyVersion'], 'core-contract-v1-2026-09-23')
        self.assertEqual(scope['profile']['locale'], 'en-US')
        self.assertEqual(scope['profile']['region'], 'US')
        self.assertEqual(
            {item['platform'] for item in scope['pausedPlatforms']},
            {'DramaWave', 'ShortMax'},
        )
        self.assertTrue(all(item['status'] == PAUSED for item in scope['pausedPlatforms']))
        self.assertTrue(
            all(PLATFORM_POLICY[platform]['state'] == ACTIVE for platform in scope['activePlatforms'])
        )

    def test_dramabox_default_target_is_top10_not_full_channel(self):
        captured = {}
        original = runner.collect_dramabox_channel

        def fake_collect_dramabox_channel(**kwargs):
            captured.update(kwargs)
            return {
                'target_key': 'web_trending_all',
                'collector_version': 'test',
            }

        runner.collect_dramabox_channel = fake_collect_dramabox_channel
        try:
            payload = runner.collect_dramabox_trending_top10('2026-09-23')
        finally:
            runner.collect_dramabox_channel = original

        self.assertEqual(captured['channel'], 'trending')
        self.assertEqual(captured['top_n'], 10)
        self.assertEqual(payload['target_key'], 'web_trending_top10')
        self.assertEqual(payload['collector_version'], 'dramabox-nextdata-top10-v1')


if __name__ == '__main__':
    unittest.main()
