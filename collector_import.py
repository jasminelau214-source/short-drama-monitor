from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone


MAX_TOP_N = 100
ALLOWED_SOURCE_TYPES = {
    'SHORT_DRAMA_APP',
    'VIDEO_SOCIAL',
    'DATA_MONITORING',
    'APP_STORE',
    'SEARCH_TREND',
    'OFFICIAL_WEB',
}
ALLOWED_COLLECTION_METHODS = {
    'API',
    'WEB_SCRAPE',
    'APP_AUTOMATION',
    'MANUAL_SCREENSHOT',
    'IMPORT',
    'APP_UI_XML',
    'APP_UI_XML_SCROLL',
}
SOURCE_TYPE_PREFIX = {
    'SHORT_DRAMA_APP': 'shortapp',
    'VIDEO_SOCIAL': 'videosocial',
    'DATA_MONITORING': 'datamonitoring',
    'APP_STORE': 'appstore',
    'SEARCH_TREND': 'searchtrend',
    'OFFICIAL_WEB': 'officialweb',
}


class CollectorImportError(ValueError):
    pass


def _clean(value, limit=6000):
    return str(value or '').strip()[:limit]


def _list_text(value, limit=100):
    if isinstance(value, str):
        items = re.split(r'[,，;；|]', value)
    elif isinstance(value, list):
        items = value
    else:
        items = []
    out = []
    seen = set()
    for item in items:
        text = _clean(item, limit)
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        out.append(text)
    return out


def _norm_title(value):
    """Normalize drama identity while removing non-title release labels.

    Only anchored dub/release markers are stripped. This intentionally avoids
    fuzzy aliasing so genuinely different titles are never merged by guesswork.
    """
    text = str(value or '').casefold().strip()
    marker = r'(?:eng(?:lish)?\s*)?dub(?:bed)?'
    text = re.sub(rf'^\s*[\[(]?\s*{marker}\s*[\])]?(?:\s*[:|–—-]\s*)*', '', text)
    text = re.sub(rf'(?:\s*[:|–—-]\s*)*[\[(]?\s*{marker}\s*[\])]?\s*$', '', text)
    return re.sub(r'[^a-z0-9]+', '', text)

def _bool_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {'1', 'true', 'yes', 'y', 'pass'}
    return bool(value)


def _iso_date(value):
    text = _clean(value, 20)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        raise CollectorImportError('collection_date 必须为 YYYY-MM-DD')
    try:
        datetime.strptime(text, '%Y-%m-%d')
    except ValueError as exc:
        raise CollectorImportError('collection_date 不是有效日期') from exc
    return text


def _slug(value, limit=80):
    text = re.sub(r'[^a-z0-9]+', '_', str(value or '').casefold()).strip('_')
    return text[:limit]


def _int_top_n(value, fallback):
    raw = value if value not in (None, '') else fallback
    try:
        top_n = int(raw)
    except (TypeError, ValueError) as exc:
        raise CollectorImportError(f'无效 top_n：{raw!r}') from exc
    if not 1 <= top_n <= MAX_TOP_N:
        raise CollectorImportError(f'top_n 必须在 1-{MAX_TOP_N}：{top_n}')
    return top_n


def _int_rank(value, top_n):
    try:
        rank = int(value)
    except (TypeError, ValueError) as exc:
        raise CollectorImportError(f'无效榜位：{value!r}') from exc
    if not 1 <= rank <= top_n:
        raise CollectorImportError(f'榜位必须在 1-{top_n}：{rank}')
    return rank


def _default_source_id(platform, source_type):
    prefix = SOURCE_TYPE_PREFIX.get(source_type, 'source')
    slug = _slug(platform)
    if not slug:
        raise CollectorImportError('platform 无法生成 source_id')
    return f'{prefix}_{slug}'


