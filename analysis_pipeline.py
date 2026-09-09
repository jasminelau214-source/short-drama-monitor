from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def configured() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


def model_name() -> str:
    return os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip() or "gemini-3.5-flash-lite"


def provider_name() -> str:
    return "gemini"


def _schema() -> dict:
    row_props = {
        "rank": {"type": "integer", "minimum": 1, "maximum": 10},
        "title": {"type": "string"},
        "heat": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "metrics": {
            "type": "object",
            "properties": {
                "collect": {"type": "string"},
                "like": {"type": "string"},
                "followers": {"type": "string"},
            },
            "required": ["collect", "like", "followers"],
            "additionalProperties": False,
        },
        "newness": {"type": "string", "enum": ["old", "new", "uncertain"]},
        "matchedExistingTitle": {"type": "string"},
        "synopsis": {"type": "string"},
        "genre": {"type": "string"},
        "lane": {"type": "string"},
        "audience": {"type": "string"},
        "storyCore": {"type": "string"},
        "storySkin": {"type": "string"},
        "conflict": {"type": "string"},
        "payoff": {"type": "string"},
        "openingSummary": {"type": "string"},
        "openingType": {"type": "string"},
        "payEpisode": {"type": "string"},
        "paywallSummary": {"type": "string"},
        "paywallType": {"type": "string"},
        "localizationLevel": {"type": "string"},
        "localizationJudgment": {"type": "string"},
        "mismatch": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "pendingChecks": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}},
    }
    return {
        "type": "object",
        "properties": {
            "collectionDate": {"type": "string"},
            "platform": {"type": "string"},
            "batchComplete": {"type": "boolean"},
            "missingRanks": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 10}},
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": row_props,
                    "required": list(row_props.keys()),
                    "additionalProperties": False,
                },
            },
            "auditNotes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["collectionDate", "platform", "batchComplete", "missingRanks", "rows", "auditNotes"],
        "additionalProperties": False,
    }


def _response_text(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        feedback = payload.get("promptFeedback") or {}
        raise RuntimeError(f"Gemini 未返回候选结果: {json.dumps(feedback, ensure_ascii=False)[:1000]}")
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    texts = [p.get("text", "") for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)]
    text = "\n".join(x for x in texts if x).strip()
    if not text:
        raise RuntimeError("Gemini 响应中没有结构化文本结果")
    return text


