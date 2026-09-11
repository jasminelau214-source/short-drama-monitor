from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict


_RANKING_BADGE_PATTERNS = [
    re.compile(r'^\s*(?:up|down)\s+by\s+\d+\s*$', re.I),
    re.compile(r'^\s*latest\s+on\s+the\s+list\s*$', re.I),
    re.compile(r'^\s*new\s+on\s+(?:the\s+)?list\s*$', re.I),
    re.compile(r'^\s*(?:latest|new)\s*$', re.I),
]

_CONTROLLED_GENRES = {
    '现代都市', '校园青春', '犯罪黑帮', '科幻', '奇幻超自然', '西幻',
    '悬疑惊悚', '历史古装', '动作冒险', '家庭伦理', '其他',
}
_CONTROLLED_AUDIENCES = {'女频', '男频', '泛受众', '待确认'}
_AUTO_CORE_FIELDS = [
    'synopsis', 'genre', 'lane', 'audience', 'storyCore', 'storySkin', 'conflict', 'payoff',
    'localizationLevel', 'localizationJudgment', 'mismatch',
]
_HUMAN_OR_EVIDENCE_FIELDS = ['openingSummary', 'openingType', 'payEpisode', 'paywallSummary', 'paywallType']


def _copy_records(records: list[dict]) -> list[dict]:
    return json.loads(json.dumps(records, ensure_ascii=False))


def _stable_id(platform: str, title: str, normalize_title) -> str:
    norm = normalize_title(title) or title.casefold().strip()
    digest = hashlib.sha1(norm.encode('utf-8')).hexdigest()[:12]
    slug = ''.join(ch.lower() if ch.isalnum() else '-' for ch in platform).strip('-') or 'platform'
    return f'auto-{slug}-{digest}'


def _audit_norm_title(value: object) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(value or '').casefold())


def _content_tags_and_badges(values) -> tuple[list[str], list[str]]:
    """Separate semantic content tags from ranking/status badges misread from screenshots."""
    if isinstance(values, str):
        raw_values = [x.strip() for x in re.split(r'[,，;；|]', values) if x.strip()]
    else:
        raw_values = [str(x).strip() for x in (values or []) if str(x).strip()]
    tags: list[str] = []
    badges: list[str] = []
    for value in raw_values:
        if any(pattern.fullmatch(value) for pattern in _RANKING_BADGE_PATTERNS):
            badges.append(value)
        else:
            tags.append(value)
    return tags, badges


