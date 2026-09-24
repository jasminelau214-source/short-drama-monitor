from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone

from collector_import import CollectorImportError, validate_and_normalize
from moboreels_web_collector import collect_moboreels_popular, parse_moboreels_popular
from official_web_collectors import OfficialWebCollectorError


URL = 'https://www.moboreels.com/'


def fixture_html(label='Popular Series', count=12):
    cards = ''.join(
        f'<div class="home-list-item"><h3 class="home-list-item-title">Title {i}</h3></div>'
        for i in range(1, count + 1)
    )
    return (
        '<html><body>'
        '<div class="home-list">'
        f'<h2 class="home-list-title">{label}</h2>'
        f'{cards}'
        '</div>'
        '<div class="home-list"><h2 class="home-list-title">Other</h2></div>'
        '</body></html>'
    )


def payload(doc=None):
    result = collect_moboreels_popular(
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


class MoboReelsWebFaultIntegrationTests(unittest.TestCase):
    def test_parser_is_section_scoped(self):
        rows = parse_moboreels_popular(fixture_html(), top_n=10)
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]['title'], 'Title 1')
        self.assertEqual(rows[-1]['title'], 'Title 10')

    def test_top10_crosses_import_boundary(self):
        result = validate_and_normalize(payload(), set())
        self.assertEqual(result['platform'], 'MoboReels')
        self.assertEqual(result['rowCount'], 10)
        self.assertEqual(result['topN'], 10)
        self.assertEqual(result['status'], '已采集')
        self.assertEqual(result['newTitleCount'], 0)

    def test_semantic_replacement_fails_closed(self):
        with self.assertRaisesRegex(OfficialWebCollectorError, 'MOBOREELS_POPULAR_SERIES_NOT_FOUND'):
            payload(fixture_html('Control Shelf'))

    def test_incomplete_top10_fails_closed(self):
        with self.assertRaisesRegex(OfficialWebCollectorError, 'MOBOREELS_INCOMPLETE_TOP'):
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
