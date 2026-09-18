from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
RESULT_ROOT = ROOT / "pilot_expansion" / "FreeReels" / "results"
EVIDENCE_ROOT = ROOT / "pilot_expansion" / "evidence"
ACCEPTANCE_ROOT = ROOT / "pilot_expansion" / "acceptance"
TZ = ZoneInfo("Asia/Shanghai")

OFFICIAL_URLS = [
    "https://free-reels.com/",
    "https://www.free-reels.com/",
]

TITLE_KEYS = (
    "title", "name", "bookName", "book_name", "dramaName", "drama_name",
    "seriesName", "series_name", "bookTitle", "book_title", "videoTitle", "video_title",
)
RANK_KEYS = ("rank", "ranking", "position", "rankNo", "rank_no", "rankingNo", "ranking_no")
SEMANTIC_TERMS = ("ranking", "rank", "top", "trending", "popular", "hot", "chart")
RECOMMENDATION_TERMS = ("recommend", "recommendation", "personalized", "for_you", "foryou", "feed")
FIELD_GROUPS = {
    "title": TITLE_KEYS,
    "synopsis": ("synopsis", "introduction", "description", "summary", "intro"),
    "tags": ("tag", "tags", "genre", "genres", "category", "categories"),
    "views": ("view", "views", "viewCount", "view_count", "playCount", "play_count"),
    "likes": ("like", "likes", "likeCount", "like_count"),
    "favorites": ("favorite", "favorites", "favourite", "collect", "collection", "bookmark"),
    "episodes": ("episode", "episodes", "episodeCount", "episode_count", "chapterCount", "chapter_count"),
    "rating": ("rating", "ratings", "score"),
}