def _request(body: dict, api_key: str) -> dict:
    endpoint = GEMINI_ENDPOINT.format(model=model_name())
    waits = [2, 5, 10]
    for attempt in range(4):
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:4000]
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if retryable and attempt < 3:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = max(float(retry_after), waits[attempt]) if retry_after else waits[attempt]
                except (TypeError, ValueError):
                    delay = waits[attempt]
                time.sleep(delay)
                continue
            raise RuntimeError(f"GEMINI_HTTP_{exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            if attempt < 3:
                time.sleep(waits[attempt])
                continue
            raise RuntimeError(f"GEMINI_NETWORK_ERROR: {exc}") from exc
    raise RuntimeError("GEMINI_REQUEST_FAILED")


def _normalize_result(result: dict, *, collection_date: str, platform: str) -> dict:
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    by_rank: dict[int, dict] = {}
    audit = list(result.get("auditNotes") or [])
    for item in rows:
        if not isinstance(item, dict):
            continue
        try:
            rank = int(item.get("rank"))
        except (TypeError, ValueError):
            audit.append("存在无法解析的排名行，已丢弃。")
            continue
        if not 1 <= rank <= 10:
            audit.append(f"检测到 Top10 之外的排名 {rank}，已丢弃。")
            continue
        if rank in by_rank:
            audit.append(f"排名 {rank} 重复，程序仅保留第一条并进入稽查。")
            continue
        item["rank"] = rank
        item["title"] = str(item.get("title") or "").strip()
        item["heat"] = str(item.get("heat") or "").strip()
        item["tags"] = [str(x).strip() for x in (item.get("tags") or []) if str(x).strip()]
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        item["metrics"] = {k: str(metrics.get(k) or "").strip() for k in ("collect", "like", "followers")}
        item["sources"] = []
        item["pendingChecks"] = [str(x).strip() for x in (item.get("pendingChecks") or []) if str(x).strip()]
        if item.get("newness") in {"new", "uncertain"} and "待深度研究" not in item["pendingChecks"]:
            item["pendingChecks"].append("待深度研究")
        by_rank[rank] = item
    final_rows = [by_rank[x] for x in sorted(by_rank)]
    missing = [x for x in range(1, 11) if x not in by_rank]
    if missing:
        audit.append("Top10 不完整，缺失排名：" + ",".join(map(str, missing)))
    titles = [str(x.get("title") or "").casefold().strip() for x in final_rows if str(x.get("title") or "").strip()]
    if len(titles) != len(set(titles)):
        audit.append("检测到重复剧名，禁止自动正式入库，需人工复核。")
    result["collectionDate"] = collection_date
    result["platform"] = platform
    result["rows"] = final_rows
    result["missingRanks"] = missing
    result["batchComplete"] = len(final_rows) == 10 and not missing and len(titles) == len(set(titles))
    result["auditNotes"] = audit
    result["provider"] = provider_name()
    result["model"] = model_name()
    return result


def analyze_batch(*, collection_date: str, platform: str, image_rows: list[dict], known_titles: list[str]) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY_NOT_CONFIGURED")
    if not image_rows:
        raise RuntimeError("没有可分析的截图")

    known = "\n".join(f"- {x}" for x in known_titles[:1000])
    instructions = f"""
你是海外短剧市场监测系统的“截图事实抽取 + 基础分类”引擎。
采集日期：{collection_date}
平台：{platform}

必须遵守：
1. rank、title、heat、tags、collect/like/followers 等事实字段只能来自所附截图。禁止根据常识、模型记忆或猜测补事实。
2. 多张截图可能重叠。合并、去重，只保留 rank 1-10。截图看不到的排名不要猜。
3. 先与已有剧名库匹配。完全相同或明显只是大小写、标点差异的作品标记 old；确实没有匹配才标记 new；拿不准标记 uncertain。
4. 本调用没有联网研究能力。禁止编造官方简介、剧情细节、首集内容或付费卡点。
5. 对 old 剧：可以做基础题材/赛道/受众判断，但后端会优先继承系统里已有研究数据。
6. 对 new/uncertain 剧：仅依据标题、海报和截图标签做“基础分类”；synopsis、storyCore、conflict、payoff、本土化等若缺乏直接证据应留空，并把“待深度研究”写入 pendingChecks。
7. openingSummary/openingType、payEpisode/paywallSummary/paywallType 没有直接证据必须留空。
8. sources 必须为空数组，因为本调用不做外部搜索。
9. confidence 反映截图识别和基础分类可信度；看不清时用 low，并写 pendingChecks。
10. 不确定宁可留空，绝不为了填满字段而推测。

已有剧名库：
{known}
""".strip()

    parts: list[dict] = [{"text": instructions}]
    for row in image_rows:
        raw = Path(row["storage_path"]).read_bytes()
        parts.append({
            "inlineData": {
                "mimeType": row["mime_type"],
                "data": base64.b64encode(raw).decode("ascii"),
            }
        })

    # Gemini 3.5 Flash-Lite currently rejects the newer responseFormat REST shape
    # even though model docs list structured-output support. Use the stable
    # GenerateContent JSON-mode fields instead: responseMimeType + responseJsonSchema.
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "responseJsonSchema": _schema(),
        },
    }
    payload = _request(body, api_key)
    raw_text = _response_text(payload)
    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GEMINI_INVALID_JSON: {raw_text[:2000]}") from exc
    result = _normalize_result(result, collection_date=collection_date, platform=platform)
    usage = payload.get("usageMetadata") or {}
    result["usage"] = {
        "promptTokens": usage.get("promptTokenCount", 0),
        "outputTokens": usage.get("candidatesTokenCount", 0),
        "totalTokens": usage.get("totalTokenCount", 0),
    }
    return result
