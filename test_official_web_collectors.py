import json
import unittest

from official_web_collectors import (
    OfficialWebCollectorError,
    collect_dramabox_channel,
    collect_shortmax,
    parse_next_data,
    parse_shortmax_sections,
)


SHORTMAX_HTML = r'''
<html><body>
<section>
  <h2 class="section-title">Most Popular 🔥</h2>
  <div class="drama-cards">
    <div class="drama-card">
      <a class="card-image" href="/episode/alpha-1"><img src="a.jpg"></a>
      <a class="card-title-layout" href="/drama/alpha"><p class="card-title">Alpha &amp; Omega</p></a>
      <div class="card-overlay">
        <p class="overlay-tags"><span>Modern</span><span>Romance</span></p>
        <p class="overlay-description">First synopsis.</p>
      </div>
    </div>
    <div class="drama-card">
      <a class="card-image" href="/episode/beta-1"><img src="b.jpg"></a>
      <a class="card-title-layout" href="/drama/beta"><p class="card-title">Beta</p></a>
      <div class="card-overlay">
        <p class="overlay-tags"><span>High Fantasy</span></p>
        <p class="overlay-description">Second synopsis.</p>
      </div>
    </div>
  </div>
</section>
<section>
  <h2 class="section-title">War God ⚔️</h2>
  <div class="drama-cards">
    <div class="drama-card">
      <a class="card-title-layout" href="/drama/gamma"><p class="card-title">Gamma</p></a>
      <div class="card-overlay"><p class="overlay-tags"><span>High Fantasy</span></p></div>
    </div>
  </div>
</section>
</body></html>
'''

DRAMABOX_NEXT = {
    'props': {
        'pageProps': {
            'moreData': {
                'name': '当前热播',
                'items': [
                    {
                        'bookId': '4201',
                        'bookName': 'Drama A',
                        'bookNameLower': 'drama-a',
                        'introduction': 'Synopsis A',
                        'typeOneNames': ['F-Drama'],
                        'typeTwoNames': ['Romance', 'CEO'],
                        'tags': ['Billionaire'],
                        'viewCount': 12345,
                        'viewCountDisplay': '12.3K',
                        'ratings': 9.1,
                        'chapterCount': 58,
                    },
                    {
                        'bookId': '4102',
                        'bookName': 'Drama B',
                        'bookNameLower': 'drama-b',
                        'introduction': 'Synopsis B',
                        'typeOneNames': ['M-Drama'],
                        'typeTwoNames': ['Fantasy'],
                        'tags': ['Revenge'],
                        'viewCount': 9876,
                        'viewCountDisplay': '9.9K',
                        'ratings': 8.4,
                        'chapterCount': 74,
                    },
                ],
            },
            'pageNo': 1,
            'pages': 4,
            'locale': 'en',
        }
    },
    'buildId': 'dramaboxdb_prod_test',
}
DRAMABOX_HTML = '<html><body><script id="__NEXT_DATA__" type="application/json">' + json.dumps(DRAMABOX_NEXT) + '</script></body></html>'


class ShortMaxCollectorTests(unittest.TestCase):
    def test_parser_extracts_named_sections(self):
        sections = parse_shortmax_sections(SHORTMAX_HTML)
        self.assertEqual(list(sections), ['Most Popular 🔥', 'War God ⚔️'])
        self.assertEqual(len(sections['Most Popular 🔥']), 2)
        self.assertEqual(sections['Most Popular 🔥'][0]['title'], 'Alpha & Omega')
        self.assertEqual(sections['Most Popular 🔥'][0]['tags'], ['Modern', 'Romance'])
        self.assertEqual(sections['Most Popular 🔥'][0]['synopsis'], 'First synopsis.')
        self.assertEqual(sections['Most Popular 🔥'][0]['url'], 'https://www.shorttv.live/drama/alpha')

    def test_collect_shortmax_builds_generic_official_web_payload(self):
        result = collect_shortmax(
            document=SHORTMAX_HTML,
            section='Most Popular',
            collection_date='2026-09-16',
            top_n=2,
        )
        self.assertEqual(result['platform'], 'ShortMax')
        self.assertEqual(result['source_type'], 'OFFICIAL_WEB')
        self.assertEqual(result['target_key'], 'web_most_popular_all')
        self.assertEqual(result['ranking_type'], 'Most Popular 🔥')
        self.assertEqual(result['top_n'], 2)
        self.assertTrue(result['batch_complete'])
        self.assertEqual(result['rows'][0]['rank'], 1)
        self.assertEqual(result['rows'][1]['title'], 'Beta')

    def test_incomplete_requested_topn_fails_closed(self):
        with self.assertRaises(OfficialWebCollectorError):
            collect_shortmax(
                document=SHORTMAX_HTML,
                section='Most Popular',
                collection_date='2026-09-16',
                top_n=3,
            )

    def test_category_section_can_be_collected_independently(self):
        result = collect_shortmax(
            document=SHORTMAX_HTML,
            section='War God',
            collection_date='2026-09-16',
            top_n=1,
        )
        self.assertEqual(result['target_key'], 'web_category_war_god')
        self.assertEqual(result['category'], 'War God ⚔️')
        self.assertEqual(result['rows'][0]['title'], 'Gamma')


class DramaBoxCollectorTests(unittest.TestCase):
    def test_next_data_parser_extracts_json(self):
        data = parse_next_data(DRAMABOX_HTML)
        self.assertEqual(data['buildId'], 'dramaboxdb_prod_test')

    def test_collect_trending_keeps_native_fields(self):
        result = collect_dramabox_channel(
            document=DRAMABOX_HTML,
            channel='trending',
            collection_date='2026-09-16',
            top_n=2,
        )
        self.assertEqual(result['platform'], 'DramaBox')
        self.assertEqual(result['source_type'], 'OFFICIAL_WEB')
        self.assertEqual(result['target_key'], 'web_trending_all')
        self.assertEqual(result['ranking_type'], 'Trending')
        self.assertEqual(result['top_n'], 2)
        self.assertEqual(result['rows'][0]['title'], 'Drama A')
        self.assertEqual(result['rows'][0]['source_url'], 'https://www.dramaboxdb.com/movie/4201/drama-a')
        self.assertEqual(result['rows'][0]['tags'], ['F-Drama', 'Romance', 'CEO', 'Billionaire'])
        self.assertEqual(result['rows'][0]['metrics']['views'], '12345')
        self.assertEqual(result['rows'][0]['metrics']['episode_count'], '58')
        self.assertEqual(result['evidence']['pages'], 4)

    def test_dramabox_incomplete_topn_fails_closed(self):
        with self.assertRaises(OfficialWebCollectorError):
            collect_dramabox_channel(
                document=DRAMABOX_HTML,
                channel='trending',
                collection_date='2026-09-16',
                top_n=3,
            )


if __name__ == '__main__':
    unittest.main()
