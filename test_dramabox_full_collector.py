import json
import unittest

from dramabox_full_collector import collect_dramabox_channel_all_pages
from official_web_collectors import OfficialWebCollectorError


def make_page(page_no, pages, names):
    items = []
    for i, name in enumerate(names, start=1):
        book_id = f'{page_no}{i:03d}'
        items.append({
            'bookId': book_id,
            'bookName': name,
            'bookNameLower': name.lower().replace(' ', '-'),
            'introduction': f'Synopsis {name}',
            'typeOneNames': ['F-Drama'],
            'typeTwoNames': ['Romance'],
            'tags': ['Test'],
            'viewCount': 1000 + i,
            'viewCountDisplay': f'{i}K',
            'ratings': 8.0,
            'chapterCount': 50 + i,
        })
    next_data = {
        'props': {
            'pageProps': {
                'moreData': {'name': '当前热播', 'items': items},
                'pageNo': page_no,
                'pages': pages,
                'locale': 'en',
            }
        },
        'buildId': 'test-build',
    }
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(next_data) + '</script>'


class DramaBoxFullCollectorTests(unittest.TestCase):
    def test_combines_pages_into_global_positions(self):
        documents = {
            1: make_page(1, 2, ['A', 'B']),
            2: make_page(2, 2, ['C']),
        }
        result = collect_dramabox_channel_all_pages(
            channel='trending',
            collection_date='2026-09-16',
            documents=documents,
        )
        self.assertEqual(result['top_n'], 3)
        self.assertEqual([x['rank'] for x in result['rows']], [1, 2, 3])
        self.assertEqual([x['title'] for x in result['rows']], ['A', 'B', 'C'])
        self.assertEqual([x['source_page'] for x in result['rows']], [1, 1, 2])
        self.assertEqual(result['evidence']['pages'], 2)
        self.assertEqual(result['evidence']['page_item_counts'], [2, 1])
        self.assertEqual(result['collector_version'], 'dramabox-nextdata-full-v1')

    def test_duplicate_across_pages_fails_closed(self):
        documents = {
            1: make_page(1, 2, ['A']),
            2: make_page(2, 2, ['A']),
        }
        with self.assertRaises(OfficialWebCollectorError):
            collect_dramabox_channel_all_pages(
                channel='trending',
                collection_date='2026-09-16',
                documents=documents,
            )

    def test_missing_page_document_fails_closed(self):
        documents = {1: make_page(1, 2, ['A'])}
        with self.assertRaises(OfficialWebCollectorError):
            collect_dramabox_channel_all_pages(
                channel='trending',
                collection_date='2026-09-16',
                documents=documents,
            )


if __name__ == '__main__':
    unittest.main()