def _build_data_audit(records: list[dict]) -> dict:
    """Deterministic audit over the exact records returned by /api/data.

    This intentionally does no model inference. It only reports structural completeness,
    taxonomy conflicts, badge contamination, duplicate platform-title rows and evidence coverage.
    """
    missing_core = []
    invalid_genre = []
    invalid_audience = []
    badge_contamination = []
    human_or_evidence_blanks = Counter()
    evidence_count = 0
    research_status = Counter()
    by_platform_title: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_title: dict[str, list[dict]] = defaultdict(list)

    for record in records:
        rid = str(record.get('id') or '')
        title = str(record.get('title') or '').strip()
        app = str(record.get('app') or '').strip()
        missing = [field for field in _AUTO_CORE_FIELDS if not str(record.get(field) or '').strip()]
        if missing:
            missing_core.append({'id': rid, 'title': title, 'app': app, 'missing': missing})

        genre = str(record.get('genre') or '').strip()
        if genre and genre not in _CONTROLLED_GENRES:
            invalid_genre.append({'id': rid, 'title': title, 'app': app, 'value': genre})

        audience = str(record.get('audience') or '').strip()
        if audience and audience not in _CONTROLLED_AUDIENCES:
            invalid_audience.append({'id': rid, 'title': title, 'app': app, 'value': audience})

        _, stray_badges = _content_tags_and_badges(record.get('tags') or '')
        if stray_badges:
            badge_contamination.append({'id': rid, 'title': title, 'app': app, 'badges': stray_badges})

        for field in _HUMAN_OR_EVIDENCE_FIELDS:
            if not str(record.get(field) or '').strip():
                human_or_evidence_blanks[field] += 1

        sources = record.get('researchSources')
        if isinstance(sources, list) and any(str(x).strip() for x in sources):
            evidence_count += 1
        status = str(record.get('researchStatus') or '').strip() or '未标记'
        research_status[status] += 1

        norm = _audit_norm_title(title)
        if norm:
            by_platform_title[(app, norm)].append(record)
            by_title[norm].append(record)

    duplicate_platform_title = []
    for (app, _), group in by_platform_title.items():
        if len(group) > 1:
            duplicate_platform_title.append({
                'app': app,
                'title': str(group[0].get('title') or ''),
                'ids': [str(x.get('id') or '') for x in group],
                'count': len(group),
            })

    same_title_multi_platform = []
    for _, group in by_title.items():
        apps = sorted({str(x.get('app') or '') for x in group if str(x.get('app') or '')})
        if len(apps) > 1:
            same_title_multi_platform.append({
                'title': str(group[0].get('title') or ''),
                'apps': apps,
                'ids': [str(x.get('id') or '') for x in group],
            })

    missing_core.sort(key=lambda x: (x['app'], x['title']))
    invalid_genre.sort(key=lambda x: (x['app'], x['title']))
    invalid_audience.sort(key=lambda x: (x['app'], x['title']))
    badge_contamination.sort(key=lambda x: (x['app'], x['title']))
    duplicate_platform_title.sort(key=lambda x: (x['app'], x['title']))
    same_title_multi_platform.sort(key=lambda x: x['title'])

    return {
        'recordRows': len(records),
        'platformTitleUnique': len(by_platform_title),
        'missingAutoCoreCount': len(missing_core),
        'missingAutoCore': missing_core,
        'invalidGenreCount': len(invalid_genre),
        'invalidGenre': invalid_genre,
        'invalidAudienceCount': len(invalid_audience),
        'invalidAudience': invalid_audience,
        'rankingBadgeContaminationCount': len(badge_contamination),
        'rankingBadgeContamination': badge_contamination,
        'duplicatePlatformTitleCount': len(duplicate_platform_title),
        'duplicatePlatformTitle': duplicate_platform_title,
        'sameTitleMultiPlatformCount': len(same_title_multi_platform),
        'sameTitleMultiPlatform': same_title_multi_platform,
        'recordsWithResearchSources': evidence_count,
        'researchStatusCounts': dict(sorted(research_status.items())),
        'humanOrEvidenceBlankCounts': dict(human_or_evidence_blanks),
        'controlledGenreValues': sorted(_CONTROLLED_GENRES),
        'controlledAudienceValues': sorted(_CONTROLLED_AUDIENCES),
    }


def _latest_complete_runs(connect) -> list[dict]:
    latest: dict[tuple[str, str], dict] = {}
    with connect() as c:
        rows = c.execute(
            "SELECT id,collection_date,platform,status,result_json,updated_at FROM analysis_runs ORDER BY updated_at ASC"
        ).fetchall()
    for row in rows:
        try:
            result = json.loads(row['result_json'] or '{}')
        except (TypeError, json.JSONDecodeError):
            continue
        parsed_rows = result.get('rows') if isinstance(result.get('rows'), list) else []
        if not result.get('batchComplete') or len(parsed_rows) != 10:
            continue
        key = (str(row['collection_date'] or ''), str(row['platform'] or ''))
        latest[key] = {
            'id': str(row['id'] or ''),
            'collection_date': key[0],
            'platform': key[1],
            'status': str(row['status'] or ''),
            'updated_at': str(row['updated_at'] or ''),
            'result': result,
        }
    return [latest[k] for k in sorted(latest)]


