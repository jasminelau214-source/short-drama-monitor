import unittest

from live_observations import _build_current_scope_coverage, _build_web_collection


def rows(n, prefix):
    return [{'rank': i, 'title': f'{prefix} {i}'} for i in range(1, n + 1)]


def target(target_id, source_name, ranking_type, top_n, *, category='All', enabled=True):
    return {
        'source_group': 'OFFICIAL_WEB',
        'source_name': source_name,
        'source_enabled': True,
        'target_id': target_id,
        'target_key': f'legacy_{target_id}',
        'ranking_type': ranking_type,
        'category': category,
        'top_n': top_n,
        'target_enabled': enabled,
    }


def job(target_id, n, prefix, status='SUCCEEDED'):
    return {
        'id': f'job-{target_id}',
        'target_id': target_id,
        'collection_date': '2026-09-16',
        'status': status,
        'finished_at': '2026-09-16T12:00:00Z',
        'result_json': {'rows': rows(n, prefix)},
    }


class WebCurrentScopeTests(unittest.TestCase):
    def monitoring(self):
        return {
            'collectionDate': '2026-09-16',
            'targetStatus': [
                target('dramabox', 'DramaBox Official Web', 'Trending', 60),
                target('goodshort', 'GoodShort Official Web', 'Top in GoodShort', 10),
                target('shortmax-cat', 'ShortMax Official Web', 'Category Shelf', 8, category='War God'),
                target('shortmax-pop', 'ShortMax Official Web', 'Most Popular', 8),
                target('moboreels-old', 'MoboReels Official Web', 'Trending Series', 10),
                target('netshort-old', 'NetShort Official Web', 'Homepage Top10', 10),
                target('reelshort-disabled', 'ReelShort Official Web', 'TOP', 10, enabled=False),
            ],
            'jobs': [
                job('dramabox', 60, 'DramaBox'),
                job('goodshort', 10, 'GoodShort'),
                job('shortmax-cat', 8, 'ShortMax Category'),
                job('shortmax-pop', 8, 'ShortMax Popular'),
                job('moboreels-old', 10, 'MoboReels Old'),
                job('netshort-old', 10, 'NetShort Old'),
                job('reelshort-disabled', 10, 'ReelShort Old'),
            ],
        }

    def test_current_scope_keeps_only_equivalent_top10(self):
        coverage = _build_current_scope_coverage(self.monitoring())
        self.assertEqual(coverage['target_jobs'], 2)
        self.assertEqual(coverage['web_platforms_with_jobs'], 2)
        self.assertEqual(coverage['extracted_rows'], 20)
        self.assertEqual(coverage['succeeded_targets'], 2)

    def test_dramabox_top60_is_trimmed_not_padded_or_merged(self):
        web = _build_web_collection(self.monitoring())
        self.assertEqual(web['rowCount'], 20)
        drama = [x for x in web['rows'] if x['platform'] == 'DramaBox']
        self.assertEqual(len(drama), 10)
        self.assertEqual([x['rank'] for x in drama], list(range(1, 11)))

    def test_official_web_never_auto_attaches_research(self):
        web = _build_web_collection(self.monitoring())
        self.assertTrue(web['rows'])
        self.assertTrue(all(x['researchTaskStatus'] == '' for x in web['rows']))
        self.assertTrue(all(x['research'] == {} for x in web['rows']))
        self.assertTrue(all(x['analysisStatus'] == '证据层已采集（不自动深研）' for x in web['rows']))


if __name__ == '__main__':
    unittest.main()
