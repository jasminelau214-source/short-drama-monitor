from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

SUPPORTED_PLATFORMS = {'NetShort', 'MoboReels'}
PLATFORM_SOURCE_IDS = {'NetShort': 'shortapp_netshort', 'MoboReels': 'shortapp_moboreels'}
PLATFORM_TARGET_KEYS = {'NetShort': 'daily_top_all', 'MoboReels': 'daily_top_all'}


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
    return re.sub(r'[^a-z0-9]+', '', str(value or '').casefold())


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


def _int_rank(value):
    try:
        rank = int(value)
    except (TypeError, ValueError) as exc:
        raise CollectorImportError(f'无效榜位：{value!r}') from exc
    if not 1 <= rank <= 10:
        raise CollectorImportError(f'榜位必须在 1-10：{rank}')
    return rank


def validate_and_normalize(payload: dict, known_titles: set[str] | list[str]) -> dict:
    if not isinstance(payload, dict):
        raise CollectorImportError('collector JSON 必须是对象')
    platform = _clean(payload.get('platform'), 40)
    if platform not in SUPPORTED_PLATFORMS:
        raise CollectorImportError(f'暂不支持的平台：{platform or "(空)"}')
    collection_date = _iso_date(payload.get('collection_date'))
    if not _bool_value(payload.get('batch_complete')):
        raise CollectorImportError('collector 未通过本地审计：batch_complete=false')
    raw_rows = payload.get('rows')
    if not isinstance(raw_rows, list) or len(raw_rows) != 10:
        raise CollectorImportError(f'必须完整提供 Top10，当前 {len(raw_rows) if isinstance(raw_rows, list) else 0} 行')

    known_norm = {_norm_title(x) for x in known_titles if _norm_title(x)}
    seen_ranks = set()
    seen_titles = set()
    rows = []
    new_titles = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise CollectorImportError('rows 中存在非对象行')
        rank = _int_rank(raw.get('rank'))
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
        metrics = {}
        for source_key, metric_key in (('followers','followers'),('collect','collect'),('like','like')):
            value = _clean(raw.get(source_key), 100)
            if value:
                metrics[metric_key] = value
        is_old = norm in known_norm
        newness = 'old' if is_old else 'new'
        if not is_old:
            new_titles.append(title)
        item = {
            'rank': rank,
            'title': title,
            'heat': _clean(raw.get('heat'), 100),
            'tags': tags,
            'rankingBadges': badges,
            'metrics': metrics,
            'newness': newness,
            'pendingChecks': [] if is_old else ['待深度研究'],
            'confidence': 'collector-verified',
        }
        source_synopsis = _clean(raw.get('synopsis'), 3000)
        if source_synopsis:
            item['sourceSynopsis'] = source_synopsis
        rows.append(item)

    if seen_ranks != set(range(1, 11)):
        missing = sorted(set(range(1, 11)) - seen_ranks)
        raise CollectorImportError(f'Top10 榜位不完整，缺失：{missing}')
    rows.sort(key=lambda x: x['rank'])
    core = {
        'platform': platform,
        'collectionDate': collection_date,
        'rows': [{'rank': x['rank'], 'title': x['title'], 'heat': x['heat'], 'tags': x['tags'], 'metrics': x['metrics']} for x in rows],
    }
    digest = hashlib.sha256(json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()[:20]
    run_id = f'collector-{platform.casefold()}-{collection_date.replace("-", "")}-{digest}'
    evidence = payload.get('evidence') if isinstance(payload.get('evidence'), dict) else {}
    collector_meta = {
        'sourceId': PLATFORM_SOURCE_IDS[platform],
        'targetKey': PLATFORM_TARGET_KEYS[platform],
        'sourceType': _clean(payload.get('source_type'), 80) or 'SHORT_DRAMA_APP',
        'rankingType': _clean(payload.get('ranking_type'), 100),
        'collectionMethod': _clean(payload.get('collection_method'), 100) or 'APP_UI_XML',
        'collectorVersion': _clean(payload.get('collector_version'), 100),
        'collectedAt': _clean(payload.get('collected_at'), 80),
        'adbSerial': _clean(payload.get('adb_serial'), 100),
        'evidence': evidence,
        'evidencePersistence': 'LOCAL_ONLY',
        'localAudit': {
            'batchComplete': True,
            'missingRanks': payload.get('missing_ranks') if isinstance(payload.get('missing_ranks'), list) else [],
            'duplicateRanks': payload.get('duplicate_ranks') if isinstance(payload.get('duplicate_ranks'), list) else [],
            'duplicateTitles': payload.get('duplicate_titles') if isinstance(payload.get('duplicate_titles'), list) else [],
        },
    }
    result = {'batchComplete': True, 'rows': rows, 'collector': collector_meta, 'importedAt': datetime.now(timezone.utc).isoformat()}
    status = '已识别-待深研' if new_titles else '已分析'
    return {
        'runId': run_id,
        'collectionDate': collection_date,
        'platform': platform,
        'status': status,
        'result': result,
        'newTitles': new_titles,
        'newTitleCount': len(new_titles),
        'rowCount': len(rows),
        'model': 'collector-direct-v1',
        'provider': 'app-ui-xml',
    }