def _clean_metrics(raw):
    metrics = {}
    raw_metrics = raw.get('metrics') if isinstance(raw, dict) else None
    if isinstance(raw_metrics, dict):
        for key, value in raw_metrics.items():
            k = _slug(key, 60)
            v = _clean(value, 200)
            if k and v:
                metrics[k] = v
    for source_key, metric_key in (
        ('followers', 'followers'),
        ('collect', 'collect'),
        ('like', 'like'),
        ('views', 'views'),
        ('plays', 'plays'),
        ('comments', 'comments'),
        ('shares', 'shares'),
    ):
        value = _clean(raw.get(source_key), 200)
        if value:
            metrics[metric_key] = value
    return metrics


def validate_and_normalize(payload: dict, known_titles: set[str] | list[str]) -> dict:
    if not isinstance(payload, dict):
        raise CollectorImportError('collector JSON 必须是对象')

    platform = _clean(payload.get('platform'), 80)
    if not platform:
        raise CollectorImportError('platform 不能为空')

    source_type = _clean(payload.get('source_type'), 80) or 'SHORT_DRAMA_APP'
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise CollectorImportError(f'不支持的 source_type：{source_type}')

    collection_method = _clean(payload.get('collection_method'), 100) or 'IMPORT'
    if collection_method not in ALLOWED_COLLECTION_METHODS:
        raise CollectorImportError(f'不支持的 collection_method：{collection_method}')

    collection_date = _iso_date(payload.get('collection_date'))
    if not _bool_value(payload.get('batch_complete')):
        raise CollectorImportError('collector 未通过本地审计：batch_complete=false')

    raw_rows = payload.get('rows')
    if not isinstance(raw_rows, list) or not raw_rows:
        raise CollectorImportError('rows 必须是非空数组')

    top_n = _int_top_n(payload.get('top_n'), len(raw_rows))
    if len(raw_rows) != top_n:
        raise CollectorImportError(f'必须完整提供 Top{top_n}，当前 {len(raw_rows)} 行')

    source_id = _clean(payload.get('source_id'), 120) or _default_source_id(platform, source_type)
    target_key = _clean(payload.get('target_key'), 120) or 'daily_top_all'
    ranking_type = _clean(payload.get('ranking_type'), 120)
    category = _clean(payload.get('category'), 120) or 'All'

    known_norm = {_norm_title(x) for x in known_titles if _norm_title(x)}
    seen_ranks = set()
    seen_titles = set()
    rows = []
    new_titles = []

    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise CollectorImportError('rows 中存在非对象行')

        rank = _int_rank(raw.get('rank'), top_n)
        title = _clean(raw.get('title'), 500)
        norm = _norm_title(title)
        if not title or not norm:
            raise CollectorImportError(f'#{rank} 标题为空或无法规范化')
        if rank in seen_ranks:
            raise CollectorImportError(f'重复榜位：#{rank}')
        if norm in seen_titles:
            raise CollectorImportError(f'重复标题：{title}')

        seen_ranks.add(rank)
        seen_titles.add(norm)
        tags = _list_text(raw.get('tags'), 120)
        badges = _list_text(raw.get('badges'), 120)
        metrics = _clean_metrics(raw)

        if source_type == 'SHORT_DRAMA_APP':
            is_old = norm in known_norm
            newness = 'old' if is_old else 'new'
            if not is_old:
                new_titles.append(title)
            pending_checks = [] if is_old else ['待深度研究']
        else:
            # Secondary evidence layers are observations, not authoritative "new drama" signals.
            # This keeps web/social/data-monitoring imports from triggering the App deep-research queue.
            is_old = norm in known_norm
            newness = 'observed'
            pending_checks = []

        item = {
            'rank': rank,
            'title': title,
            'heat': _clean(raw.get('heat'), 100),
            'tags': tags,
            'rankingBadges': badges,
            'metrics': metrics,
            'newness': newness,
            'pendingChecks': pending_checks,
            'confidence': _clean(raw.get('confidence'), 80) or 'collector-verified',
        }

        source_synopsis = _clean(raw.get('synopsis'), 3000)
        if source_synopsis:
            item['sourceSynopsis'] = source_synopsis
        source_url = _clean(raw.get('source_url') or raw.get('sourceUrl'), 1200)
        if source_url:
            item['sourceUrl'] = source_url
        episode_url = _clean(raw.get('episode_url') or raw.get('episodeUrl'), 1200)
        if episode_url:
            item['episodeUrl'] = episode_url
        rows.append(item)

    expected_ranks = set(range(1, top_n + 1))
    if seen_ranks != expected_ranks:
        missing = sorted(expected_ranks - seen_ranks)
        raise CollectorImportError(f'Top{top_n} 榜位不完整，缺失：{missing}')

    rows.sort(key=lambda x: x['rank'])
    core = {
        'sourceId': source_id,
        'targetKey': target_key,
        'platform': platform,
        'collectionDate': collection_date,
        'topN': top_n,
        'rows': [
            {
                'rank': x['rank'],
                'title': x['title'],
                'heat': x['heat'],
                'tags': x['tags'],
                'metrics': x['metrics'],
                'sourceUrl': x.get('sourceUrl', ''),
            }
            for x in rows
        ],
    }
    digest = hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()[:20]

    run_platform = _slug(platform, 40) or 'platform'
    run_target = _slug(target_key, 40) or 'target'
    run_id = f'collector-{run_platform}-{run_target}-{collection_date.replace("-", "")}-{digest}'

    evidence = payload.get('evidence') if isinstance(payload.get('evidence'), dict) else {}
    collector_meta = {
        'sourceId': source_id,
        'targetKey': target_key,
        'sourceType': source_type,
        'rankingType': ranking_type,
        'category': category,
        'topN': top_n,
        'collectionMethod': collection_method,
        'collectorVersion': _clean(payload.get('collector_version'), 100),
        'collectedAt': _clean(payload.get('collected_at'), 80),
        'locale': _clean(payload.get('locale'), 40),
        'region': _clean(payload.get('region'), 80),
        'adbSerial': _clean(payload.get('adb_serial'), 100),
        'evidence': evidence,
        'evidencePersistence': _clean(payload.get('evidence_persistence'), 80) or 'LOCAL_ONLY',
        'localAudit': {
            'batchComplete': True,
            'missingRanks': payload.get('missing_ranks') if isinstance(payload.get('missing_ranks'), list) else [],
            'duplicateRanks': payload.get('duplicate_ranks') if isinstance(payload.get('duplicate_ranks'), list) else [],
            'duplicateTitles': payload.get('duplicate_titles') if isinstance(payload.get('duplicate_titles'), list) else [],
        },
    }

    result = {
        'batchComplete': True,
        'rows': rows,
        'collector': collector_meta,
        'importedAt': datetime.now(timezone.utc).isoformat(),
    }
    status = ('已采集' if source_type != 'SHORT_DRAMA_APP' else ('已识别-待深研' if new_titles else '已分析'))
    return {
        'runId': run_id,
        'collectionDate': collection_date,
        'platform': platform,
        'sourceId': source_id,
        'targetKey': target_key,
        'topN': top_n,
        'status': status,
        'result': result,
        'newTitles': new_titles,
        'newTitleCount': len(new_titles),
        'rowCount': len(rows),
        'model': 'collector-direct-v2',
        'provider': _clean(payload.get('provider'), 80) or 'collector-direct',
    }