def merge_analysis_records(base_records: list[dict], connect, normalize_title, split_lane) -> list[dict]:
    """Publish screenshot-confirmed facts immediately, independent of deep research completion.

    The screenshot model may emit provisional genre/lane/audience classifications. Those are
    deliberately NOT promoted into official research fields for a brand-new title. They remain
    available in analysis_runs for audit, while the public record stays blank until inherited
    historical research or the deep-research worker supplies controlled fields.
    """
    records = _copy_records(base_records)
    by_platform_title: dict[tuple[str, str], dict] = {}
    by_title: dict[str, dict] = {}
    for record in records:
        norm = normalize_title(record.get('title'))
        if norm:
            by_platform_title[(str(record.get('app') or ''), norm)] = record
            by_title.setdefault(norm, record)

    research_fields = [
        'synopsis', 'genre', 'lane', 'audience', 'storyCore', 'storySkin', 'conflict', 'payoff',
        'openingSummary', 'openingType', 'payEpisode', 'paywallSummary', 'paywallType',
        'localizationLevel', 'localizationJudgment', 'mismatch',
    ]

    for run in _latest_complete_runs(connect):
        date = run['collection_date']
        platform = run['platform']
        result = run['result']
        for item in result.get('rows') or []:
            title = str(item.get('title') or '').strip()
            if not title:
                continue
            try:
                rank = int(item.get('rank'))
            except (TypeError, ValueError):
                continue
            if not 1 <= rank <= 10:
                continue
            norm = normalize_title(title)
            if not norm:
                continue

            content_tags, ranking_badges = _content_tags_and_badges(item.get('tags') or [])
            clean_tags = ', '.join(content_tags)

            record = by_platform_title.get((platform, norm))
            if record is None:
                # If the same title was researched on another platform, inherit content research only;
                # keep the ranking observation as a separate platform-specific record.
                template = by_title.get(norm)
                inherited = {field: (template.get(field, '') if template else '') for field in research_fields}
                record = {
                    'id': _stable_id(platform, title, normalize_title),
                    'title': title,
                    'app': platform,
                    'date': date,
                    'rank': rank,
                    'heat': str(item.get('heat') or '').strip(),
                    'daysOnChart': 0,
                    'recordedDates': [],
                    'highestRank': rank,
                    'firstDate': date,
                    'lastDate': date,
                    'tags': clean_tags,
                    'rankingBadges': ranking_badges,
                    **inherited,
                    'history': [],
                    'platformMetrics': {},
                }
                records.append(record)
                by_platform_title[(platform, norm)] = record
                by_title.setdefault(norm, record)

            metrics = item.get('metrics') if isinstance(item.get('metrics'), dict) else {}
            event = {
                'date': date,
                'app': platform,
                'rank': rank,
                'heat': str(item.get('heat') or '').strip(),
                'tags': clean_tags,
                'rankingBadges': ranking_badges,
                'metrics': {k: str(metrics.get(k) or '').strip() for k in ('collect', 'like', 'followers') if str(metrics.get(k) or '').strip()},
                'source': f"analysis_run:{run['id']}",
            }
            history = [
                h for h in (record.get('history') or [])
                if not (str(h.get('date') or '') == date and str(h.get('app') or record.get('app') or '') == platform)
            ]
            history.append(event)
            history.sort(key=lambda h: (str(h.get('date') or ''), str(h.get('app') or ''), int(h.get('rank') or 999)))
            dates = sorted({str(h.get('date')) for h in history if h.get('date')})
            ranks = [int(h.get('rank')) for h in history if h.get('rank') not in (None, '')]
            latest_event = max(history, key=lambda h: (str(h.get('date') or ''), str(h.get('app') or '')))

            pending = [str(x).strip() for x in (item.get('pendingChecks') or []) if str(x).strip()]
            meaningful_pending = [x for x in pending if not (item.get('newness') == 'old' and x == '待深度研究')]
            needs_research = item.get('newness') in {'new', 'uncertain'} or bool(meaningful_pending)
            record.update({
                'app': latest_event.get('app') or platform,
                'date': latest_event.get('date') or date,
                'rank': int(latest_event.get('rank') or rank),
                'heat': str(latest_event.get('heat') or ''),
                'tags': str(latest_event.get('tags') or ''),
                'rankingBadges': latest_event.get('rankingBadges') if isinstance(latest_event.get('rankingBadges'), list) else [],
                'platformMetrics': latest_event.get('metrics') if isinstance(latest_event.get('metrics'), dict) else {},
                'history': history,
                'recordedDates': dates,
                'daysOnChart': len(dates),
                'firstDate': dates[0] if dates else date,
                'lastDate': dates[-1] if dates else date,
                'highestRank': min(ranks) if ranks else rank,
                'laneTerms': split_lane(record.get('lane')),
                'factStatus': 'FACT_READY',
                'factConfidence': str(item.get('confidence') or ''),
                'researchStatus': '待深研' if needs_research else (record.get('researchStatus') or '已研究'),
                'analysisRunId': run['id'],
                'newness': str(item.get('newness') or ''),
            })
    return records


