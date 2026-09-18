from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "pilot_data"
TZ = ZoneInfo("Asia/Shanghai")

NON_BLOCKING_PLATFORMS = {"DramaWave"}


def today() -> str:
    return datetime.now(TZ).date().isoformat()


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def alt_history(platform: str, semantic_type: str, target: str, current_date: str) -> dict:
    entries = []
    for path in sorted(DATA_ROOT.glob("20??-??-??/alternate_sources.json")):
        date_key = path.parent.name
        if date_key > current_date:
            continue
        payload = load_json(path, {"sources": []})
        match = next(
            (
                x for x in (payload.get("sources") or [])
                if isinstance(x, dict)
                and x.get("platform") == platform
                and x.get("semantic_type") == semantic_type
                and x.get("target") == target
            ),
            None,
        )
        if match:
            entries.append((date_key, int(match.get("row_count") or 0) == 10 and not match.get("error")))
    consecutive = 0
    for _, ok in reversed(entries):
        if ok:
            consecutive += 1
        else:
            break
    return {
        "consecutive_complete_runs": consecutive,
        "path_stable": consecutive >= 3,
        "dates": [d for d, _ in entries[-3:]],
    }


def rank_probe_history(current_date: str) -> dict:
    entries = []
    for path in sorted(DATA_ROOT.glob("20??-??-??/dramawave_rank_probe.json")):
        date_key = path.parent.name
        if date_key > current_date:
            continue
        payload = load_json(path, {})
        ok = bool(payload.get("top10_complete")) and not (payload.get("rank_conflicts") or {})
        entries.append((date_key, ok, len(payload.get("explicit_ranks") or [])))
    consecutive = 0
    for _, ok, _ in reversed(entries):
        if ok:
            consecutive += 1
        else:
            break
    return {
        "consecutive_complete_runs": consecutive,
        "path_stable": consecutive >= 3,
        "dates": [d for d, _, _ in entries[-3:]],
        "explicit_counts": [n for _, _, n in entries[-3:]],
    }


def evidence_summary(platform: str, detail: dict) -> str:
    evidence = detail.get("evidence") if isinstance(detail.get("evidence"), dict) else {}
    structured = str(evidence.get("structuredData") or "").strip()
    collector = str(evidence.get("collectorVersion") or "").strip()

    if platform == "DramaBox":
        source_evidence = evidence.get("sourceEvidence") if isinstance(evidence.get("sourceEvidence"), dict) else {}
        egress = str(source_evidence.get("egress") or "").strip()
        status = source_evidence.get("http_status")
        if egress:
            return f"official Trending page via {egress}; HTTP {status or '-'}"
        return collector or "official Trending page"
    if platform == "GoodShort":
        payload = evidence.get("payloadEvidence") if isinstance(evidence.get("payloadEvidence"), dict) else {}
        return f"server-rendered official Top in GoodShort; rows={payload.get('row_count', detail.get('row_count', 0))}"
    if platform == "DramaWave":
        ranks = evidence.get("explicitRanksSeen") or evidence.get("profileMergedRanks") or []
        cards = evidence.get("cardCount")
        return f"explicit Most Trending labels {ranks}; rendered cards={cards or '-'}"
    if structured:
        extra = ""
        if platform == "NetShort" and evidence.get("itemListName"):
            extra = f" ({evidence.get('itemListName')})"
        elif platform == "ReelShort" and evidence.get("shelfName"):
            extra = f" (shelf={evidence.get('shelfName')})"
        elif platform == "ShortMax" and evidence.get("sectionScoped"):
            extra = f"; section-scoped DOM; rendered cards={evidence.get('renderedCardCount', '-')}"
        return structured + extra
    return collector or "official Web evidence captured"


