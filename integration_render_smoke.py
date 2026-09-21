from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from urllib.parse import urljoin


def get_json(base_url: str, path: str) -> tuple[int, dict]:
    url = urljoin(base_url.rstrip('/') + '/', path.lstrip('/'))
    request = urllib.request.Request(
        url,
        headers={'Accept': 'application/json', 'User-Agent': 'JSM-Integration-Smoke/1.0'},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = int(response.status)
            payload = json.loads(response.read().decode('utf-8'))
            return status, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {'raw': raw[:2000]}
        return int(exc.code), payload


def run(base_url: str, expected_commit: str = '') -> dict:
    health_status, health = get_json(base_url, '/health')
    ready_status, ready = get_json(base_url, '/ready')
    data_status, data = get_json(base_url, '/api/data')

    checks = {
        'health_http_200': health_status == 200,
        'health_liveness_true': bool(health.get('liveness') or health.get('ok')),
        'ready_http_200': ready_status == 200,
        'ready_true': bool(ready.get('ready')),
        'data_http_200': data_status == 200,
        'data_shape': isinstance(data.get('records'), list) and isinstance(data.get('summary'), dict),
    }

    actual_commit = str(ready.get('gitCommit') or health.get('gitCommit') or '').strip()
    if expected_commit:
        checks['commit_matches'] = actual_commit == expected_commit

    return {
        'status': 'PASS_RUNTIME_SMOKE' if all(checks.values()) else 'FAIL_RUNTIME_SMOKE',
        'checks': checks,
        'health': {
            'httpStatus': health_status,
            'liveness': health.get('liveness', health.get('ok')),
            'gitCommit': health.get('gitCommit', ''),
        },
        'ready': {
            'httpStatus': ready_status,
            'ready': ready.get('ready'),
            'gitCommit': ready.get('gitCommit', ''),
            'serviceName': ready.get('serviceName', ''),
            'required': ready.get('required', {}),
            'checks': ready.get('checks', {}),
        },
        'data': {
            'httpStatus': data_status,
            'recordCount': len(data.get('records') or []),
            'collectionDate': (data.get('summary') or {}).get('collectionDate'),
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--expected-commit', default='')
    args = parser.parse_args(argv)
    result = run(args.base_url, args.expected_commit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'PASS_RUNTIME_SMOKE' else 2


if __name__ == '__main__':
    raise SystemExit(main())