def build_live_summary(records: list[dict], platform_order: list[str], clean, split_lane) -> dict:
    """Summary counts ranking observations, not just unique title records."""
    dates = sorted({str(h.get('date')) for r in records for h in (r.get('history') or []) if h.get('date')})
    latest = dates[-1] if dates else ''
    current: list[dict] = []
    for r in records:
        for h in (r.get('history') or []):
            if str(h.get('date') or '') != latest:
                continue
            x = dict(r)
            x.update({
                'date': latest,
                'app': h.get('app') or r.get('app', ''),
                'rank': h.get('rank', r.get('rank', 0)),
                'heat': h.get('heat') or '',
                'tags': h.get('tags', r.get('tags', '')),
                'rankingBadges': h.get('rankingBadges', r.get('rankingBadges', [])),
                'platformMetrics': h.get('metrics') or {},
            })
            current.append(x)

    platforms = [{'name': p, 'value': sum(1 for r in current if r.get('app') == p)} for p in platform_order]
    genres = Counter(clean(r.get('genre')) for r in current)
    genres.pop('', None); genres.pop('未采集', None)
    audiences = Counter(clean(r.get('audience')) for r in current)
    audiences.pop('', None); audiences.pop('未采集', None)
    lane_counts = Counter(t for r in current for t in split_lane(r.get('lane')))
    leaders = []
    for p in platform_order:
        xs = [r for r in current if r.get('app') == p]
        if xs:
            leaders.append(min(xs, key=lambda r: int(r.get('rank') or 999)))

    return {
        'collectionDate': latest,
        'dates': dates,
        'dateCount': len(dates),
        'totalRows': len(current),
        'uniqueTitles': len({r.get('title') for r in current if r.get('title')}),
        'totalUniqueTitles': len({(r.get('app'), r.get('title')) for r in records if r.get('title')}),
        'newTitles': len({r.get('title') for r in current if r.get('firstDate') == latest and r.get('title')}),
        'continuingTitles': len({r.get('title') for r in current if r.get('firstDate') != latest and r.get('title')}),
        'platforms': platforms,
        'genres': [{'name': k, 'value': v} for k, v in sorted(genres.items(), key=lambda x: (-x[1], x[0]))],
        'audiences': [{'name': k, 'value': v} for k, v in sorted(audiences.items(), key=lambda x: (-x[1], x[0]))],
        'lanes': [{'name': k, 'value': v} for k, v in sorted(lane_counts.items(), key=lambda x: (-x[1], x[0]))[:12]],
        'topByPlatform': leaders,
        'dateStats': [
            {
                'date': d,
                'rows': sum(1 for r in records for h in (r.get('history') or []) if h.get('date') == d),
                'uniqueTitles': len({r.get('title') for r in records if r.get('title') and any(h.get('date') == d for h in (r.get('history') or []))}),
            }
            for d in dates
        ],
        'audit': _build_data_audit(records),
    }
