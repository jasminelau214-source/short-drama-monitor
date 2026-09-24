from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone

from collector_import import CollectorImportError, validate_and_normalize
from goodshort_collector import collect_goodshort_top


URL = 'https://www.goodshort.com/channel/Top-in-GoodShort'


def document(label='Top in GoodShort'):
    cards = []
    for i in range(1, 11):
        cards.append(f'''
<div class="book">
  <a class="book-name" href="/book/title-{i}">Title {i}</a>
  <span class="book-tag-item">Drama</span>
  <div class="intro">Synopsis {i}</div>
  <div class="like"><span>{i}.1M</span></div>
</div>
''')
    return f'<html><body><h2>{label}</h2>{"".join(cards)}</body></html>'


def payload_from_document(doc: str):
    payload = collect_goodshort_top(
        url=URL,
        collection_date='2026-09-20',
        top_n=10,
        document=doc,
    )
    payload['evidence'].update({
        'requestedUrl': URL,
        'httpStatus': 200,
        'pageUrl': URL,
        'fetchedAt': datetime.now(timezone.utc).isoformat(),
    })
    return payload


class GoodShortFaultIntegrationTests(unittest.TestCase):
    def test_live_shape_baseline_crosses_import_boundary(self):
        payload = payload_from_document(document())
        result = validate_and_normalize(payload, set())
        self.assertEqual(result['platform'], 'GoodShort')
        self.assertEqual(result['rowCount'], 10)
        self.assertEqual(result['status'], '已采集')
        self.assertEqual(result['newTitleCount'], 0)
        self.assertTrue(all(row['newness'] == 'observed' for row in result['result']['rows']))

    def test_target_semantic_replacement_fails_closed_even_when_rows_remain(self):
        payload = payload_from_document(document('Control Shelf'))
        self.assertFalse(payload['evidence']['semanticVerified'])
        with self.assertRaisesRegex(
            CollectorImportError,
            'TARGET_SEMANTIC_UNVERIFIED',
        ):
            validate_and_normalize(payload, set())

    def test_parseable_http_503_fails_closed(self):
        payload = payload_from_document(document())
        payload['evidence']['httpStatus'] = 503
        with self.assertRaisesRegex(CollectorImportError, 'HTTP_STATUS_INVALID'):
            validate_and_normalize(payload, set())

    def test_cross_host_redirect_fails_closed(self):
        payload = payload_from_document(document())
        payload['evidence']['pageUrl'] = 'https://example.invalid/control'
        with self.assertRaisesRegex(CollectorImportError, 'OFFICIAL_HOST_MISMATCH'):
            validate_and_normalize(payload, set())

    def test_stale_replay_fails_closed(self):
        payload = payload_from_document(document())
        payload['evidence']['fetchedAt'] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        with self.assertRaisesRegex(CollectorImportError, 'FETCH_EVIDENCE_STALE'):
            validate_and_normalize(payload, set())

    def test_missing_row_fails_closed_at_import_boundary(self):
        payload = payload_from_document(document())
        payload['rows'] = copy.deepcopy(payload['rows'][:-1])
        with self.assertRaisesRegex(CollectorImportError, 'Top10|10'):
            validate_and_normalize(payload, set())

    def test_duplicate_title_fails_closed_at_import_boundary(self):
        payload = payload_from_document(document())
        payload['rows'][1]['title'] = payload['rows'][0]['title']
        with self.assertRaisesRegex(CollectorImportError, '重复标题'):
            validate_and_normalize(payload, set())


if __name__ == '__main__':
    unittest.main()