, '', text)
    return re.sub(r'[^a-z0-9]+', '', text)


def _bool_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {'1', 'true', 'yes', 'y', 'pass'}
    return bool(value)


def _iso_date(value):
    text = _clean(value, 20)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        raise CollectorImportError('collection_date 必须为 YYYY-MM-DD')
    try:
        datetime.strptime(text, '%Y-%m-%d')
    except ValueError as exc:
        raise CollectorImportError('collection_date 不是有效日期') from exc
    return text


def _slug(value, limit=80):
    text = re.sub(r'[^a-z0-9]+', '_', str(value or '').casefold()).strip('_')
    return text[:limit]


def _int_top_n(value, fallback):
    raw = value if value not in (None, '') else fallback
    try:
        top_n = int(raw)
    except (TypeError, ValueError) as exc:
        raise CollectorImportError(f'无效 top_n：{raw!r}') from exc
    if not 1 <= top_n <= MAX_TOP_N:
        raise CollectorImportError(f'top_n 必须在 1-{MAX_TOP_N}：{top_n}')
    return top_n


def _int_rank(value, top_n):
    try:
        rank = int(value)
    except (TypeError, ValueError) as exc:
        raise CollectorImportError(f'无效榜位：{value!r}') from exc
    if not 1 <= rank <= top_n:
        raise CollectorImportError(f'榜位必须在 1-{top_n}：{rank}')
    return rank


