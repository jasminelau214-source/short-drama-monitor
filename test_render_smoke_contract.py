from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import integration_render_smoke as smoke


class RenderSmokeContractTests(unittest.TestCase):
    def test_blueprint_is_free_isolated_and_no_secrets(self):
        text = Path('render.integration-smoke.yaml').read_text(encoding='utf-8')
        self.assertIn('name: short-drama-monitor-integration-smoke', text)
        self.assertIn('plan: free', text)
        self.assertIn('branch: integration/core-contract-v1-2026-09-20', text)
        self.assertIn('autoDeploy: false', text)
        self.assertIn('healthCheckPath: /ready', text)
        self.assertNotIn('MONITOR_PERSISTENCE_TOKEN', text)
        self.assertNotIn('GEMINI_API_KEY', text)
        self.assertNotIn('ADMIN_PASSWORD', text)

    def test_smoke_pass_requires_health_ready_and_data(self):
        responses = [
            (200, {'ok': True, 'liveness': True, 'gitCommit': 'abc'}),
            (200, {'ready': True, 'gitCommit': 'abc', 'serviceName': 'smoke'}),
            (200, {'records': [], 'summary': {}}),
        ]
        with mock.patch.object(smoke, 'get_json', side_effect=responses):
            result = smoke.run('https://example.invalid', 'abc')
        self.assertEqual(result['status'], 'PASS_RUNTIME_SMOKE')
        self.assertTrue(result['checks']['commit_matches'])

    def test_smoke_fails_on_wrong_commit(self):
        responses = [
            (200, {'ok': True, 'liveness': True, 'gitCommit': 'wrong'}),
            (200, {'ready': True, 'gitCommit': 'wrong'}),
            (200, {'records': [], 'summary': {}}),
        ]
        with mock.patch.object(smoke, 'get_json', side_effect=responses):
            result = smoke.run('https://example.invalid', 'expected')
        self.assertEqual(result['status'], 'FAIL_RUNTIME_SMOKE')
        self.assertFalse(result['checks']['commit_matches'])

    def test_smoke_fails_when_ready_is_503(self):
        responses = [
            (200, {'ok': True, 'liveness': True}),
            (503, {'ready': False}),
            (200, {'records': [], 'summary': {}}),
        ]
        with mock.patch.object(smoke, 'get_json', side_effect=responses):
            result = smoke.run('https://example.invalid')
        self.assertEqual(result['status'], 'FAIL_RUNTIME_SMOKE')
        self.assertFalse(result['checks']['ready_http_200'])


if __name__ == '__main__':
    unittest.main()
