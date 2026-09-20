import unittest

from collector_import import CollectorImportError, validate_and_normalize


def make_payload(platform='NetShort', top_n=10):
    rows = []
    for rank in range(1, top_n + 1):
        row = {
            'rank': rank,
            'title': f'Title {rank}',
            'heat': f'{100-rank}K',
            'tags': ['Drama', 'Fantasy'],
        }
        if platform == 'NetShort':
            row['followers'] = f'{rank}K'
        else:
            row['badges'] = ['Up by 1'] if rank == 2 else []
            row['synopsis'] = f'Source synopsis {rank}'
        rows.append(row)
    return {
        'platform': platform,
        'source_type': 'SHORT_DRAMA_APP',
        'source_id': f'shortapp_{platform.lower()}',
        'target_key': 'daily_top_all',
        'ranking_type': 'Top Trending' if platform == 'NetShort' else 'Trending Series',
        'collection_method': 'APP_UI_XML',
        'collector_version': 'test-fixture-v1',
        'collection_date': '2026-09-15',
        'collected_at': '2026-09-15T09:00:00',
        'top_n': top_n,
        'batch_complete': True,
        'rank_conflicts': [],
        'rows': rows,
        'evidence': {
            'originalSourceType': 'SHORT_DRAMA_APP',
            'semanticVerified': True,
            'appFocusVerified': True,
            'ui_xml': r'D:\\example.xml',
        },
    }


class CollectorImportTests(unittest.TestCase):
    def test_complete_top10_normalizes_and_marks_old_new(self):
        result = validate_and_normalize(make_payload(), {'Title 1', 'Title 3'})
        self.assertEqual(result['rowCount'], 10)
        self.assertEqual(result['topN'], 10)
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

    def test_generic_platform_is_supported(self):
        payload = make_payload('GoodShort')
        payload['source_id'] = 'shortapp_goodshort'
        result = validate_and_normalize(payload, set())
        self.assertEqual(result['platform'], 'GoodShort')
        self.assertEqual(result['sourceId'], 'shortapp_goodshort')

    def test_top20_is_supported(self):
        payload = make_payload('DramaBox', top_n=20)
        payload['source_id'] = 'shortapp_dramabox'
        result = validate_and_normalize(payload, {'Title 1'})
        self.assertEqual(result['rowCount'], 20)
        self.assertEqual(result['topN'], 20)
        self.assertEqual(result['result']['collector']['topN'], 20)

    def test_incomplete_topn_is_rejected(self):
        payload = make_payload('ShortMax', top_n=20)
        payload['rows'] = payload['rows'][:-1]
        with self.assertRaises(CollectorImportError):
            validate_and_normalize(payload, set())

    def test_official_web_source_is_distinct_and_keeps_urls(self):
        payload = make_payload('ReelShort')
        payload.update({
            'source_type': 'OFFICIAL_WEB',
            'source_id': 'officialweb_reelshort',
            'target_key': 'web_top_all',
            'collection_method': 'WEB_SCRAPE',
            'locale': 'en-US',
        })
        payload['rows'][0]['source_url'] = 'https://example.com/drama/title-1'
        payload['rows'][0]['episode_url'] = 'https://example.com/episode/title-1-1'
        result = validate_and_normalize(payload, set())
        collector = result['result']['collector']
        first = result['result']['rows'][0]
        self.assertEqual(collector['sourceId'], 'officialweb_reelshort')
        self.assertEqual(collector['sourceType'], 'OFFICIAL_WEB')
        self.assertEqual(collector['locale'], 'en-US')
        self.assertEqual(result['status'], '已采集')
        self.assertEqual(result['newTitleCount'], 0)
        self.assertEqual(result['newTitles'], [])
        self.assertEqual(first['newness'], 'observed')
        self.assertEqual(first['pendingChecks'], [])
        self.assertEqual(first['sourceUrl'], 'https://example.com/drama/title-1')
        self.assertEqual(first['episodeUrl'], 'https://example.com/episode/title-1-1')


if __name__ == '__main__':
    unittest.main()
