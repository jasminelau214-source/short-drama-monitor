from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from web_promotion_gate import audit_official_web_payload, web_promotion_error


class WebPromotionGateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 20, 8, 30, tzinfo=timezone.utc)
        self.payload = {
            'platform': 'GoodShort',
            'source_type': 'OFFICIAL_WEB',
            'source_id': 'officialweb_goodshort',
            'target_key': 'web_top_goodshort_pilot',
            'ranking_type': 'Top in GoodShort',
            'collection_method': 'WEB_SCRAPE',
            'collection_date': '2026-09-20',
            'top_n': 10,
            'batch_complete': True,
            'evidence': {
                'requestedUrl': 'https://www.goodshort.com/channel/Top-in-GoodShort',
                'httpStatus': 200,
                'pageUrl': 'https://www.goodshort.com/channel/Top-in-GoodShort',
                'fetchedAt': self.now.isoformat(),
                'semanticVerified': True,
            },
            'rows': [{'rank': i, 'title': f'Drama {i}'} for i in range(1, 11)],
        }

    def test_valid_current_official_source_passes(self):
        result = audit_official_web_payload(self.payload, now=self.now)
        self.assertTrue(result['pass'])
        self.assertEqual(web_promotion_error(self.payload, now=self.now), '')

    def test_http_503_fails(self):
        self.payload['evidence']['httpStatus'] = 503
        result = audit_official_web_payload(self.payload, now=self.now)
        self.assertFalse(result['pass'])
        self.assertIn('HTTP_STATUS_INVALID', result['errors'])

    def test_cross_host_redirect_fails(self):
        self.payload['evidence']['pageUrl'] = 'https://example.invalid/control'
        result = audit_official_web_payload(self.payload, now=self.now)
        self.assertFalse(result['pass'])
        self.assertIn('OFFICIAL_HOST_MISMATCH', result['errors'])

    def test_stale_replay_fails(self):
        self.payload['evidence']['fetchedAt'] = (
            self.now - timedelta(hours=1)
        ).isoformat()
        result = audit_official_web_payload(self.payload, now=self.now)
        self.assertFalse(result['pass'])
        self.assertIn('FETCH_EVIDENCE_STALE', result['errors'])

    def test_target_semantic_replacement_fails(self):
        self.payload['evidence']['semanticVerified'] = False
        result = audit_official_web_payload(self.payload, now=self.now)
        self.assertFalse(result['pass'])
        self.assertIn('TARGET_SEMANTIC_UNVERIFIED', result['errors'])

    def test_missing_final_url_fails(self):
        self.payload['evidence']['pageUrl'] = ''
        result = audit_official_web_payload(self.payload, now=self.now)
        self.assertFalse(result['pass'])
        self.assertIn('OFFICIAL_HOST_MISMATCH', result['errors'])

    def test_multi_page_requires_every_page_to_pass(self):
        base = dict(self.payload['evidence'])
        self.payload['evidence']['pageFetchEvidence'] = [
            dict(base),
            {
                **base,
                'requestedUrl': 'https://www.goodshort.com/channel/Top-in-GoodShort/2',
                'pageUrl': 'https://example.invalid/control',
            },
        ]
        result = audit_official_web_payload(self.payload, now=self.now)
        self.assertFalse(result['pass'])
        self.assertIn('PAGE_2:OFFICIAL_HOST_MISMATCH', result['errors'])


if __name__ == '__main__':
    unittest.main()