def _default_source_id(platform, source_type):
    prefix = SOURCE_TYPE_PREFIX.get(source_type, 'source')
    slug = _slug(platform)
    if not slug:
        raise CollectorImportError('platform 无法生成 source_id')
    return f'{prefix}_{slug}'


def _clean_metrics(raw):
    metrics = {}
    raw_metrics = raw.get('metrics') if isinstance(raw, dict) else None
    if isinstance(raw_metrics, dict):
        for key, value in raw_metrics.items():
            k = _slug(key, 60)
            v = _clean(value, 200)
            if k and v:
                metrics[k] = v
    for source_key, metric_key in (
        ('followers', 'followers'),
        ('collect', 'collect'),
        ('like', 'like'),
        ('views', 'views'),
        ('plays', 'plays'),
        ('comments', 'comments'),
        ('shares', 'shares'),
    ):
        value = _clean(raw.get(source_key), 200)
        if value:
            metrics[metric_key] = value
    return metrics


def validate_and_normalize(payload: dict, known_titles: set[str] | list[str]) -> dict:
    if not isinstance(payload, dict):
        raise CollectorImportError('collector JSON 必须是对象')

    platform = _clean(payload.get('platform'), 80)
    if not platform:
        raise CollectorImportError('platform 不能为空')

    source_type = _clean(payload.get('source_type'), 80) or 'SHORT_DRAMA_APP'
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise CollectorImportError(f'不支持的 source_type：{source_type}')

    collection_method = _clean(payload.get('collection_method'), 100) or 'IMPORT'
    if collection_method not in ALLOWED_COLLECTION_METHODS:
        raise CollectorImportError(f'不支持的 collection_method：{collection_method}')

    collection_date = _iso_date(payload.get('collection_date'))
    if not _bool_value(payload.get('batch_complete')):
        raise CollectorImportError('collector 未通过本地审计：batch_complete=false')

    raw_rows = payload.get('rows')
    if not isinstance(raw_rows, list) or not raw_rows:
        raise CollectorImportError('rows 必须是非空数组')

    top_n = _int_top_n(payload.get('top_n'), len(raw_rows))
    if len(raw_rows) != top_n:
        raise CollectorImportError(f'必须完整提供 Top{top_n}，当前 {len(raw_rows)} 行')

    source_id = _clean(payload.get('source_id'), 120) or _default_source_id(platform, source_type)
    target_key = _clean(payload.get('target_key'), 120) or 'daily_top_all'
    ranking_type = _clean(payload.get('ranking_type'), 120)
    category = _clean(payload.get('category'), 120) or 'All'

    known_norm = {_norm_title(x) for x in known_titles if _norm_title(x)}
    seen_ranks = set()
    seen_titles = set()
    rows = []
    new_titles = []

    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise CollectorImportError('rows 中存在非对象行')

        rank = _int_rank(raw.get('rank'), top_n)
        title = _clean(raw.get('title'), 500)
        norm = _norm_title(title)
        if not title or not norm:
            raise CollectorImportError(f'#{rank} 标题为空或无法规范化')
        if rank in seen_ranks:
            raise CollectorImportError(f'重复榜位：#{rank}')
        if norm in seen_titles:
            raise CollectorImportError(f'重复标题：{title}')

        seen_ranks.add(rank)
        seen_titles.add(norm)
        tags = _list_text(raw.get('tags'), 120)
        badges = _list_text(raw.get('badges'), 120)
        metrics = _clean_metrics(raw)

        if source_type == 'SHORT_DRAMA_APP':
            is_old = norm in known_norm
            newness = 'old' if is_old else 'new'
            if not is_old:
                new_titles.append(title)
            pending_checks = [] if is_old else ['待深度研究']
        else:
            # Secondary evidence layers are observations, not authoritative "new drama" signals.
            # This keeps web/social/data-monitoring imports from triggering the App deep-research queue.
            is_old = norm in known_norm
            newness = 'observed'
            pending_checks = []

        item = {
            'rank': rank,
            'title': title,
            'heat': _clean(raw.get('heat'), 100),
            'tags': tags,
            'rankingBadges': badges,
            'metrics': metrics,
            'newness': newness,
            'pendingChecks': pending_checks,
            'confidence': _clean(raw.get('confidence'), 80) or 'collector-verified',
        }

        source_synopsis = _clean(raw.get('synopsis'), 3000)
        if source_synopsis:
            item['sourceSynopsis'] = source_synopsis
        source_url = _clean(raw.get('source_url') or raw.get('sourceUrl'), 1200)
        if source_url:
            item['sourceUrl'] = source_url
        episode_url = _clean(raw.get('episode_url') or raw.get('episodeUrl'), 1200)
        if episode_url:
            item['episodeUrl'] = episode_url
        rows.append(item)

    expected_ranks = set(range(1, top_n + 1))
    if seen_ranks != expected_ranks:
        missing = sorted(expected_ranks - seen_ranks)
        raise CollectorImportError(f'Top{top_n} 榜位不完整，缺失：{missing}')

    rows.sort(key=lambda x: x['rank'])
    core = {
        'sourceId': source_id,
        'targetKey': target_key,
        'platform': platform,
        'collectionDate': collection_date,
        'topN': top_n,
        'rows': [
            {
                'rank': x['rank'],
                'title': x['title'],
                'heat': x['heat'],
                'tags': x['tags'],
                'metrics': x['metrics'],
                'sourceUrl': x.get('sourceUrl', ''),
            }
            for x in rows
        ],
    }
    digest = hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()[:20]

    run_platform = _slug(platform, 40) or 'platform'
    run_target = _slug(target_key, 40) or 'target'
    run_id = f'collector-{run_platform}-{run_target}-{collection_date.replace("-", "")}-{digest}'

    evidence = payload.get('evidence') if isinstance(payload.get('evidence'), dict) else {}
    collector_meta = {
        'sourceId': source_id,
        'targetKey': target_key,
        'sourceType': source_type,
        'rankingType': ranking_type,
        'category': category,
        'topN': top_n,
        'collectionMethod': collection_method,
        'collectorVersion': _clean(payload.get('collector_version'), 100),
        'collectedAt': _clean(payload.get('collected_at'), 80),
        'locale': _clean(payload.get('locale'), 40),
        'region': _clean(payload.get('region'), 80),
        'adbSerial': _clean(payload.get('adb_serial'), 100),
        'evidence': evidence,
        'evidencePersistence': _clean(payload.get('evidence_persistence'), 80) or 'LOCAL_ONLY',
        'localAudit': {
            'batchComplete': True,
            'missingRanks': payload.get('missing_ranks') if isinstance(payload.get('missing_ranks'), list) else [],
            'duplicateRanks': payload.get('duplicate_ranks') if isinstance(payload.get('duplicate_ranks'), list) else [],
            'duplicateTitles': payload.get('duplicate_titles') if isinstance(payload.get('duplicate_titles'), list) else [],
        },
    }

    result = {
        'batchComplete': True,
        'rows': rows,
        'collector': collector_meta,
        'importedAt': datetime.now(timezone.utc).isoformat(),
    }
    status = ('已采集' if source_type != 'SHORT_DRAMA_APP' else ('已识别-待深研' if new_titles else '已分析'))
    return {
        'runId': run_id,
        'collectionDate': collection_date,
        'platform': platform,
        'sourceId': source_id,
        'targetKey': target_key,
        'topN': top_n,
        'status': status,
        'result': result,
        'newTitles': new_titles,
        'newTitleCount': len(new_titles),
        'rowCount': len(rows),
        'model': 'collector-direct-v2',
        'provider': _clean(payload.get('provider'), 80) or 'collector-direct',
    }
