from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from analysis_pipeline import _request as gemini_request, _response_text as gemini_response_text, model_name

TAVILY_SEARCH_URL = 'https://api.tavily.com/search'

PLATFORM_DOMAINS = {
    'ReelShort': ['reelshort.com'],
    'MoboReels': ['moboreels.com'],
    'NetShort': ['netshort.com'],
    'DramaWave': ['mydramawave.com', 'dramawave.tech'],
}

CORE_FIELDS = [
    'synopsis','genre','lane','audience','storyCore','storySkin','conflict','payoff',
    'localizationLevel','localizationJudgment','mismatch',
]
OPTIONAL_FIELDS = ['openingSummary','openingType','payEpisode','paywallSummary','paywallType']


def configured() -> bool:
    return bool(os.environ.get('TAVILY_API_KEY', '').strip() and os.environ.get('GEMINI_API_KEY', '').strip())


def _json_post(url: str, body: dict, *, auth: str = '', timeout: int = 60) -> dict:
    headers = {'Content-Type': 'application/json'}
    if auth:
        headers['Authorization'] = f'Bearer {auth}'
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    waits = [2, 5, 10]
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')[:2500]
            if (exc.code == 429 or 500 <= exc.code <= 599) and attempt < 3:
                time.sleep(waits[attempt])
                continue
            raise RuntimeError(f'TAVILY_HTTP_{exc.code}: {detail}') from exc
        except urllib.error.URLError as exc:
            if attempt < 3:
                time.sleep(waits[attempt])
                continue
            raise RuntimeError(f'TAVILY_NETWORK_ERROR: {exc}') from exc
    raise RuntimeError('TAVILY_REQUEST_FAILED')


def _search_once(query: str, *, include_domains: list[str] | None = None, max_results: int = 5) -> dict:
    key = os.environ.get('TAVILY_API_KEY', '').strip()
    if not key:
        raise RuntimeError('TAVILY_API_KEY_NOT_CONFIGURED')
    body = {
        'query': query,
        'topic': 'general',
        'search_depth': 'basic',
        'max_results': max(1, min(int(max_results), 8)),
        'include_answer': False,
        'include_raw_content': 'markdown',
        'include_images': False,
    }
    if include_domains:
        body['include_domains'] = include_domains[:8]
    return _json_post(TAVILY_SEARCH_URL, body, auth=key, timeout=75)


def _clean_result(item: dict, platform: str) -> dict:
    url = str(item.get('url') or '').strip()
    title = str(item.get('title') or '').strip()
    content = str(item.get('raw_content') or item.get('content') or '').strip()
    if len(content) > 14000:
        content = content[:14000]
    score = item.get('score')
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0
    host = ''
    try:
        host = urllib.parse.urlparse(url).hostname or ''
    except Exception:
        pass
    official = any(host == d or host.endswith('.' + d) for d in PLATFORM_DOMAINS.get(platform, []))
    return {
        'title': title,
        'url': url,
        'content': content,
        'score': score,
        'official': official,
    }


def discover_sources(title: str, platform: str) -> tuple[list[dict], dict]:
    """Spend one basic search first; spend a second only if the first is weak."""
    query = f'"{title}" {platform} short drama synopsis episodes'
    first = _search_once(query, max_results=6)
    results = [_clean_result(x, platform) for x in (first.get('results') or []) if isinstance(x, dict)]
    results = [x for x in results if x['url']]

    title_norm = re.sub(r'[^a-z0-9]+', '', title.casefold())
    def relevant(x: dict) -> bool:
        hay = re.sub(r'[^a-z0-9]+', '', (x['title'] + ' ' + x['content'][:1200]).casefold())
        return bool(title_norm and (title_norm in hay or x['score'] >= 0.45))

    useful = [x for x in results if relevant(x)]
    searches = 1
    if not useful:
        second = _search_once(f'"{title}" short drama', max_results=6)
        searches += 1
        seen = {x['url'] for x in results}
        for raw in second.get('results') or []:
            if not isinstance(raw, dict):
                continue
            x = _clean_result(raw, platform)
            if x['url'] and x['url'] not in seen:
                results.append(x); seen.add(x['url'])
        useful = [x for x in results if relevant(x)]

    # Official and high relevance first. Keep context bounded.
    useful.sort(key=lambda x: (not x['official'], -x['score']))
    selected = useful[:5]
    meta = {
        'searches': searches,
        'candidateCount': len(results),
        'selectedCount': len(selected),
        'officialCount': sum(1 for x in selected if x['official']),
    }
    return selected, meta


