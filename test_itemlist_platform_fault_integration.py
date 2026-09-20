from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone

from collector_import import CollectorImportError, validate_and_normalize
from itemlist_platform_collectors import (
    FLEXTV_URL,
    NETSHORT_URL,
    collect_flextv_top,
    collect_netshort_trending,
)
from official_web_collectors import OfficialWebCollectorError


def itemlist_entries(count=12):
    return [
        {
            '@type': 'ListItem',
            'position': i,
            'item': {
                '@type': 'TVSeries',
                'name': f'Title {i}',
                'url': f'https://example.com/title-{i}',
            },
        }
        for i in range(1, count + 1)
    ]


def jsonld_html(*, name=None, page_marker='', count=12):
    itemlist = {
        '@context': 'https://schema.org',
        '@type': 'ItemList',
        'itemListElement': itemlist_entries(count),
    }
    if name is not None:
        itemlist['name'] = name
    return (
        '<html><head>'
        f'<title>{page_marker}</title>'
        '<script type="application/ld+json">'
        + json.dumps(itemlist)
        + '</script></head><body>'
        f'<h1>{page_marker}</h1>'
        '</body></html>'
    )


def attach_transport(payload, url):
    payload['evidence'].update({
        'requestedUrl': url,
        'httpStatus': 200,
        'pageUrl': url,
        'fetchedAt': datetime.now(timezone.utc).isoformat(),
    })
    return payload


class ItemListPlatformFaultIntegrationTests(unittest.TestCase):
    def test_netshort_named_itemlist_crosses_import_boundary(self):
        payload = attach_transport(
            collect_netshort_trending(
                collection_date='2026-09-20',
                top_n=10,
                document=jsonld_html(name='Trending Now'),
            ),
            NETSHORT_URL,
        )
        result = validate_and_normalize(payload, set())
        self.assertEqual(result['platform'], 'NetShort')
        self.assertEqual(result['rowCount'], 10)
        self.assertEqual(result['result']['collector']['evidence']['semanticMethod'], 'itemListName')

    def test_flextv_unnamed_single_itemlist_requires_page_marker(self):
        payload = attach_transport(
            collect_flextv_top(
                collection_date='2026-09-20',
                top_n=10,
                document=jsonld_html(name='', page_marker='Top in FlexTV Short Dramas Online'),
            ),
            FLEXTV_URL,
        )
        result = validate_and_normalize(payload, set())
        self.assertEqual(result['platform'], 'FlexTV')
        self.assertEqual(result['rowCount'], 10)
        self.assertEqual(
            result['result']['collector']['evidence']['semanticMethod'],
            'pageTitleOrHeading+singleItemList',
        )

    def test_netshort_wrong_itemlist_name_fails_closed(self):
        with self.assertRaisesRegex(OfficialWebCollectorError, 'TARGET_ITEMLIST_NOT_IDENTIFIED'):
            collect_netshort_trending(
                collection_date='2026-09-20',
                top_n=10,
                document=jsonld_html(name='Control Shelf'),
            )

    def test_flextv_without_page_marker_fails_closed(self):
        with self.assertRaisesRegex(OfficialWebCollectorError, 'TARGET_ITEMLIST_NOT_IDENTIFIED'):
            collect_flextv_top(
                collection_date='2026-09-20',
                top_n=10,
                document=jsonld_html(name='', page_marker='Control Shelf'),
            )

    def test_incomplete_itemlist_fails_closed(self):
        with self.assertRaisesRegex(OfficialWebCollectorError, 'ITEMLIST_INCOMPLETE_TOP'):
            collect_netshort_trending(
                collection_date='2026-09-20',
                top_n=10,
                document=jsonld_html(name='Trending Now', count=9),
            )

    def test_source_faults_fail_closed_for_both_platforms(self):
        cases = [
            (
                collect_netshort_trending,
                NETSHORT_URL,
                jsonld_html(name='Trending Now'),
            ),
            (
                collect_flextv_top,
                FLEXTV_URL,
                jsonld_html(name='', page_marker='Top in FlexTV'),
            ),
        ]
        for collector, url, doc in cases:
            payload = attach_transport(
                collector(collection_date='2026-09-20', top_n=10, document=doc),
                url,
            )

            http_503 = copy.deepcopy(payload)
            http_503['evidence']['httpStatus'] = 503
            with self.assertRaisesRegex(CollectorImportError, 'HTTP_STATUS_INVALID'):
                validate_and_normalize(http_503, set())

            redirect = copy.deepcopy(payload)
            redirect['evidence']['pageUrl'] = 'https://example.invalid/control'
            with self.assertRaisesRegex(CollectorImportError, 'OFFICIAL_HOST_MISMATCH'):
                validate_and_normalize(redirect, set())

            stale = copy.deepcopy(payload)
            stale['evidence']['fetchedAt'] = (
                datetime.now(timezone.utc) - timedelta(hours=1)
            ).isoformat()
            with self.assertRaisesRegex(CollectorImportError, 'FETCH_EVIDENCE_STALE'):
                validate_and_normalize(stale, set())

            missing = copy.deepcopy(payload)
            missing['rows'] = missing['rows'][:-1]
            with self.assertRaisesRegex(CollectorImportError, 'Top10|10'):
                validate_and_normalize(missing, set())

            duplicate = copy.deepcopy(payload)
            duplicate['rows'][1]['title'] = duplicate['rows'][0]['title']
            with self.assertRaisesRegex(CollectorImportError, '重复标题'):
                validate_and_normalize(duplicate, set())


if __name__ == '__main__':
    unittest.main()
