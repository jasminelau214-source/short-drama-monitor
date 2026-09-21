from __future__ import annotations

import inspect
import os
import sqlite3
import unittest
from pathlib import Path

import app


ENV_KEYS = (
    'JSM_READINESS_REQUIRE_PERSISTENCE',
    'JSM_READINESS_REQUIRE_ANALYSIS',
    'JSM_READINESS_REQUIRE_RESEARCH',
    'RENDER_GIT_COMMIT',
    'RENDER_SERVICE_NAME',
)


class ReadinessContractTests(unittest.TestCase):
    def setUp(self):
        self._env = {key: os.environ.get(key) for key in ENV_KEYS}
        self._connect = app.connect
        self._persistence_configured = app.persistence.configured
        self._persistence_healthcheck = getattr(app.persistence, 'healthcheck')
        self._analysis_configured = app.analysis_configured
        self._research_configured = app.research_configured

        for key in ENV_KEYS:
            os.environ.pop(key, None)

        app.connect = lambda: sqlite3.connect(':memory:', factory=app.ClosingSQLiteConnection)
        app.persistence.configured = lambda: False
        app.persistence.healthcheck = lambda timeout=5.0: {'ok': True}
        app.analysis_configured = lambda: False
        app.research_configured = lambda: False

    def tearDown(self):
        app.connect = self._connect
        app.persistence.configured = self._persistence_configured
        app.persistence.healthcheck = self._persistence_healthcheck
        app.analysis_configured = self._analysis_configured
        app.research_configured = self._research_configured
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_optional_dependencies_do_not_block_readiness(self):
        status = app.readiness_status()
        self.assertTrue(status['ready'])
        self.assertTrue(status['checks']['localDb']['ok'])
        self.assertFalse(status['checks']['persistence']['required'])
        self.assertFalse(status['checks']['analysis']['required'])
        self.assertFalse(status['checks']['research']['required'])

    def test_required_persistence_must_be_configured(self):
        os.environ['JSM_READINESS_REQUIRE_PERSISTENCE'] = 'true'
        status = app.readiness_status()
        self.assertFalse(status['ready'])
        self.assertEqual(status['checks']['persistence']['errorType'], 'NOT_CONFIGURED')

    def test_required_persistence_must_be_reachable(self):
        os.environ['JSM_READINESS_REQUIRE_PERSISTENCE'] = '1'
        app.persistence.configured = lambda: True

        def fail(timeout=5.0):
            raise RuntimeError('control dependency failure')

        app.persistence.healthcheck = fail
        status = app.readiness_status()
        self.assertFalse(status['ready'])
        self.assertTrue(status['checks']['persistence']['configured'])
        self.assertEqual(status['checks']['persistence']['errorType'], 'RuntimeError')

    def test_required_persistence_passes_after_read_only_probe(self):
        os.environ['JSM_READINESS_REQUIRE_PERSISTENCE'] = 'yes'
        app.persistence.configured = lambda: True
        seen = {}

        def healthy(timeout=5.0):
            seen['timeout'] = timeout
            return {'ok': True}

        app.persistence.healthcheck = healthy
        status = app.readiness_status()
        self.assertTrue(status['ready'])
        self.assertEqual(seen['timeout'], 5.0)
        self.assertTrue(status['checks']['persistence']['ok'])

    def test_required_analysis_must_be_configured(self):
        os.environ['JSM_READINESS_REQUIRE_ANALYSIS'] = 'true'
        status = app.readiness_status()
        self.assertFalse(status['ready'])
        self.assertFalse(status['checks']['analysis']['configured'])

        app.analysis_configured = lambda: True
        status = app.readiness_status()
        self.assertTrue(status['ready'])

    def test_required_research_must_be_configured(self):
        os.environ['JSM_READINESS_REQUIRE_RESEARCH'] = 'true'
        status = app.readiness_status()
        self.assertFalse(status['ready'])

        app.research_configured = lambda: True
        status = app.readiness_status()
        self.assertTrue(status['ready'])

    def test_readiness_exposes_non_sensitive_deployment_identity(self):
        os.environ['RENDER_GIT_COMMIT'] = 'abc123'
        os.environ['RENDER_SERVICE_NAME'] = 'integration-staging'
        status = app.readiness_status()
        self.assertEqual(status['gitCommit'], 'abc123')
        self.assertEqual(status['serviceName'], 'integration-staging')

    def test_http_handler_has_separate_health_and_ready_routes(self):
        source = inspect.getsource(app.Handler.do_GET)
        self.assertIn("path=='/health'", source)
        self.assertIn("path=='/ready'", source)
        self.assertIn("200 if status.get('ready') else 503", source)

    def test_render_blueprint_matches_reproducible_readiness_contract(self):
        text = Path('render.yaml').read_text(encoding='utf-8')
        self.assertIn('buildCommand: pip install -r requirements.txt', text)
        self.assertIn('startCommand: python app.py --host 0.0.0.0 --port $PORT --no-open', text)
        self.assertIn('healthCheckPath: /ready', text)
        self.assertIn('value: 3.13.15', text)
        self.assertIn('JSM_READINESS_REQUIRE_PERSISTENCE', text)
        self.assertIn('JSM_READINESS_REQUIRE_ANALYSIS', text)
        self.assertIn('SUPABASE_PERSISTENCE_URL', text)
        self.assertIn('MONITOR_PERSISTENCE_TOKEN', text)
        self.assertIn('GEMINI_API_KEY', text)


if __name__ == '__main__':
    unittest.main()
