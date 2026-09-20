# Integration-only read-only ShortMax Top10 audit.
from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector_import import CollectorImportError, validate_and_normalize
from official_web_collectors import OfficialWebCollectorError, collect_shortmax, fetch_html_with_evidence


URL = 'https://www.shorttv.live/'


def attach_transport(payload: dict, transport: dict) -> dict:
    out = copy.deepcopy(payload)
    evidence = dict(out.get('evidence') or {})
    evidence.update({
        'requestedUrl': URL,
        'httpStatus': transport.get('httpStatus'),
        'pageUrl': transport.get('pageUrl'),
        'fetchedAt': transport.get('fetchedAt'),
    })
    out['evidence'] = evidence
    return out


def expect_rejected(name: str, payload: dict, expected: str) -> dict:
    try:
        validate_and_normalize(payload, set())
    except CollectorImportError as exc:
        message = str(exc)
        return {
            'scenario': name,
            'safe': expected in message,
            'error': message,
            'expectedMarker': expected,
        }
    return {
        'scenario': name,
        'safe': False,
        'error': 'PAYLOAD_ACCEPTED',
        'expectedMarker': expected,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument('--output', default='')
    args = parser.parse_args(argv)

    generated_at = datetime.now(timezone.utc)
    try:
        fetched = fetch_html_with_evidence(URL)
        doc = str(fetched['document'])
        transport = dict(fetched.get('evidence') or {})

        baseline = collect_shortmax(
            url=URL,
            section='Most Popular',
            collection_date=args.date,
            top_n=10,
            document=doc,
        )
        baseline = attach_transport(baseline, transport)
        normalized = validate_and_normalize(baseline, set())

        scenarios = []

        mutated_doc, mutation_count = re.subn(
            r'Most\s+Popular',
            'Control Shelf',
            doc,
            flags=re.I,
        )
        if mutation_count:
            try:
                semantic_payload = collect_shortmax(
                    url=URL,
                    section='Most Popular',
                    collection_date=args.date,
                    top_n=10,
                    document=mutated_doc,
                )
                semantic_payload = attach_transport(semantic_payload, transport)
                semantic = expect_rejected(
                    'target_semantic_replaced_but_items_remain',
                    semantic_payload,
                    'TARGET_SEMANTIC_UNVERIFIED',
                )
            except OfficialWebCollectorError as exc:
                semantic = {
                    'scenario': 'target_semantic_replaced_but_items_remain',
                    'safe': 'SECTION_NOT_FOUND' in str(exc),
                    'error': str(exc),
                    'expectedMarker': 'SECTION_NOT_FOUND_OR_TARGET_SEMANTIC_UNVERIFIED',
                }
            semantic['mutationApplied'] = True
            semantic['mutationCount'] = mutation_count
        else:
            semantic = {
                'scenario': 'target_semantic_replaced_but_items_remain',
                'safe': False,
                'classification': 'TEST_BLOCKED',
                'reason': 'Most Popular label not found in live document',
                'mutationApplied': False,
                'mutationCount': 0,
            }
        scenarios.append(semantic)

        http_503 = copy.deepcopy(baseline)
        http_503['evidence']['httpStatus'] = 503
        scenarios.append(expect_rejected(
            'parseable_body_with_http_503',
            http_503,
            'HTTP_STATUS_INVALID',
        ))

        redirect = copy.deepcopy(baseline)
        redirect['evidence']['pageUrl'] = 'https://example.invalid/control'
        scenarios.append(expect_rejected(
            'cross_host_redirect_with_parseable_body',
            redirect,
            'OFFICIAL_HOST_MISMATCH',
        ))

        stale = copy.deepcopy(baseline)
        stale['evidence']['fetchedAt'] = (
            generated_at - timedelta(hours=1)
        ).isoformat()
        scenarios.append(expect_rejected(
            'stale_replay_freshness',
            stale,
            'FETCH_EVIDENCE_STALE',
        ))

        missing = copy.deepcopy(baseline)
        missing['rows'] = missing['rows'][:-1]
        scenarios.append(expect_rejected(
            'missing_one_row',
            missing,
            'Top10',
        ))

        duplicate = copy.deepcopy(baseline)
        duplicate['rows'][1]['title'] = duplicate['rows'][0]['title']
        scenarios.append(expect_rejected(
            'duplicate_title',
            duplicate,
            '重复标题',
        ))

        unsafe = [item for item in scenarios if not item.get('safe')]
        payload = {
            'platform': 'ShortMax',
            'phase': 'INTEGRATION_PLATFORM_FAULT',
            'productionWrite': False,
            'generatedAt': generated_at.isoformat(),
            'collectionDate': args.date,
            'target': {
                'rankingType': 'Most Popular',
                'requestedTopN': 10,
            },
            'status': 'PASS_FAULT' if not unsafe else 'BLOCKED_OR_FAIL',
            'baseline': {
                'status': normalized['status'],
                'rowCount': normalized['rowCount'],
                'topN': normalized['topN'],
                'rankingType': normalized['result']['collector']['rankingType'],
                'sourceType': normalized['result']['collector']['sourceType'],
                'httpStatus': baseline['evidence'].get('httpStatus'),
                'pageUrl': baseline['evidence'].get('pageUrl'),
                'semanticVerified': baseline['evidence'].get('semanticVerified'),
            },
            'scenarioCount': len(scenarios),
            'unsafeScenarioCount': len(unsafe),
            'scenarios': scenarios,
        }
    except Exception as exc:
        payload = {
            'platform': 'ShortMax',
            'phase': 'INTEGRATION_PLATFORM_FAULT',
            'productionWrite': False,
            'generatedAt': generated_at.isoformat(),
            'collectionDate': args.date,
            'target': {
                'rankingType': 'Most Popular',
                'requestedTopN': 10,
            },
            'status': 'BLOCKED_BASELINE_UNAVAILABLE',
            'error': f'{type(exc).__name__}: {exc}',
            'scenarios': [],
        }

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + '\n', encoding='utf-8')
        path.with_suffix('.md').write_text(
            '# ShortMax Integration Top10 Fault Audit\n\n'
            f"- Status: **{payload.get('status')}**\n"
            f"- Requested target: **Most Popular Top10**\n"
            f"- Baseline rows: **{(payload.get('baseline') or {}).get('rowCount', '-')}**\n"
            f"- Scenarios: **{payload.get('scenarioCount', 0)}**\n"
            f"- Unsafe: **{payload.get('unsafeScenarioCount', 0)}**\n",
            encoding='utf-8',
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
