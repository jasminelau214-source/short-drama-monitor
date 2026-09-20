from __future__ import annotations

import json
import os
import pathlib
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer


_TEST_DATA_DIR = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = _TEST_DATA_DIR.name

import app
from collector_import import validate_and_normalize
from research_pipeline import CORE_FIELDS, OPTIONAL_FIELDS
from research_validation import validate_research_payload


ROOT = pathlib.Path(__file__).resolve().parent


def valid_research(title: str) -> dict:
    payload = {field: 'verified' for field in CORE_FIELDS}
    payload['synopsis'] = 'Golden E2E verified synopsis'
    payload['genre'] = '现代都市'
    payload['lane'] = 'Golden E2E lane'
    payload['audience'] = '泛受众'
    payload['storyCore'] = 'Golden E2E story core'
    payload['storySkin'] = 'Golden E2E story skin'
    payload['conflict'] = 'Golden E2E conflict'
    payload['payoff'] = 'Golden E2E payoff'
    payload['localizationLevel'] = 'Golden E2E localization'
    payload['localizationJudgment'] = 'Golden E2E judgment'
    payload['mismatch'] = 'none'
    payload.update({field: '' for field in OPTIONAL_FIELDS})
    payload.update({
        'canonicalTitle': title,
        'newnessResolution': 'new',
        'confidence': 'medium',
        'missingFields': [],
        'auditNotes': [],
        'sourceUrls': ['https://example.com/golden-evidence'],
        'needsGPT': False,
    })
    return payload


class GoldenLocalHTTPE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(
            (ROOT / 'tests' / 'fixtures' / 'golden_real_app_20260917.json').read_text(encoding='utf-8')
        )

        # Ensure an isolated, empty local cache. No Supabase persistence is configured.
        for key in ('SUPABASE_PERSISTENCE_URL', 'MONITOR_PERSISTENCE_TOKEN', 'TAVILY_API_KEY', 'GEMINI_API_KEY'):
            os.environ.pop(key, None)

        with app.connect() as conn:
            conn.execute('delete from analysis_runs')
            conn.execute('delete from drama_overrides')
            conn.execute('delete from collection_uploads')
            conn.commit()

        cls.normalized = {}
        for platform in ('MoboReels', 'NetShort'):
            item = cls.fixture['platforms'][platform]
            payload = {
                'platform': platform,
                'source_type': item['sourceType'],
                'source_id': item['sourceId'],
                'target_key': item['targetKey'],
                'ranking_type': item['rankingType'],
                'category': 'All',
                'collection_method': item['collectionMethod'],
                'collection_date': cls.fixture['collectionDate'],
                'top_n': item['topN'],
                'batch_complete': True,
                'rows': item['rows'],
                'provider': 'golden-local-http-e2e',
                'evidence': {
                    'fixture': 'golden_real_app_20260917',
                    'originalSourceType': item['sourceType'],
                },
            }
            run = validate_and_normalize(payload, item['priorAppTitles'])
            cls.normalized[platform] = run
            with app.connect() as conn:
                conn.execute(
                    '''insert into analysis_runs
                    (id,collection_date,platform,upload_ids_json,status,result_json,error,model,created_at,updated_at)
                    values(?,?,?,?,?,?,?,?,?,?)''',
                    (
                        run['runId'],
                        run['collectionDate'],
                        run['platform'],
                        '[]',
                        run['status'],
                        json.dumps(run['result'], ensure_ascii=False),
                        '',
                        run['model'],
                        '2026-09-17T09:22:00+00:00',
                        '2026-09-17T09:22:00+00:00',
                    ),
                )
                conn.commit()

        # Drive one real new Mobo title through deterministic COMPLETE validation
        # and the actual app local writeback function.
        cls.researched_title = 'Left at the Altar, Married Power'
        research = valid_research(cls.researched_title)
        validation = validate_research_payload(
            research,
            allowed_source_urls={'https://example.com/golden-evidence'},
        )
        if not validation['ok']:
            raise AssertionError(validation)
        app.apply_research_result(
            {
                'platform': 'MoboReels',
                'title': cls.researched_title,
            },
            research,
        )

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        _TEST_DATA_DIR.cleanup()

    @classmethod
    def get_json(cls, path):
        with urllib.request.urlopen(cls.base_url + path, timeout=5) as response:
            return response.status, json.loads(response.read().decode('utf-8'))

    @classmethod
    def get_text(cls, path):
        with urllib.request.urlopen(cls.base_url + path, timeout=5) as response:
            return response.status, response.read().decode('utf-8')

    def test_api_data_exposes_real_app_latest_batch(self):
        status, data = self.get_json('/api/data')
        self.assertEqual(status, 200)
        self.assertEqual(data['summary']['collectionDate'], '2026-09-17')
        self.assertEqual(data['summary']['totalRows'], 20)

        latest = [
            record for record in data['records']
            if record.get('date') == '2026-09-17'
        ]
        self.assertEqual(len(latest), 20)
        self.assertTrue(all(record.get('sourceType') == 'SHORT_DRAMA_APP' for record in latest))
        self.assertNotIn('WEB GHOST MUST NOT PUBLISH', {record.get('title') for record in latest})

    def test_actual_writeback_reappears_through_api(self):
        _, data = self.get_json('/api/data')
        record = next(
            item for item in data['records']
            if item.get('title') == self.researched_title
            and item.get('app') == 'MoboReels'
        )
        self.assertEqual(record.get('synopsis'), 'Golden E2E verified synopsis')
        self.assertEqual(record.get('researchStatus'), '已研究')
        self.assertEqual(record.get('researchConfidence'), 'medium')
        self.assertEqual(record.get('researchSources'), ['https://example.com/golden-evidence'])

    def test_health_reports_local_isolated_environment(self):
        status, health = self.get_json('/health')
        self.assertEqual(status, 200)
        self.assertTrue(health['ok'])
        self.assertEqual(health['collectionDate'], '2026-09-17')
        self.assertEqual(health['latestRows'], 20)
        self.assertFalse(health['persistenceConfigured'])

    def test_frontend_html_embeds_current_real_app_data(self):
        status, html = self.get_text('/')
        self.assertEqual(status, 200)
        self.assertIn('Left at the Altar, Married Power', html)
        self.assertIn('Back to 95 - Her Big Comeback', html)


if __name__ == '__main__':
    unittest.main()
