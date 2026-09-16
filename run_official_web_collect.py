from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from collector_import import CollectorImportError, validate_and_normalize
from dramabox_full_collector import collect_dramabox_channel_all_pages
from official_web_collectors import (
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
    source_type = normalized['result']['collector']['sourceType']
    if source_type != 'OFFICIAL_WEB':
        raise CollectorImportError(f'official web runner received sourceType={source_type}')
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
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    targets = args.target or list(TARGETS)
    root = Path(args.root)
    results = []
    failures = []

    print('Official Web Collector V2 Multi-Target')
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
        'date': args.date,
        'root': str(root),
        'targetCount': len(targets),
        'succeeded': results,
        'failed': failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
