from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import app
from drama_identity import normalize_title
from live_observations_v2 import merge_analysis_records

ROOT = Path(__file__).resolve().parent


def _split_lane(value):
    return [x.strip() for x in str(value or '').replace('/', '\n').splitlines() if x.strip()]


class FrontendStaticContractTests(unittest.TestCase):
    def test_ui_does_not_infer_new_from_missing_previous_rank(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('function isAppRankingNew(item)', html)
        self.assertNotIn('if (isNew || move === null', html)
        self.assertNotIn('r.firstDate === activeDate', html)
        self.assertNotIn('r.firstDate===activeDate', html)
        self.assertIn('没有可比的前一有效采集点', html)

    def test_poster_is_same_record_only(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn("return String(item?.posterUrl || '').trim();", html)
        self.assertNotIn('posterUrl || item?.coverUrl', html)
        self.assertNotIn('r.posterUrl || r.poster || r.cover', html)
        self.assertNotIn('local_by_title', html)

    def test_runtime_patch_skips_rebased_ui(self):
        patch = (ROOT / 'runtime_ui_patch.py').read_text(encoding='utf-8')
        self.assertIn("if 'UI REDESIGN V1 FOUNDATION' in html:", patch)
        self.assertIn('return', patch)

    def test_research_and_quality_states_are_visible(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('researchTaskBlock(item)', html)
        self.assertIn('dataQualityChip(r)', html)
        self.assertIn('历史口径推断', html)


class PublicationContractTests(unittest.TestCase):
    def _db(self):
        tmp = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False)
        tmp.close()
        path = tmp.name
        with sqlite3.connect(path) as c:
            c.execute('CREATE TABLE analysis_runs(id TEXT, collection_date TEXT, platform TEXT, status TEXT, result_json TEXT, updated_at TEXT)')
        def connect():
            c = sqlite3.connect(path)
            c.row_factory = sqlite3.Row
            return c
        return path, connect

    def _insert(self, connect, *, run_id, date, platform, result, updated):
        with connect() as c:
            c.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?)', (run_id, date, platform, '已分析', json.dumps(result), updated))
            c.commit()

    def test_later_incomplete_run_cannot_override_complete(self):
        path, connect = self._db()
        try:
            complete = {'batchComplete': True, 'rows': [
                {'rank': 1, 'title': 'Alpha', 'newness': 'new', 'confidence': 'collector-verified'},
                {'rank': 2, 'title': 'Beta', 'newness': 'old', 'confidence': 'collector-verified'},
            ]}
            incomplete = {'batchComplete': False, 'rows': [{'rank': 1, 'title': 'Wrong'}]}
            self._insert(connect, run_id='good', date='2026-09-10', platform='ReelShort', result=complete, updated='2026-09-10T10:00:00Z')
            self._insert(connect, run_id='bad', date='2026-09-10', platform='ReelShort', result=incomplete, updated='2026-09-10T11:00:00Z')
            records = merge_analysis_records([], connect, normalize_title, _split_lane)
            self.assertEqual({r['title'] for r in records}, {'Alpha', 'Beta'})
            alpha = next(r for r in records if r['title'] == 'Alpha')
            self.assertEqual(alpha['analysisRunId'], 'good')
            self.assertEqual(alpha['dataQuality'], 'VALID')
            self.assertTrue(alpha['scopeInferred'])
            self.assertEqual(alpha['appRankingNewness'], 'NEW')
        finally:
            Path(path).unlink(missing_ok=True)

    def test_same_platform_different_target_keys_remain_separate(self):
        path, connect = self._db()
        try:
            for target, rank, updated in [('daily_trending_all', 1, '2026-09-17T10:00:00Z'), ('daily_must_sees_all', 1, '2026-09-17T10:01:00Z')]:
                result = {'batchComplete': True, 'collector': {'sourceType': 'SHORT_DRAMA_APP', 'targetKey': target, 'rankingType': target, 'topN': 1}, 'rows': [{'rank': rank, 'title': 'Same Title', 'newness': 'old'}]}
                self._insert(connect, run_id=target, date='2026-09-17', platform='DramaBox', result=result, updated=updated)
            records = merge_analysis_records([], connect, normalize_title, _split_lane)
            self.assertEqual(len(records), 2)
            self.assertEqual({r['targetKey'] for r in records}, {'daily_trending_all', 'daily_must_sees_all'})
            self.assertTrue(all(not r['scopeInferred'] for r in records))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_dubbed_identity_fixture(self):
        self.assertEqual(normalize_title('Example Drama (Dubbed)'), normalize_title('Example Drama'))
        self.assertEqual(normalize_title('English Dub: Example Drama'), normalize_title('Example Drama'))


class ResearchProjectionTests(unittest.TestCase):
    def _valid_research(self):
        return {
            'synopsis':'s','genre':'现代都市','lane':'l','audience':'女频','storyCore':'c','storySkin':'skin','conflict':'conf','payoff':'p',
            'localizationLevel':'high','localizationJudgment':'ok','mismatch':'none',
            'openingSummary':'','openingType':'','payEpisode':'','paywallSummary':'','paywallType':'',
            'canonicalTitle':'Example','newnessResolution':'new','confidence':'medium',
            'missingFields':['openingSummary','openingType','payEpisode','paywallSummary','paywallType'],
            'auditNotes':[],'sourceUrls':['https://example.com/evidence'],'needsGPT':False,
        }

    def test_complete_requires_contract_evidence(self):
        view = app._research_task_view({
            'status':'COMPLETE','research_json':self._valid_research(),
            'sources':[{'url':'https://example.com/evidence'}],'confidence':'medium','missing_fields':[],
            'error':'','collection_date':'2026-09-17','analysis_run_id':'run-1'
        })
        self.assertEqual(view['status'], 'COMPLETE')
        self.assertTrue(view['contractValid'])
        self.assertEqual(view['evidenceCount'], 1)

    def test_review_required_identity_conflict_is_explicit(self):
        view = app._research_task_view({'status':'REVIEW_REQUIRED','error':'RESEARCH_IDENTITY_UNRESOLVED: ambiguous','sources':[],'research_json':{}})
        self.assertEqual(view['status'], 'REVIEW_REQUIRED')
        self.assertTrue(view['identityConflict'])
        self.assertFalse(view['contractValid'])

    def test_needs_gpt_remains_distinct(self):
        view = app._research_task_view({'status':'NEEDS_GPT','missing_fields':['storyCore'],'sources':[],'research_json':{}})
        self.assertEqual(view['status'], 'NEEDS_GPT')
        self.assertIn('storyCore', view['missingFields'])


if __name__ == '__main__':
    unittest.main()