def fallback_summary(platform: str, detail: dict) -> str:
    evidence = detail.get("evidence") if isinstance(detail.get("evidence"), dict) else {}
    if platform == "DramaBox":
        service = str(evidence.get("fallbackService") or "").strip()
        if service:
            return f"independent cloud egress ({service}); still reads official Web source"
        return "same official page via browser fallback"
    if platform == "DramaWave":
        return "NONE_EQUIVALENT; H5 Popular Choices is diagnostic only, not Most Trending"
    if platform == "ShortMax":
        return "NONE_EQUIVALENT; playNum-derived ordering is rejected"
    if platform == "GoodShort":
        return "same official page via rendered-browser adapter"
    return "same official Web page via alternate browser profile; no cross-source substitution"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=today())
    args = parser.parse_args()
    day = DATA_ROOT / args.date

    summary = load_json(day / "summary.json", {"platforms": []})
    decisions = load_json(day / "path_decisions.json", {"decisions": []})
    alternate = load_json(day / "alternate_sources.json", {"sources": []})
    dw_probe = load_json(day / "dramawave_rank_probe.json", {})
    dmap = {str(x.get("platform")): x for x in decisions.get("decisions") or [] if isinstance(x, dict)}
    amap = {str(x.get("platform")): x for x in alternate.get("sources") or [] if isinstance(x, dict)}

    rows = []
    exact_valid = 0
    exact_stable = 0
    core_exact_valid = 0
    core_exact_stable = 0
    semantic_decisions = []
    unresolved = []

    for item in summary.get("platforms") or []:
        platform = str(item.get("platform"))
        status = str(item.get("status") or "")
        web_rows = int(item.get("row_count") or 0)
        stable = bool((item.get("stability") or {}).get("pathStable"))
        decision = dmap.get(platform) or {}
        alt = amap.get(platform) or {}
        detail = load_json(day / f"{platform}.json", {})

        source_state = "WEB_INCOMPLETE"
        alt_stability = None
        rank_probe = None

        if status in {"PASS_VERIFIED", "PASS_CANDIDATE"} and web_rows == 10:
            exact_valid += 1
            if platform not in NON_BLOCKING_PLATFORMS:
                core_exact_valid += 1
            if stable:
                exact_stable += 1
                if platform not in NON_BLOCKING_PLATFORMS:
                    core_exact_stable += 1
            source_state = "WEB_TOP10_STABLE" if stable else "WEB_TOP10_VALIDATING"
        elif platform == "DramaWave" and dw_probe:
            probe_complete = bool(dw_probe.get("top10_complete")) and not (dw_probe.get("rank_conflicts") or {})
            rank_probe = {
                "status": dw_probe.get("status"),
                "explicit_rank_count": len(dw_probe.get("explicit_ranks") or []),
                "top10_complete": probe_complete,
                "missing_top10_ranks": dw_probe.get("missing_top10_ranks") or [],
                "stability": rank_probe_history(args.date),
            }
            if probe_complete:
                source_state = "OFFICIAL_H5_EXPLICIT_TOP10_SCOPE_DECISION"
                semantic_decisions.append(platform)
            elif (
                web_rows == 5
                and dw_probe.get("status") == "NO_EXPLICIT_RANKS"
                and int(alt.get("row_count") or 0) == 10
                and alt.get("semantic_type") == "ordered_shelf_not_explicit_rank"
            ):
                source_state = "NON_BLOCKING_SPECIAL_SCOPE"
                alt_stability = alt_history(
                    platform,
                    str(alt.get("semantic_type")),
                    str(alt.get("target")),
                    args.date,
                )
                semantic_decisions.append(platform)
            elif int(alt.get("row_count") or 0) == 10 and alt.get("semantic_type"):
                source_state = "ALT_TOP10_SEMANTIC_DECISION"
                alt_stability = alt_history(
                    platform,
                    str(alt.get("semantic_type")),
                    str(alt.get("target")),
                    args.date,
                )
                semantic_decisions.append(platform)
            else:
                unresolved.append(platform)
        elif int(alt.get("row_count") or 0) == 10 and alt.get("semantic_type"):
            source_state = "ALT_TOP10_SEMANTIC_DECISION"
            alt_stability = alt_history(
                platform,
                str(alt.get("semantic_type")),
                str(alt.get("target")),
                args.date,
            )
            semantic_decisions.append(platform)
        else:
            unresolved.append(platform)

        stability = item.get("stability") if isinstance(item.get("stability"), dict) else {}
        rows.append({
            "platform": platform,
            "source_state": source_state,
            "source_url": detail.get("source_url"),
            "ranking_meaning": detail.get("ranking_type"),
            "strategy": detail.get("strategy"),
            "web_rows": web_rows,
            "web_status": status,
            "web_stable": stable,
            "consecutive_valid_runs": int(stability.get("consecutiveValidRuns") or 0),
            "evidence_summary": evidence_summary(platform, detail),
            "fallback_path": fallback_summary(platform, detail),
            "rank_probe": rank_probe,
            "alternate_rows": int(alt.get("row_count") or 0),
            "alternate_target": alt.get("target"),
            "alternate_semantic_type": alt.get("semantic_type"),
            "alternate_stability": alt_stability,
            "next_step": decision.get("next_step"),
        })

    blocking_unresolved = [p for p in unresolved if p not in NON_BLOCKING_PLATFORMS]
    core_platform_count = sum(1 for x in rows if x["platform"] not in NON_BLOCKING_PLATFORMS)

    if blocking_unresolved:
        overall = "PILOT_RUNNING_UNRESOLVED_PATHS"
    elif core_exact_stable == core_platform_count and core_platform_count:
        overall = "AWAITING_USER_CONFIRMATION_CORE_SCOPE"
    else:
        overall = "PILOT_RUNNING_STABILITY_VALIDATION"

    payload = {
        "collection_date": args.date,
        "generated_at": datetime.now(TZ).isoformat(),
        "production_write": False,
        "promotion_requires_user_confirmation": True,
        "overall_state": overall,
        "exact_web_top10_valid": exact_valid,
        "exact_web_top10_stable": exact_stable,
        "core_promotion_scope_count": core_platform_count,
        "core_exact_web_top10_valid": core_exact_valid,
        "core_exact_web_top10_stable": core_exact_stable,
        "non_blocking_platforms": sorted(NON_BLOCKING_PLATFORMS),
        "semantic_decision_platforms": sorted(set(semantic_decisions)),
        "unresolved_platforms": sorted(set(unresolved)),
        "platforms": rows,
    }
    (day / "acceptance.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Web Top10 Pilot Acceptance — {args.date}",
        "",
        f"- Overall state: **{overall}**",
        f"- Exact Web Top10 valid (all observed platforms): **{exact_valid}/{len(rows)}**",
        f"- Core promotion scope: **{core_platform_count} platforms**",
        f"- Core exact Web Top10 valid: **{core_exact_valid}/{core_platform_count}**",
        f"- Core exact Web Top10 stable (3 daily runs): **{core_exact_stable}/{core_platform_count}**",
        f"- Non-blocking special scope: **{', '.join(sorted(NON_BLOCKING_PLATFORMS))}**",
        "- Production write: **false**",
        "- Core promotion requires user confirmation: **true**",
        "",
        "| Platform | Official source | Ranking meaning | Completeness | Stability | Evidence | Fallback | Decision |",
        "|---|---|---|---:|---:|---|---|---|",
    ]
    for x in rows:
        alt_text = "-"
        if x["alternate_rows"]:
            ast = x["alternate_stability"] or {}
            alt_text = (
                f'{x["alternate_target"]} {x["alternate_rows"]}/10; '
                f'alt stable={ast.get("consecutive_complete_runs", 0)}/3'
            )

        probe_text = "-"
        if x["rank_probe"]:
            rp = x["rank_probe"]
            rs = rp.get("stability") or {}
            probe_text = (
                f'explicit={rp.get("explicit_rank_count", 0)}/10; '
                f'complete={"YES" if rp.get("top10_complete") else "NO"}; '
                f'stable={rs.get("consecutive_complete_runs", 0)}/3'
            )

        decision_text = x["next_step"] or "-"
        source_text = x.get("source_url") or "-"
        ranking_text = x.get("ranking_meaning") or "-"
        evidence_text = x.get("evidence_summary") or "-"
        fallback_text = x.get("fallback_path") or "-"
        stability_text = f'{x.get("consecutive_valid_runs", 0)}/3'
        completeness_text = f'{x["web_rows"]}/10'
        if x["platform"] == "DramaWave" and x["rank_probe"]:
            evidence_text = f'{evidence_text}; H5 explicit-rank probe: {probe_text}; alternate: {alt_text}'
        elif x["alternate_rows"]:
            evidence_text = f'{evidence_text}; diagnostic alternate: {alt_text}'
        lines.append(
            f'| {x["platform"]} | {source_text} | {ranking_text} | {completeness_text} | '
            f'{stability_text} | {evidence_text} | {fallback_text} | {decision_text} |'
        )

    if semantic_decisions:
        lines += [
            "",
            "## Non-blocking special-scope observations",
            "",
            *[
                (
                    f"- **{p}**: excluded from the current core promotion gate. Web currently verifies only explicit Top1–5; "
                    "the H5 probe exposes no equivalent Most Trending rank labels, and the 10-item Popular Choices shelf remains semantically different."
                )
                for p in sorted(set(semantic_decisions))
            ],
        ]

    lines += [
        "",
        "No test result in this report is authorized for production ingestion.",
        "",
    ]
    (day / "ACCEPTANCE.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({
        "overall_state": overall,
        "exact_web_top10_valid": exact_valid,
        "exact_web_top10_stable": exact_stable,
        "core_promotion_scope_count": core_platform_count,
        "core_exact_web_top10_valid": core_exact_valid,
        "core_exact_web_top10_stable": core_exact_stable,
        "non_blocking_platforms": sorted(NON_BLOCKING_PLATFORMS),
        "semantic_decision_platforms": sorted(set(semantic_decisions)),
        "unresolved_platforms": sorted(set(unresolved)),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
