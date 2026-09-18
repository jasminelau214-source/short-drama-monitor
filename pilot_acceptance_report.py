from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "pilot_data"
TZ = ZoneInfo("Asia/Shanghai")


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
    semantic_decisions = []
    unresolved = []

    for item in summary.get("platforms") or []:
        platform = str(item.get("platform"))
        status = str(item.get("status") or "")
        web_rows = int(item.get("row_count") or 0)
        stable = bool((item.get("stability") or {}).get("pathStable"))
        decision = dmap.get(platform) or {}
        alt = amap.get(platform) or {}

        source_state = "WEB_INCOMPLETE"
        alt_stability = None
        rank_probe = None

        if status in {"PASS_VERIFIED", "PASS_CANDIDATE"} and web_rows == 10:
            exact_valid += 1
            if stable:
                exact_stable += 1
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
                source_state = "WEB_EXPLICIT_TOP5_H5_NO_EQUIVALENT_TOP10"
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

        rows.append({
            "platform": platform,
            "source_state": source_state,
            "web_rows": web_rows,
            "web_status": status,
            "web_stable": stable,
            "rank_probe": rank_probe,
            "alternate_rows": int(alt.get("row_count") or 0),
            "alternate_target": alt.get("target"),
            "alternate_semantic_type": alt.get("semantic_type"),
            "alternate_stability": alt_stability,
            "next_step": decision.get("next_step"),
        })

    if unresolved:
        overall = "PILOT_RUNNING_UNRESOLVED_PATHS"
    elif semantic_decisions:
        overall = "PILOT_RUNNING_SCOPE_DECISION_REQUIRED"
    elif exact_stable == len(rows) and rows:
        overall = "AWAITING_USER_CONFIRMATION"
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
        "semantic_decision_platforms": sorted(set(semantic_decisions)),
        "unresolved_platforms": sorted(set(unresolved)),
        "platforms": rows,
    }
    (day / "acceptance.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Web Top10 Pilot Acceptance — {args.date}",
        "",
        f"- Overall state: **{overall}**",
        f"- Exact Web Top10 valid: **{exact_valid}/{len(rows)}**",
        f"- Exact Web Top10 stable (3 daily runs): **{exact_stable}/{len(rows)}**",
        "- Production write: **false**",
        "- Core promotion requires user confirmation: **true**",
        "",
        "| Platform | State | Web | Stable | Official rank probe | Alternate | Decision |",
        "|---|---|---:|---:|---|---|---|",
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
        lines.append(
            f'| {x["platform"]} | {x["source_state"]} | {x["web_rows"]}/10 | '
            f'{"YES" if x["web_stable"] else "NO"} | {probe_text} | {alt_text} | {decision_text} |'
        )

    if semantic_decisions:
        lines += [
            "",
            "## Scope decisions still required before promotion",
            "",
            *[
                (
                    f"- **{p}**: Web currently verifies only explicit Top1–5; the H5 probe exposes no equivalent "
                    "Most Trending rank labels, and the 10-item Popular Choices shelf remains semantically different."
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
        "semantic_decision_platforms": sorted(set(semantic_decisions)),
        "unresolved_platforms": sorted(set(unresolved)),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
