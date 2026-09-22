from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from collector_import import CollectorImportError, validate_and_normalize
from dramabox_full_collector import collect_dramabox_channel_all_pages
from goodshort_collector import collect_goodshort_top
from itemlist_platform_collectors import collect_flextv_top, collect_netshort_trending
from moboreels_web_collector import collect_moboreels_popular
from reelshort_collector import collect_reelshort_top
from official_web_collectors import (
    COLLECTION_LOCALE,
    COLLECTION_REGION,
    OfficialWebCollectorError,
    collect_shortmax,
)


TARGETS = {
    'shortmax_most_popular': {
        'platform': 'ShortMax',
        'target_key': 'web_most_popular_all',
        'collect': lambda date: collect_shortmax(
            collection_date=date,
            section='Most Popular',
            top_n=8,
        ),
    },
    'shortmax_war_god': {
        'platform': 'ShortMax',
        'target_key': 'web_category_war_god',
        'collect': lambda date: collect_shortmax(
            collection_date=date,
            section='War God',
            top_n=8,
        ),
    },
    'shortmax_tycoon_life': {
        'platform': 'ShortMax',
        'target_key': 'web_category_tycoon_life',
        'collect': lambda date: collect_shortmax(
            collection_date=date,
            section='Tycoon Life',
            top_n=8,
        ),
    },
    'shortmax_apocalypse': {
        'platform': 'ShortMax',
        'target_key': 'web_category_apocalypse',
        'collect': lambda date: collect_shortmax(
            collection_date=date,
            section='Apocalypse',
            top_n=8,
        ),
    },
    'shortmax_dragon_clan': {
        'platform': 'ShortMax',
        'target_key': 'web_category_dragon_clan',
        'collect': lambda date: collect_shortmax(
            collection_date=date,
            section='Dragon Clan',
            top_n=3,
        ),
    },
    'dramabox_trending': {
        'platform': 'DramaBox',
        'target_key': 'web_trending_all',
        'collect': lambda date: collect_dramabox_channel_all_pages(
            collection_date=date,
            channel='trending',
        ),
    },
    'goodshort_top': {
        'platform': 'GoodShort',
        'target_key': 'web_top_goodshort_pilot',
        'collect': lambda date: collect_goodshort_top(
            collection_date=date,
            top_n=10,
        ),
    },
    'reelshort_top': {
        'platform': 'ReelShort',
        'target_key': 'web_top_shelf_all',
        'collect': lambda date: collect_reelshort_top(
            collection_date=date,
            top_n=10,
        ),
    },
    'moboreels_popular': {
        'platform': 'MoboReels',
        'target_key': 'web_popular_series_all',
        'collect': lambda date: collect_moboreels_popular(
            collection_date=date,
            top_n=10,
        ),
    },
    'netshort_trending': {
        'platform': 'NetShort',
        'target_key': 'web_trending_now_all',
        'collect': lambda date: collect_netshort_trending(
            collection_date=date,
            top_n=10,
        ),
    },
    'flextv_top': {
        'platform': 'FlexTV',
        'target_key': 'web_top_in_flextv_all',
        'collect': lambda date: collect_flextv_top(
            collection_date=date,
            top_n=10,
        ),
    },
}


def default_root() -> Path:
    configured = os.environ.get('SHORT_DRAMA_COLLECTOR_ROOT')
    if configured:
        return Path(configured)
    if os.name == 'nt':
        return Path(r'D:\ShortDramaCollector')
    return Path.cwd() / 'collector_output'


def write_payload(root: Path, payload: dict) -> Path:
    date = str(payload['collection_date'])
    platform = str(payload['platform'])
    target_key = str(payload.get('target_key') or 'daily_top_all')
    folder = root / date / platform
    folder.mkdir(parents=True, exist_ok=True)
    filename = f'{target_key}.json'
    path = folder / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def audit_payload(payload: dict) -> dict:
    normalized = validate_and_normalize(payload, set())
    collector = normalized['result']['collector']
    source_type = collector['sourceType']
    if source_type != 'OFFICIAL_WEB':
        raise CollectorImportError(f'official web runner received sourceType={source_type}')
    if collector.get('locale') != COLLECTION_LOCALE:
        raise CollectorImportError(
            f'official web locale mismatch: expected={COLLECTION_LOCALE} actual={collector.get("locale") or "(blank)"}'
        )
    if collector.get('region') != COLLECTION_REGION:
        raise CollectorImportError(
            f'official web region mismatch: expected={COLLECTION_REGION} actual={collector.get("region") or "(blank)"}'
        )
    if normalized['rowCount'] != normalized['topN']:
        raise CollectorImportError('rowCount/topN mismatch')
    return normalized


def collect_target(name: str, collection_date: str, root: Path) -> dict:
    config = TARGETS[name]
    payload = config['collect'](collection_date)
    normalized = audit_payload(payload)
    path = write_payload(root, payload)
    return {
        'name': name,
        'platform': payload['platform'],
        'targetKey': payload['target_key'],
        'rankingType': payload.get('ranking_type', ''),
        'category': payload.get('category', ''),
        'rows': normalized['rowCount'],
        'topN': normalized['topN'],
        'status': normalized['status'],
        'path': str(path),
        'evidence': payload.get('evidence') or {},
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Collect verified official-web short-drama shelves into the shared collector spool.'
    )
    parser.add_argument(
        '--target',
        action='append',
        choices=sorted(TARGETS),
        help='Target to collect. Repeat for several. Default: all configured targets.',
    )
    parser.add_argument(
        '--date',
        default=datetime.now(timezone.utc).date().isoformat(),
        help='Collection date YYYY-MM-DD. Default: UTC today.',
    )
    parser.add_argument(
        '--root',
        default=str(default_root()),
        help='Collector spool root. Windows default: D:\\ShortDramaCollector.',
    )
    parser.add_argument(
        '--manifest',
        default='',
        help='Optional path for this exact run manifest. Scheduled sync should consume only this manifest.',
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    targets = args.target or list(TARGETS)
    root = Path(args.root)
    results = []
    failures = []
    run_id = 'web-' + uuid.uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()

    print('Official Web Collector V3 Promotion-Gated')
    print(f'Date: {args.date}')
    print(f'Root: {root}')
    print(f'Targets: {", ".join(targets)}')

    for name in targets:
        try:
            result = collect_target(name, args.date, root)
            results.append(result)
            print(
                'PASS '
                f"{result['platform']} / {result['targetKey']} / "
                f"rows={result['rows']} -> {result['path']}"
            )
        except (OfficialWebCollectorError, CollectorImportError, OSError, ValueError) as exc:
            failure = {'name': name, 'error': str(exc)}
            failures.append(failure)
            print(f'FAIL {name}: {exc}', file=sys.stderr)

    summary = {
        'ok': not failures,
        'runId': run_id,
        'startedAt': started_at,
        'finishedAt': datetime.now(timezone.utc).isoformat(),
        'date': args.date,
        'root': str(root),
        'targetCount': len(targets),
        'succeeded': results,
        'failed': failures,
    }
    if args.manifest:
        manifest_path = Path(args.manifest)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        print(f'MANIFEST {manifest_path}')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
