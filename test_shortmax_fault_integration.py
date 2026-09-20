from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone

import run_official_web_collect as runner
from collector_import import CollectorImportError, validate_and_normalize
from official_web_collectors import OfficialWebCollectorError, collect_shortmax


URL = 'https://www.shorttv.live/'
CURRENT_TOP_N = 8


def fixture_html(label='Most Popular', card_count=CURRENT_TOP_N):
    cards = []
    for i in range(1, card_count + 1):
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


def current_payload(doc=None):
    payload = collect_shortmax(
        url=URL,
        section='Most Popular',
        collection_date='2026-09-20',
        top_n=CURRENT_TOP_N,
        document=doc or fixture_html(),
    )
    payload['evidence'].update({
        'requestedUrl': URL,
        'httpStatus': 200,
        'pageUrl': URL,
        'fetchedAt': datetime.now(timezone.utc).isoformat(),
    })
    return payload


class ShortMaxCurrentTargetIntegrationTests(unittest.TestCase):
    def test_current_top8_contract_crosses_import_boundary(self):
        payload = current_payload()
        result = validate_and_normalize(payload, set())
        self.assertEqual(result['platform'], 'ShortMax')
        self.assertEqual(result['topN'], CURRENT_TOP_N)
        self.assertEqual(result['rowCount'], CURRENT_TOP_N)
        self.assertEqual(result['status'], '已采集')
        self.assertEqual(result['newTitleCount'], 0)

    def test_runner_current_target_is_explicitly_top8(self):
        captured = {}
        original = runner.collect_shortmax

        def fake_collect_shortmax(**kwargs):
            captured.update(kwargs)
            return {'ok': True}

        runner.collect_shortmax = fake_collect_shortmax
        try:
            result = runner.TARGETS['shortmax_most_popular']['collect']('2026-09-20')
        finally:
            runner.collect_shortmax = original

        self.assertEqual(result, {'ok': True})
        self.assertEqual(captured['section'], 'Most Popular')
        self.assertEqual(captured['top_n'], CURRENT_TOP_N)

    def test_current_eight_card_shelf_cannot_be_promoted_as_legacy_top10(self):
        with self.assertRaisesRegex(
            OfficialWebCollectorError,
            'INCOMPLETE_SECTION.*expected=10 actual=8',
        ):
            collect_shortmax(
                url=URL,
                section='Most Popular',
                collection_date='2026-09-20',
                top_n=10,
                document=fixture_html(card_count=8),
            )

    def test_target_semantic_replacement_fails_closed(self):
        with self.assertRaisesRegex(OfficialWebCollectorError, 'SECTION_NOT_FOUND'):
            current_payload(fixture_html('Control Shelf'))

    def test_parseable_http_503_fails_closed(self):
        payload = current_payload()
        payload['evidence']['httpStatus'] = 503
        with self.assertRaisesRegex(CollectorImportError, 'HTTP_STATUS_INVALID'):
            validate_and_normalize(payload, set())

    def test_cross_host_redirect_fails_closed(self):
        payload = current_payload()
        payload['evidence']['pageUrl'] = 'https://example.invalid/control'
        with self.assertRaisesRegex(CollectorImportError, 'OFFICIAL_HOST_MISMATCH'):
            validate_and_normalize(payload, set())

    def test_stale_replay_fails_closed(self):
        payload = current_payload()
        payload['evidence']['fetchedAt'] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        with self.assertRaisesRegex(CollectorImportError, 'FETCH_EVIDENCE_STALE'):
            validate_and_normalize(payload, set())

    def test_missing_row_fails_closed(self):
        payload = current_payload()
        payload['rows'] = copy.deepcopy(payload['rows'][:-1])
        with self.assertRaisesRegex(CollectorImportError, 'Top8|8'):
            validate_and_normalize(payload, set())

    def test_duplicate_title_fails_closed(self):
        payload = current_payload()
        payload['rows'][1]['title'] = payload['rows'][0]['title']
        with self.assertRaisesRegex(CollectorImportError, '重复标题'):
            validate_and_normalize(payload, set())


if __name__ == '__main__':
    unittest.main()