def clean(value: Any, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def business_date() -> str:
    return datetime.now(TZ).date().isoformat()


def safe_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    except Exception:
        return clean(url, 1000)


def is_official_host(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").casefold()
    except Exception:
        return False
    return host == "free-reels.com" or host.endswith(".free-reels.com")


def title_from(obj: dict[str, Any]) -> str:
    for key in TITLE_KEYS:
        value = obj.get(key)
        if isinstance(value, str) and len(clean(value)) >= 2:
            return clean(value)
    return ""


def rank_from(obj: dict[str, Any]) -> tuple[str, int | None]:
    for key in RANK_KEYS:
        if key not in obj:
            continue
        value = obj.get(key)
        try:
            rank = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= rank <= 500:
            return key, rank
    return "", None


def semantic_hint(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
    hits = [term for term in SEMANTIC_TERMS if term in normalized]
    return ", ".join(hits)


def recommendation_hint(text: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
    return any(term.replace("_", " ") in normalized for term in RECOMMENDATION_TERMS)


def walk_arrays(value: Any, path: str = "$", depth: int = 0) -> list[dict[str, Any]]:
    if depth > 9:
        return []
    found: list[dict[str, Any]] = []
    if isinstance(value, list):
        dict_items = [x for x in value if isinstance(x, dict)]
        titled = [(obj, title_from(obj)) for obj in dict_items]
        titled = [(obj, title) for obj, title in titled if title]
        if len(titled) >= 3:
            ranks = []
            rank_keys = []
            sample = []
            observed_keys: set[str] = set()
            for obj, title in titled[:50]:
                observed_keys.update(str(k) for k in obj.keys())
                rkey, rank = rank_from(obj)
                if rank is not None:
                    ranks.append(rank)
                    rank_keys.append(rkey)
                if len(sample) < 5:
                    sample.append({"title": title, "rank": rank, "rank_key": rkey})
            found.append({
                "path": path,
                "array_len": len(value),
                "titled_items": len(titled),
                "explicit_ranks": sorted(set(ranks)),
                "rank_keys": sorted(set(rank_keys)),
                "semantic_hint": semantic_hint(path),
                "recommendation_hint": recommendation_hint(path),
                "sample": sample,
                "observed_keys": sorted(observed_keys)[:120],
            })
        for idx, item in enumerate(value[:80]):
            found.extend(walk_arrays(item, f"{path}[{idx}]", depth + 1))
    elif isinstance(value, dict):
        for key, item in list(value.items())[:160]:
            found.extend(walk_arrays(item, f"{path}.{key}", depth + 1))
    return found


def detect_field_richness(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    keys: set[str] = set()
    for candidate in candidates:
        keys.update(candidate.get("observed_keys") or [])
    keys_cf = {k.casefold() for k in keys}
    present = {}
    for group, aliases in FIELD_GROUPS.items():
        present[group] = any(str(alias).casefold() in keys_cf for alias in aliases)
    return {
        "groups": present,
        "group_count": sum(1 for value in present.values() if value),
        "observed_keys": sorted(keys)[:150],
    }


def classify(candidates: list[dict[str, Any]], page_records: list[dict[str, Any]]) -> dict[str, Any]:
    explicit: list[dict[str, Any]] = []
    ordered: list[dict[str, Any]] = []
    recommendation: list[dict[str, Any]] = []

    for candidate in candidates:
        ranks = candidate.get("explicit_ranks") or []
        if ranks:
            explicit.append(candidate)
        elif candidate.get("recommendation_hint"):
            recommendation.append(candidate)
        else:
            ordered.append(candidate)

    def score(item: dict[str, Any]) -> tuple[int, int, int]:
        ranks = item.get("explicit_ranks") or []
        contiguous = 0
        for expected in range(1, 51):
            if expected in ranks:
                contiguous = expected
            else:
                break
        semantic = 1 if item.get("semantic_hint") else 0
        return (semantic, contiguous, int(item.get("titled_items") or 0))

    explicit.sort(key=score, reverse=True)
    best = explicit[0] if explicit else None

    accessible = [p for p in page_records if p.get("http_status") and int(p["http_status"]) < 400]
    blocked_statuses = {403, 429, 451}
    blocked = bool(page_records) and not accessible and all(
        p.get("http_status") in blocked_statuses or p.get("challenge_detected")
        for p in page_records
    )

    if blocked:
        return {
            "status": "BLOCKED",
            "semantic_type": "UNKNOWN",
            "ranking_meaning": "",
            "top10_complete": False,
            "top20_eligible": False,
            "max_public_rank": 0,
            "selected_candidate": None,
            "reason": "Official Web access was blocked or challenged from the cloud runner.",
        }

    if best:
        ranks = best.get("explicit_ranks") or []
        top10_complete = all(i in ranks for i in range(1, 11))
        max_public_rank = max(ranks) if ranks else 0
        has_named_semantics = bool(best.get("semantic_hint"))
        if top10_complete and has_named_semantics:
            status = "PASS_CANDIDATE"
            reason = "Explicit ranks 1-10 were captured from an official FreeReels source with ranking semantics."
        else:
            status = "PARTIAL"
            reason = (
                "Explicit rank evidence exists, but Top10 is incomplete or the ranking meaning is not sufficiently named."
            )
        return {
            "status": status,
            "semantic_type": "RANKING",
            "ranking_meaning": best.get("semantic_hint") or "EXPLICIT_RANK_FIELD_UNNAMED",
            "top10_complete": top10_complete,
            "top20_eligible": top10_complete and all(i in ranks for i in range(1, 21)),
            "max_public_rank": max_public_rank,
            "selected_candidate": best,
            "reason": reason,
        }

    if recommendation:
        semantic_type = "RECOMMENDATION_FEED"
        reason = "Official sources exposed titled recommendation/feed arrays but no explicit rank evidence."
    elif ordered:
        semantic_type = "ORDERED_SHELF"
        reason = "Official sources exposed ordered titled content but no explicit rank evidence."
    else:
        semantic_type = "NO_PUBLIC_LIST"
        reason = "No official public ranked list was found in Web/structured evidence."

    return {
        "status": "NO_RANKING_SOURCE",
        "semantic_type": semantic_type,
        "ranking_meaning": "",
        "top10_complete": False,
        "top20_eligible": False,
        "max_public_rank": 0,
        "selected_candidate": None,
        "reason": reason,
    }


def run_probe(collection_date: str) -> dict[str, Any]:
    evidence_dir = EVIDENCE_ROOT / collection_date / "FreeReels"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    page_records: list[dict[str, Any]] = []
    network_candidates: list[dict[str, Any]] = []
    discovered_endpoints: list[dict[str, Any]] = []
    section_labels: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            contexts = [
                ("desktop", browser.new_context(
                    viewport={"width": 1440, "height": 1000},
                    locale="en-US",
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36"
                    ),
                )),
                ("mobile", browser.new_context(
                    viewport={"width": 412, "height": 915},
                    locale="en-US",
                    is_mobile=True,
                    has_touch=True,
                    user_agent=(
                        "Mozilla/5.0 (Linux; Android 15; Pixel 9 Pro) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Mobile Safari/537.36"
                    ),
                )),
            ]
            for profile, context in contexts:
                for index, url in enumerate(OFFICIAL_URLS, start=1):
                    page = context.new_page()
                    local_candidates: list[dict[str, Any]] = []
                    local_endpoints: list[dict[str, Any]] = []

                    def on_response(response):
                        try:
                            if not is_official_host(response.url):
                                return
                            ctype = (response.headers.get("content-type") or "").lower()
                            rtype = response.request.resource_type
                            if "json" not in ctype and rtype not in {"xhr", "fetch"}:
                                return
                            endpoint = {
                                "url": safe_url(response.url),
                                "status": response.status,
                                "content_type": ctype[:120],
                                "resource_type": rtype,
                            }
                            local_endpoints.append(endpoint)
                            if response.status >= 400:
                                return
                            body = response.json()
                            arrays = walk_arrays(body)
                            if not arrays:
                                return
                            for item in arrays:
                                item["endpoint"] = safe_url(response.url)
                                item["profile"] = profile
                            local_candidates.extend(arrays)
                            raw_path = evidence_dir / f"{profile}_{index}_structured_{len(local_candidates)}.json"
                            raw_path.write_text(
                                json.dumps(body, ensure_ascii=False, indent=2)[:2_000_000],
                                encoding="utf-8",
                            )
                        except Exception:
                            return

                    page.on("response", on_response)
                    status = None
                    error = ""
                    challenge = False
                    final_url = url
                    title = ""
                    body_text = ""
                    try:
                        response = page.goto(url, wait_until="domcontentloaded", timeout=90000)
                        status = response.status if response else None
                        page.wait_for_timeout(5000)
                        for _ in range(8):
                            page.mouse.wheel(0, 1200)
                            page.wait_for_timeout(350)
                        page.wait_for_timeout(1500)
                        final_url = page.url
                        title = clean(page.title(), 300)
                        body_text = clean(page.locator("body").inner_text(timeout=10000), 30000)
                        lowered = body_text.casefold()
                        challenge = any(token in lowered for token in (
                            "verify you are human", "access denied", "cloudflare", "captcha",
                        ))
                        for label in re.findall(
                            r"(?im)^(?:.{0,40})\b(?:top(?:\s*\d+)?|trending|popular|hot|ranking|rankings)\b.{0,60}$",
                            body_text,
                        ):
                            section_labels.add(clean(label, 180))
                        (evidence_dir / f"{profile}_{index}_page.html").write_text(
                            page.content(), encoding="utf-8"
                        )
                        (evidence_dir / f"{profile}_{index}_page.txt").write_text(
                            body_text, encoding="utf-8"
                        )
                        page.screenshot(
                            path=str(evidence_dir / f"{profile}_{index}.png"),
                            full_page=True,
                        )
                    except PlaywrightTimeoutError as exc:
                        error = f"TimeoutError: {exc}"
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"
                    finally:
                        page_records.append({
                            "profile": profile,
                            "requested_url": url,
                            "final_url": safe_url(final_url),
                            "http_status": status,
                            "title": title,
                            "body_text_chars": len(body_text),
                            "challenge_detected": challenge,
                            "error": error,
                        })
                        network_candidates.extend(local_candidates)
                        discovered_endpoints.extend(local_endpoints)
                        page.close()
                context.close()
        finally:
            browser.close()

    # De-duplicate candidate arrays and endpoints without inventing facts.
    uniq_candidates = {}
    for item in network_candidates:
        key = (
            item.get("endpoint"),
            item.get("path"),
            tuple(item.get("explicit_ranks") or []),
            int(item.get("titled_items") or 0),
        )
        uniq_candidates[key] = item
    candidates = list(uniq_candidates.values())

    endpoint_counts = Counter(
        (x.get("url"), x.get("status"), x.get("resource_type"))
        for x in discovered_endpoints
    )
    endpoints = [
        {"url": key[0], "status": key[1], "resource_type": key[2], "observations": count}
        for key, count in endpoint_counts.items()
    ]
    endpoints.sort(key=lambda x: (x["url"] or "", x["status"] or 0))

    classification = classify(candidates, page_records)
    fields = detect_field_richness(candidates)
    accessible = any(
        p.get("http_status") and int(p["http_status"]) < 400 and not p.get("challenge_detected")
        for p in page_records
    )
    text_visible = any(int(p.get("body_text_chars") or 0) > 200 for p in page_records)
    login_requirement = "NOT_REQUIRED_FOR_PUBLIC_PAGE" if accessible and text_visible else "UNKNOWN"

    result = {
        "pilot": "platform-expansion-p9-p10",
        "platform": "FreeReels",
        "platform_group": "SHORT_DRAMA_APP",
        "source_layer": "OFFICIAL_WEB",
        "collection_date": collection_date,
        "generated_at": datetime.now(TZ).isoformat(),
        "production_write": False,
        "source": {
            "official_web": OFFICIAL_URLS[0],
            "page_attempts": page_records,
            "structured_endpoints_observed": endpoints,
        },
        "ranking_meaning": classification["ranking_meaning"],
        "semantic_type": classification["semantic_type"],
        "top10_completeness": {
            "complete": classification["top10_complete"],
            "max_public_rank": classification["max_public_rank"],
            "missing_ranks": (
                [i for i in range(1, 11)
                 if i not in ((classification.get("selected_candidate") or {}).get("explicit_ranks") or [])]
                if classification["semantic_type"] == "RANKING" else list(range(1, 11))
            ),
        },
        "top20_evaluation": {
            "eligible": classification["top20_eligible"],
            "reason": (
                "Same official source exposes explicit ranks 1-20."
                if classification["top20_eligible"]
                else "Top20 is not evaluated unless explicit Top10 and ranks 1-20 are verified."
            ),
        },
        "evidence": {
            "artifact_path": f"pilot_expansion/evidence/{collection_date}/FreeReels/",
            "section_labels_seen": sorted(section_labels),
            "structured_candidate_count": len(candidates),
            "structured_candidates": candidates[:30],
        },
        "collection_path": (
            "OFFICIAL_STRUCTURED_ENDPOINT"
            if candidates else
            "OFFICIAL_WEB_BROWSER_DOM"
            if accessible else
            "OFFICIAL_WEB_BLOCKED"
        ),
        "fallback": {
            "next": "APP_AUTOMATION_CLOUD_DEVICE",
            "manual": "MANUAL_EVIDENCE_ONLY_AS_FALLBACK",
            "rule": "Third-party ranking data cannot substitute for FreeReels official ranking evidence.",
        },
        "field_richness": fields,
        "login_required": login_requirement,
        "cloud_environment": {
            "accessible": accessible,
            "possible_ip_block": classification["status"] == "BLOCKED",
            "profiles_tested": ["desktop", "mobile"],
        },
        "automation_risk": (
            "LOW_TO_MEDIUM"
            if classification["status"] == "PASS_CANDIDATE" and candidates
            else "MEDIUM"
            if accessible
            else "HIGH"
        ),
        "status": classification["status"],
        "status_reason": classification["reason"],
        "promotion_allowed": False,
    }
    return result


def write_result(result: dict[str, Any]) -> None:
    collection_date = result["collection_date"]
    day = RESULT_ROOT / collection_date
    day.mkdir(parents=True, exist_ok=True)
    (day / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    (RESULT_ROOT / "latest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    ACCEPTANCE_ROOT.mkdir(parents=True, exist_ok=True)
    acceptance = {
        "pilot": "platform-expansion-p9-p10",
        "collection_date": collection_date,
        "production_write": False,
        "FreeReels": {
            "status": result["status"],
            "semantic_type": result["semantic_type"],
            "top10_complete": result["top10_completeness"]["complete"],
            "top20_eligible": result["top20_evaluation"]["eligible"],
            "promotion_allowed": False,
        },
        "platform10": {
            "status": "AWAITING_USER_PLATFORM10_SELECTION",
            "collector_development_allowed": False,
        },
        "overall_state": "AWAITING_USER_PLATFORM10_SELECTION",
    }
    (ACCEPTANCE_ROOT / "status.json").write_text(
        json.dumps(acceptance, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=business_date())
    args = parser.parse_args()

    result = run_probe(args.date)
    write_result(result)
    print(json.dumps({
        "platform": result["platform"],
        "status": result["status"],
        "semantic_type": result["semantic_type"],
        "top10_complete": result["top10_completeness"]["complete"],
        "top20_eligible": result["top20_evaluation"]["eligible"],
        "collection_path": result["collection_path"],
        "production_write": result["production_write"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
