import unittest

from goodshort_collector import collect_goodshort_top, parse_goodshort_channel
from official_web_collectors import OfficialWebCollectorError


FIXTURE = '''
<html><body>
<div class="book">
  <a class="cover" href="/book/alpha"><div class="cover-mask"><span>EP 83</span></div></a>
  <a class="book-name" href="/book/alpha">Alpha Lady</a>
  <div class="book-tags"><span class="book-tag-item">Strong Female Lead</span><span class="book-tag-item">CEO</span></div>
  <div class="intro">Alpha synopsis.</div>
  <div class="like"><i></i><span>25.8M</span></div>
</div>
<div class="book">
  <a class="cover" href="/book/beta"><div class="cover-mask"><span>EP 72</span></div></a>
  <a class="book-name" href="/book/beta">Beta Love</a>
  <span class="book-tag-item">Marriage</span>
  <div class="intro">Beta synopsis.</div>
  <div class="like"><span>8.9M</span></div>
</div>
</body></html>
'''


class GoodShortCollectorTests(unittest.TestCase):
    def test_parser_extracts_ordered_native_fields(self):
        items = parse_goodshort_channel(FIXTURE, 'https://www.goodshort.com/channel/Top-in-GoodShort')
        self.assertEqual([x['title'] for x in items], ['Alpha Lady', 'Beta Love'])
        self.assertEqual(items[0]['episode_count'], '83')
        self.assertEqual(items[0]['metric'], '25.8M')
        self.assertEqual(items[0]['tags'], ['Strong Female Lead', 'CEO'])
        self.assertEqual(items[0]['url'], 'https://www.goodshort.com/book/alpha')
        self.assertEqual(items[0]['synopsis'], 'Alpha synopsis.')

    def test_collect_goodshort_builds_generic_payload(self):
        payload = collect_goodshort_top(
            collection_date='2026-09-16',
            top_n=2,
            document=FIXTURE,
        )
        self.assertEqual(payload['platform'], 'GoodShort')
        self.assertEqual(payload['source_type'], 'OFFICIAL_WEB')
        self.assertEqual(payload['source_id'], 'officialweb_goodshort')
        self.assertEqual(payload['target_key'], 'web_top_goodshort_pilot')
        self.assertEqual(payload['ranking_type'], 'Top in GoodShort')
        self.assertEqual(payload['top_n'], 2)
        self.assertTrue(payload['batch_complete'])
        self.assertEqual(payload['rows'][0]['rank'], 1)
        self.assertEqual(payload['rows'][0]['metrics']['episode_count'], '83')
        self.assertEqual(payload['rows'][0]['metrics']['plays_display'], '25.8M')

    def test_incomplete_requested_topn_fails_closed(self):
        with self.assertRaises(OfficialWebCollectorError):
            collect_goodshort_top(
                collection_date='2026-09-16',
                top_n=3,
                document=FIXTURE,
            )

    def test_duplicate_title_fails_closed(self):
        duplicated = FIXTURE.replace('Beta Love', 'Alpha Lady')
        with self.assertRaises(OfficialWebCollectorError):
            collect_goodshort_top(
                collection_date='2026-09-16',
                top_n=2,
                document=duplicated,
            )


if __name__ == '__main__':
    unittest.main()
