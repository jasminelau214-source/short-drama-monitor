import unittest

from official_web_collectors import (
    OfficialWebCollectorError,
    collect_shortmax,
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
        self.assertEqual(result['target_key'], 'web_most_popular')
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
        self.assertEqual(result['target_key'], 'web_war_god')
        self.assertEqual(result['category'], 'War God ⚔️')
        self.assertEqual(result['rows'][0]['title'], 'Gamma')


if __name__ == '__main__':
    unittest.main()
