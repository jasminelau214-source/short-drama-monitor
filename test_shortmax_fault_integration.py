from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone

from collector_import import CollectorImportError, validate_and_normalize
from official_web_collectors import OfficialWebCollectorError, collect_shortmax


URL = 'https://www.shorttv.live/'


def fixture_html(label='Most Popular'):
    cards = []
    for i in range(1, 13):
        cards.append(f'''
<div class="drama-card">
  <a class="card-title-layout" href="/drama/title-{i}">
    <p class="card-title">Title {i}</p>
  </a>
  <div class="card-overlay">
    <p class="overlay-tags"><span>Drama</span></p>
    <p class="overlay-description">Synopsis {i}</p>
  </div>
</div>
''')
    return f'''
<html><body>
<section>
  <h2 class="section-title">{label}</h2>
  <div class="drama-cards">{"".join(cards)}</div>
</section>
</body></html>
'''


def top10_payload(doc=None):
    payload = collect_shortmax(
        url=URL,
        section='Most Popular',
        collection_date='2026-09-20',
        top_n=10,
        document=doc or fixture_html(),
    )
    payload['evidence'].update({
        'requestedUrl': URL,
        'httpStatus': 200,
        'pageUrl': URL,
        'fetchedAt': datetime.now(timezone.utc).isoformat(),
    })
    return payload


class ShortMaxTop10IntegrationTests(unittest.TestCase):
    def test_top10_contract_crosses_import_boundary(self):
        payload = top10_payload()
        result = validate_and_normalize(payload, set())
        self.assertEqual(result['platform'], 'ShortMax')
        self.assertEqual(result['topN'], 10)
        self.assertEqual(result['rowCount'], 10)
        self.assertEqual(result['status'], '已采集')
        self.assertEqual(result['newTitleCount'], 0)

    def test_source_with_more_than_ten_cards_still_publishes_exact_top10(self):
        payload = top10_payload()
        self.assertEqual(len(payload['rows']), 10)
        self.assertEqual(payload['rows'][0]['rank'], 1)
        self.assertEqual(payload['rows'][-1]['rank'], 10)
        self.assertEqual(payload['rows'][-1]['title'], 'Title 10')

    def test_target_semantic_replacement_fails_closed(self):
        with self.assertRaisesRegex(OfficialWebCollectorError, 'SECTION_NOT_FOUND'):
            top10_payload(fixture_html('Control Shelf'))

    def test_parseable_http_503_fails_closed(self):
        payload = top10_payload()
        payload['evidence']['httpStatus'] = 503
        with self.assertRaisesRegex(CollectorImportError, 'HTTP_STATUS_INVALID'):
            validate_and_normalize(payload, set())

    def test_cross_host_redirect_fails_closed(self):
        payload = top10_payload()
        payload['evidence']['pageUrl'] = 'https://example.invalid/control'
        with self.assertRaisesRegex(CollectorImportError, 'OFFICIAL_HOST_MISMATCH'):
            validate_and_normalize(payload, set())

    def test_stale_replay_fails_closed(self):
        payload = top10_payload()
        payload['evidence']['fetchedAt'] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        with self.assertRaisesRegex(CollectorImportError, 'FETCH_EVIDENCE_STALE'):
            validate_and_normalize(payload, set())

    def test_missing_row_fails_closed(self):
        payload = top10_payload()
        payload['rows'] = copy.deepcopy(payload['rows'][:-1])
        with self.assertRaisesRegex(CollectorImportError, 'Top10|10'):
            validate_and_normalize(payload, set())

    def test_duplicate_title_fails_closed(self):
        payload = top10_payload()
        payload['rows'][1]['title'] = payload['rows'][0]['title']
        with self.assertRaisesRegex(CollectorImportError, '重复标题'):
            validate_and_normalize(payload, set())


if __name__ == '__main__':
    unittest.main()
