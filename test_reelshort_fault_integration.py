from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone

from collector_import import CollectorImportError, validate_and_normalize
from official_web_collectors import OfficialWebCollectorError
from reelshort_collector import collect_reelshort_top


URL = 'https://www.reelshort.com/shelf/top-short-movies-dramas-51001122'


def fixture_html(shelf_name='TOP', count=12):
    value = {
        'props': {
            'pageProps': {
                'shelfName': shelf_name,
                'total': 200,
                'list': [
                    {'book_title': f'Title {i}'}
                    for i in range(1, count + 1)
                ],
            }
        },
        'buildId': 'fixture-build',
    }
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(value)
        + '</script></body></html>'
    )


def payload(doc=None):
    result = collect_reelshort_top(
        url=URL,
        collection_date='2026-09-20',
        top_n=10,
        document=doc or fixture_html(),
    )
    result['evidence'].update({
        'requestedUrl': URL,
        'httpStatus': 200,
        'pageUrl': URL,
        'fetchedAt': datetime.now(timezone.utc).isoformat(),
    })
    return result


class ReelShortFaultIntegrationTests(unittest.TestCase):
    def test_top10_crosses_import_boundary(self):
        result = validate_and_normalize(payload(), set())
        self.assertEqual(result['platform'], 'ReelShort')
        self.assertEqual(result['rowCount'], 10)
        self.assertEqual(result['topN'], 10)
        self.assertEqual(result['status'], '已采集')
        self.assertEqual(result['newTitleCount'], 0)

    def test_shelf_name_must_be_top(self):
        with self.assertRaisesRegex(OfficialWebCollectorError, 'REELSHORT_SHELF_MISMATCH'):
            payload(fixture_html('Control Shelf'))

    def test_incomplete_top10_fails_closed(self):
        with self.assertRaisesRegex(OfficialWebCollectorError, 'REELSHORT_INCOMPLETE_TOP'):
            payload(fixture_html(count=9))

    def test_parseable_http_503_fails_closed(self):
        item = payload()
        item['evidence']['httpStatus'] = 503
        with self.assertRaisesRegex(CollectorImportError, 'HTTP_STATUS_INVALID'):
            validate_and_normalize(item, set())

    def test_cross_host_redirect_fails_closed(self):
        item = payload()
        item['evidence']['pageUrl'] = 'https://example.invalid/control'
        with self.assertRaisesRegex(CollectorImportError, 'OFFICIAL_HOST_MISMATCH'):
            validate_and_normalize(item, set())

    def test_stale_replay_fails_closed(self):
        item = payload()
        item['evidence']['fetchedAt'] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        with self.assertRaisesRegex(CollectorImportError, 'FETCH_EVIDENCE_STALE'):
            validate_and_normalize(item, set())

    def test_missing_row_fails_closed_at_import_boundary(self):
        item = payload()
        item['rows'] = copy.deepcopy(item['rows'][:-1])
        with self.assertRaisesRegex(CollectorImportError, 'Top10|10'):
            validate_and_normalize(item, set())

    def test_duplicate_title_fails_closed_at_import_boundary(self):
        item = payload()
        item['rows'][1]['title'] = item['rows'][0]['title']
        with self.assertRaisesRegex(CollectorImportError, '重复标题'):
            validate_and_normalize(item, set())


if __name__ == '__main__':
    unittest.main()
