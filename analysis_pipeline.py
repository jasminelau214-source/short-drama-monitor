from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

OPENAI_ENDPOINT = "https://api.openai.com/v1/responses"


def configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def model_name() -> str:
    return os.environ.get("OPENAI_MODEL", "gpt-5.6").strip() or "gpt-5.6"


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
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise RuntimeError("模型响应中没有结构化文本结果")


def analyze_batch(*, collection_date: str, platform: str, image_rows: list[dict], known_titles: list[str]) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY_NOT_CONFIGURED")
    if not image_rows:
        raise RuntimeError("没有可分析的截图")

    known = "\n".join(f"- {x}" for x in known_titles[:500])
    instructions = f"""
你是海外短剧市场监测系统的事实抽取 + 深度研究分析器。
采集日期：{collection_date}
平台：{platform}

必须遵守：
1. 排名、剧名、热度、平台标签、collect/like/followers 等事实字段，只能来自所附截图；禁止用搜索结果覆盖截图事实。
2. 多张截图可能重叠。合并、去重，只保留 rank 1-10。截图看不到的排名不要猜，写入 missingRanks。
3. 先与下方已有剧名库匹配。完全相同或明显只是标点/大小写差异的作品判定 old；确实没有匹配才判定 new；拿不准为 uncertain。
4. synopsis、genre、lane、audience、storyCore、storySkin、conflict、payoff、本土化等分析字段可使用 web search 做公开信息核验。优先官方平台页面；其次可信公开页面。
5. openingSummary/openingType、payEpisode/paywallSummary/paywallType 只有存在真实公开分集/付费证据时才填写；否则留空并写 pendingChecks，禁止推算。
6. 本土化单独判断：localizationLevel 建议用 A/B/C（可带 +/-）；localizationJudgment 写为什么；mismatch 写法律、职业、文化、年龄、consent、权力关系或世界观移植等明显不适配点。没有明显问题也要写“无明显硬伤”。
7. 不确定字段用空字符串并进入 pendingChecks。confidence 只反映该剧深度分析证据质量，不影响截图事实。
8. sources 输出用于深度字段的公开来源 URL；不要把截图签名链接写入 sources。

已有剧名库：
{known}
""".strip()

    content = [{"type": "input_text", "text": instructions}]
    for row in image_rows:
        raw = Path(row["storage_path"]).read_bytes()
        data_url = f"data:{row['mime_type']};base64,{base64.b64encode(raw).decode()}"
        content.append({"type": "input_image", "image_url": data_url, "detail": "high"})

    body = {
        "model": model_name(),
        "tools": [{"type": "web_search"}],
        "input": [{"role": "user", "content": content}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "short_drama_batch",
                "schema": _schema(),
                "strict": True,
            }
        },
    }
    request = urllib.request.Request(
        OPENAI_ENDPOINT,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:4000]
        raise RuntimeError(f"OPENAI_HTTP_{exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OPENAI_NETWORK_ERROR: {exc}") from exc

    result = json.loads(_response_text(payload))
    result["model"] = model_name()
    result["responseId"] = payload.get("id", "")
    return result