def _schema() -> dict:
    props = {name: {'type': 'string'} for name in CORE_FIELDS + OPTIONAL_FIELDS}
    props.update({
        'canonicalTitle': {'type': 'string'},
        'newnessResolution': {'type': 'string', 'enum': ['new','old','uncertain']},
        'confidence': {'type': 'string', 'enum': ['high','medium','low']},
        'missingFields': {'type': 'array', 'items': {'type': 'string'}},
        'auditNotes': {'type': 'array', 'items': {'type': 'string'}},
        'sourceUrls': {'type': 'array', 'items': {'type': 'string'}},
        'needsGPT': {'type': 'boolean'},
    })
    return {
        'type': 'object',
        'properties': props,
        'required': list(props.keys()),
        'additionalProperties': False,
    }


def analyze_sources(*, task: dict, sources: list[dict]) -> dict:
    api_key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not api_key:
        raise RuntimeError('GEMINI_API_KEY_NOT_CONFIGURED')
    context = task.get('context_json') if isinstance(task.get('context_json'), dict) else {}
    source_blocks = []
    for idx, src in enumerate(sources, 1):
        source_blocks.append(
            f"SOURCE {idx}\nURL: {src['url']}\nOFFICIAL: {src['official']}\nTITLE: {src['title']}\nCONTENT:\n{src['content']}"
        )
    evidence = '\n\n'.join(source_blocks)
    prompt = f"""
你是海外短剧市场研究系统的深度研究与稽查引擎。
平台：{task.get('platform','')}
榜单剧名：{task.get('title','')}
榜单排名：{task.get('rank','')}
截图基础信息：{json.dumps(context, ensure_ascii=False)}

下面是搜索层返回的公开网页证据。你只能使用这些证据和上面的截图基础信息，不得使用模型记忆补充事实。

{evidence}

输出要求：
1. 优先官方平台/官方作品页；非官方来源只能作为补充，冲突时不得强行选边，写入 auditNotes。
2. synopsis 必须是有证据支持的中文剧情简介；无法确认就留空。
3. genre、lane、audience、storyCore、storySkin、conflict、payoff 可在证据基础上做分析性归纳，但不得创造不存在的剧情事件。
4. openingSummary/openingType/payEpisode/paywallSummary/paywallType 只有来源明确支持时才填；否则留空。
5. localizationLevel/localizationJudgment/mismatch 是市场分析字段，可根据人物关系、场景、社会制度、表达方式、题材包装与英语市场语境进行判断；必须区分“来源事实”和“分析判断”。
6. canonicalTitle 用证据中最可信的正式英文剧名。若证据表明榜单剧名只是标点/大小写/译名差异，可据此规范；不能确认则保留榜单剧名。
7. newnessResolution：若公开证据能证明是已有同一作品/别名则 old；明确新作品则 new；证据不足则 uncertain。
8. sourceUrls 只能填写本次提供的 URL。
9. confidence：有官方详情页且核心剧情可核实通常 high；多个可信来源一致可 medium/high；只有标题/低质量来源则 low。
10. needsGPT=true 的情况：无有效来源、来源冲突严重、关键剧情无法确认、需要更强跨来源判断。
11. missingFields 必须列出仍无法可靠填写的核心/可选字段。不确定宁可留空。
""".strip()
    body = {
        'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
        'generationConfig': {
            'temperature': 0.1,
            'responseMimeType': 'application/json',
            'responseJsonSchema': _schema(),
        },
    }
    payload = gemini_request(body, api_key)
    text = gemini_response_text(payload)
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'GEMINI_RESEARCH_INVALID_JSON: {text[:2000]}') from exc
    allowed_urls = {s['url'] for s in sources}
    result['sourceUrls'] = [u for u in (result.get('sourceUrls') or []) if u in allowed_urls]
    if not result['sourceUrls']:
        result['sourceUrls'] = [s['url'] for s in sources]
    return result


def research_task(task: dict) -> dict:
    sources, search_meta = discover_sources(str(task.get('title') or ''), str(task.get('platform') or ''))
    if not sources:
        return {
            'status': 'NEEDS_GPT',
            'research': {},
            'sources': [],
            'confidence': 'low',
            'missingFields': list(task.get('missing_fields') or CORE_FIELDS),
            'error': 'Tavily 未找到可用的公开来源。',
            'searchMeta': search_meta,
        }
    result = analyze_sources(task=task, sources=sources)
    missing = [str(x) for x in (result.get('missingFields') or []) if str(x)]
    confidence = str(result.get('confidence') or 'low')
    needs_gpt = bool(result.get('needsGPT'))
    core_missing = [x for x in CORE_FIELDS if not str(result.get(x) or '').strip()]
    if len(core_missing) >= 6:
        needs_gpt = True
    status = 'NEEDS_GPT' if needs_gpt or confidence == 'low' else 'COMPLETE'
    source_rows = [
        {'url': s['url'], 'title': s['title'], 'official': s['official'], 'score': s['score']}
        for s in sources
    ]
    result['searchMeta'] = search_meta
    return {
        'status': status,
        'research': result,
        'sources': source_rows,
        'confidence': confidence,
        'missingFields': sorted(set(missing + core_missing)),
        'error': '',
        'searchMeta': search_meta,
    }
