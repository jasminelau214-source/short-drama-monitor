# Integration-only read-only NetShort + FlexTV ItemList fault audit.
from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector_import import CollectorImportError, validate_and_normalize
from itemlist_platform_collectors import (
    FLEXTV_URL,
    NETSHORT_URL,
    collect_flextv_top,
    collect_netshort_trending,
)
from official_web_collectors import OfficialWebCollectorError, fetch_html_with_evidence


PLATFORMS = {
    'NetShort': {
        'url': NETSHORT_URL,
        'rankingType': 'Trending Now',
        'collect': collect_netshort_trending,
    },
    'FlexTV': {
        'url': FLEXTV_URL,
        'rankingType': 'Top in FlexTV',
        'collect': collect_flextv_top,
    },
}


def attach_transport(payload: dict, transport: dict, url: str) -> dict:
    out = copy.deepcopy(payload)
    evidence = dict(out.get('evidence') or {})
    evidence.update({
        'requestedUrl': url,
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


def audit_platform(platform: str, cfg: dict, collection_date: str) -> dict:
    generated_at = datetime.now(timezone.utc)
    try:
        fetched = fetch_html_with_evidence(cfg['url'])
        doc = str(fetched['document'])
        transport = dict(fetched.get('evidence') or {})

        baseline = cfg['collect'](
            url=cfg['url'],
            collection_date=collection_date,
            top_n=10,
            document=doc,
        )
        baseline = attach_transport(baseline, transport, cfg['url'])
        normalized = validate_and_normalize(baseline, set())

        scenarios = []

        mutated_doc, changed = re.subn(
            re.escape(cfg['rankingType']),
            'Control Shelf',
            doc,
            flags=re.I,
        )
        if changed:
            try:
                cfg['collect'](
                    url=cfg['url'],
                    collection_date=collection_date,
                    top_n=10,
                    document=mutated_doc,
                )
                semantic = {
                    'scenario': 'target_semantic_replaced_but_items_remain',
                    'safe': False,
                    'error': 'COLLECTOR_ACCEPTED_WRONG_TARGET',
                }
            except OfficialWebCollectorError as exc:
                semantic = {
                    'scenario': 'target_semantic_replaced_but_items_remain',
                    'safe': 'TARGET_ITEMLIST_NOT_IDENTIFIED' in str(exc),
                    'error': str(exc),
                    'expectedMarker': 'TARGET_ITEMLIST_NOT_IDENTIFIED',
                }
            semantic['mutationApplied'] = True
            semantic['mutationCount'] = changed
        else:
            semantic = {
                'scenario': 'target_semantic_replaced_but_items_remain',
                'safe': False,
                'classification': 'TEST_BLOCKED',
                'reason': f"{cfg['rankingType']} marker not found in live document",
                'mutationApplied': False,
            }
        scenarios.append(semantic)

        http_503 = copy.deepcopy(baseline)
        http_503['evidence']['httpStatus'] = 503
        scenarios.append(expect_rejected(
            'parseable_body_with_http_503', http_503, 'HTTP_STATUS_INVALID'
        ))

        redirect = copy.deepcopy(baseline)
        redirect['evidence']['pageUrl'] = 'https://example.invalid/control'
        scenarios.append(expect_rejected(
            'cross_host_redirect_with_parseable_body', redirect, 'OFFICIAL_HOST_MISMATCH'
        ))

        stale = copy.deepcopy(baseline)
        stale['evidence']['fetchedAt'] = (
            generated_at - timedelta(hours=1)
        ).isoformat()
        scenarios.append(expect_rejected(
            'stale_replay_freshness', stale, 'FETCH_EVIDENCE_STALE'
        ))

        missing = copy.deepcopy(baseline)
        missing['rows'] = missing['rows'][:-1]
        scenarios.append(expect_rejected('missing_one_row', missing, 'Top10'))

        duplicate = copy.deepcopy(baseline)
        duplicate['rows'][1]['title'] = duplicate['rows'][0]['title']
        scenarios.append(expect_rejected('duplicate_title', duplicate, '重复标题'))

        unsafe = [x for x in scenarios if not x.get('safe')]
        return {
            'platform': platform,
            'status': 'PASS_FAULT' if not unsafe else 'BLOCKED_OR_FAIL',
            'baseline': {
                'status': normalized['status'],
                'rowCount': normalized['rowCount'],
                'topN': normalized['topN'],
                'rankingType': normalized['result']['collector']['rankingType'],
                'httpStatus': baseline['evidence'].get('httpStatus'),
                'pageUrl': baseline['evidence'].get('pageUrl'),
                'semanticMethod': baseline['evidence'].get('semanticMethod'),
                'itemListName': baseline['evidence'].get('itemListName'),
            },
            'scenarioCount': len(scenarios),
            'unsafeScenarioCount': len(unsafe),
            'scenarios': scenarios,
        }
    except Exception as exc:
        return {
            'platform': platform,
            'status': 'BLOCKED_BASELINE_UNAVAILABLE',
            'error': f'{type(exc).__name__}: {exc}',
            'scenarios': [],
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument('--output', default='')
    args = parser.parse_args(argv)

    results = [
        audit_platform(platform, cfg, args.date)
        for platform, cfg in PLATFORMS.items()
    ]
    pass_count = sum(1 for item in results if item['status'] == 'PASS_FAULT')
    payload = {
        'phase': 'INTEGRATION_ITEMLIST_PLATFORM_FAULT',
        'productionWrite': False,
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'collectionDate': args.date,
        'platformCount': len(results),
        'passCount': pass_count,
        'status': 'PASS_FAULT' if pass_count == len(results) else 'BLOCKED',
        'platforms': results,
    }

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + '\n', encoding='utf-8')
        lines = [
            '# NetShort + FlexTV Integration Fault Audit',
            '',
            f"- Overall: **{payload['status']}**",
            f"- PASS: **{pass_count}/{len(results)}**",
            '',
        ]
        for item in results:
            lines.append(
                f"- {item['platform']}: **{item['status']}**; "
                f"rows={(item.get('baseline') or {}).get('rowCount', '-')}; "
                f"unsafe={item.get('unsafeScenarioCount', '-')}"
            )
        path.with_suffix('.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
