import unittest

from collector_import import CollectorImportError, validate_and_normalize


def make_payload(platform='NetShort'):
    rows = []
    for rank in range(1, 11):
        row = {'rank': rank, 'title': f'Title {rank}', 'heat': f'{100-rank}K', 'tags': ['Drama', 'Fantasy']}
        if platform == 'NetShort':
            row['followers'] = f'{rank}K'
        else:
            row['badges'] = ['Up by 1'] if rank == 2 else []
            row['synopsis'] = f'Source synopsis {rank}'
        rows.append(row)
    return {
        'platform': platform,
        'source_type': 'SHORT_DRAMA_APP',
        'ranking_type': 'Top Trending' if platform == 'NetShort' else 'Trending Series',
        'collection_method': 'APP_UI_XML',
        'collection_date': '2026-09-15',
        'batch_complete': True,
        'rows': rows,
        'evidence': {'ui_xml': r'D:\\example.xml'},
    }


class CollectorImportTests(unittest.TestCase):
    def test_complete_top10_normalizes_and_marks_old_new(self):
        result = validate_and_normalize(make_payload(), {'Title 1', 'Title 3'})
        self.assertEqual(result['rowCount'], 10)
        self.assertEqual(result['newTitleCount'], 8)
        self.assertEqual(result['result']['rows'][0]['newness'], 'old')
        self.assertEqual(result['result']['rows'][1]['newness'], 'new')
        self.assertEqual(result['result']['rows'][0]['metrics']['followers'], '1K')
        self.assertTrue(result['result']['batchComplete'])

    def test_run_id_is_deterministic(self):
        first = validate_and_normalize(make_payload(), set())
        second = validate_and_normalize(make_payload(), set())
        self.assertEqual(first['runId'], second['runId'])

    def test_duplicate_rank_rejected(self):
        payload = make_payload()
        payload['rows'][9]['rank'] = 9
        with self.assertRaises(CollectorImportError):
            validate_and_normalize(payload, set())

    def test_incomplete_local_audit_rejected(self):
        payload = make_payload()
        payload['batch_complete'] = False
        with self.assertRaises(CollectorImportError):
            validate_and_normalize(payload, set())

    def test_moboreels_source_synopsis_is_not_official_synopsis(self):
        result = validate_and_normalize(make_payload('MoboReels'), set())
        row = result['result']['rows'][0]
        self.assertEqual(row['sourceSynopsis'], 'Source synopsis 1')
        self.assertNotIn('synopsis', row)


if __name__ == '__main__':
    unittest.main()
