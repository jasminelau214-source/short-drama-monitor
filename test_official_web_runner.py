import json
import tempfile
import unittest
from pathlib import Path

from run_official_web_collect import audit_payload, write_payload


class OfficialWebRunnerTests(unittest.TestCase):
    def payload(self):
        return {
            'platform': 'ShortMax',
            'source_type': 'OFFICIAL_WEB',
            'source_id': 'officialweb_shortmax',
            'target_key': 'web_most_popular_all',
            'ranking_type': 'Most Popular',
            'collection_method': 'WEB_SCRAPE',
            'collection_date': '2026-09-16',
            'top_n': 2,
            'batch_complete': True,
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

    def test_write_payload_uses_shared_date_platform_spool(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_payload(Path(tmp), self.payload())
            self.assertEqual(
                path,
                Path(tmp) / '2026-09-16' / 'ShortMax' / 'web_most_popular_all.json',
            )
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(saved['target_key'], 'web_most_popular_all')
            self.assertEqual(len(saved['rows']), 2)


if __name__ == '__main__':
    unittest.main()
