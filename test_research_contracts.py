from __future__ import annotations

import unittest

from research_guard import assess_research_task
from research_pipeline import CORE_FIELDS, OPTIONAL_FIELDS, classify_research_status
from research_validation import validate_research_payload
from research_writeback import select_same_platform_record


def ranking_run(run_id, date, platform, updated_at, titles, *, source_type='SHORT_DRAMA_APP', target='daily_top_all', complete=True):
    rows = [{'rank': i + 1, 'title': title} for i, title in enumerate(titles)]
    return {
        'id': run_id,
        'collection_date': date,
        'platform': platform,
        'updated_at': updated_at,
        'result_json': {
            'batchComplete': complete,
            'collector': {
                'sourceType': source_type,
                'targetKey': target,
                'topN': len(rows),
            },
            'rows': rows,
        },
    }


def complete_research(title='Example Drama'):
    payload = {field: 'verified' for field in CORE_FIELDS}
    payload.update({field: '' for field in OPTIONAL_FIELDS})
    payload.update({
        'canonicalTitle': title,
        'newnessResolution': 'new',
        'confidence': 'medium',
        'missingFields': [],
        'auditNotes': [],
        'sourceUrls': ['https://example.com/drama'],
        'needsGPT': False,
    })
    return payload


class AuthoritativeRunContractTests(unittest.TestCase):
    def test_later_complete_run_supersedes_dropped_title(self):
        runs = [
            ranking_run('old', '2026-09-20', 'ReelShort', '2026-09-20T01:00:00Z', ['Keep Me', 'Dropped Drama']),
            ranking_run('new', '2026-09-20', 'ReelShort', '2026-09-20T02:00:00Z', ['Keep Me', 'Replacement Drama']),
        ]
        task = {
            'analysis_run_id': 'old',
            'collection_date': '2026-09-20',
            'platform': 'ReelShort',
            'title': 'Dropped Drama',
        }
        result = assess_research_task(task, runs)
        self.assertFalse(result['current'])
        self.assertEqual(result['code'], 'SUPERSEDED_BY_LATER_RUN')

    def test_title_still_present_remains_current(self):
        runs = [
            ranking_run('old', '2026-09-20', 'ReelShort', '2026-09-20T01:00:00Z', ['Keep Me', 'Dropped Drama']),
            ranking_run('new', '2026-09-20', 'ReelShort', '2026-09-20T02:00:00Z', ['Keep Me', 'Replacement Drama']),
        ]
        task = {
            'analysis_run_id': 'old',
            'collection_date': '2026-09-20',
            'platform': 'ReelShort',
            'title': 'Keep Me',
        }
        result = assess_research_task(task, runs)
        self.assertTrue(result['current'])
        self.assertEqual(result['latestRunId'], 'new')

    def test_official_web_task_is_blocked_before_api(self):
        runs = [
            ranking_run('web', '2026-09-20', 'ReelShort', '2026-09-20T01:00:00Z', ['Web Drama'], source_type='OFFICIAL_WEB'),
        ]
        task = {
            'analysis_run_id': 'web',
            'collection_date': '2026-09-20',
            'platform': 'ReelShort',
            'title': 'Web Drama',
        }
        result = assess_research_task(task, runs)
        self.assertFalse(result['current'])
        self.assertEqual(result['code'], 'INVALID_RESEARCH_SOURCE_TYPE')

    def test_partial_run_cannot_be_authoritative(self):
        run = ranking_run('partial', '2026-09-20', 'ReelShort', '2026-09-20T01:00:00Z', ['A'], complete=False)
        task = {
            'analysis_run_id': 'partial',
            'collection_date': '2026-09-20',
            'platform': 'ReelShort',
            'title': 'A',
        }
        result = assess_research_task(task, [run])
        self.assertFalse(result['current'])
        self.assertEqual(result['code'], 'NO_COMPLETE_AUTHORITATIVE_RUN')


class ResearchCompleteContractTests(unittest.TestCase):
    def test_valid_complete_payload_passes(self):
        payload = complete_research()
        validation = validate_research_payload(payload, allowed_source_urls={'https://example.com/drama'})
        self.assertTrue(validation['ok'])

        status, missing = classify_research_status(
            requested_title='Example Drama',
            result=payload,
            search_meta={'officialCount': 1, 'sanitizedSources': 0},
        )
        self.assertEqual(status, 'COMPLETE')
        self.assertEqual(missing, [])

    def test_missing_core_field_cannot_complete(self):
        payload = complete_research()
        payload['synopsis'] = ''
        payload['missingFields'] = ['synopsis']
        status, missing = classify_research_status(
            requested_title='Example Drama',
            result=payload,
            search_meta={'officialCount': 1, 'sanitizedSources': 0},
        )
        self.assertEqual(status, 'NEEDS_GPT')
        self.assertIn('synopsis', missing)

    def test_identity_conflict_requires_review(self):
        payload = complete_research()
        payload['auditNotes'] = ['source conflict on identity']
        status, _ = classify_research_status(
            requested_title='Example Drama',
            result=payload,
            search_meta={'officialCount': 1, 'sanitizedSources': 0},
        )
        self.assertEqual(status, 'REVIEW_REQUIRED')

    def test_source_url_outside_evidence_is_rejected(self):
        payload = complete_research()
        payload['sourceUrls'] = ['https://wrong.example/drama']
        validation = validate_research_payload(payload, allowed_source_urls={'https://example.com/drama'})
        self.assertFalse(validation['ok'])
        self.assertTrue(any(x.startswith('sourceUrls_outside_evidence:') for x in validation['errors']))


class WritebackContractTests(unittest.TestCase):
    def test_same_platform_record_is_selected(self):
        records = [
            {'id': 'reel', 'title': 'Shared Title', 'app': 'ReelShort', 'history': []},
            {'id': 'net', 'title': 'Shared Title', 'app': 'NetShort', 'history': []},
        ]
        result = select_same_platform_record(records, platform='NetShort', title='Shared Title')
        self.assertEqual(result['id'], 'net')

    def test_cross_platform_fallback_is_forbidden(self):
        records = [
            {'id': 'reel', 'title': 'Shared Title', 'app': 'ReelShort', 'history': []},
        ]
        result = select_same_platform_record(records, platform='NetShort', title='Shared Title')
        self.assertIsNone(result)

    def test_historical_same_platform_event_is_allowed(self):
        records = [
            {
                'id': 'record',
                'title': 'Shared Title',
                'app': 'ReelShort',
                'history': [{'app': 'NetShort', 'date': '2026-09-18'}],
            },
        ]
        result = select_same_platform_record(records, platform='NetShort', title='Shared Title')
        self.assertEqual(result['id'], 'record')


if __name__ == '__main__':
    unittest.main()
