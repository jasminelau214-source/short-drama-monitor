from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "testdata"

_FILES = {
    "analysis_runs": DATA_DIR / "analysis_runs.json",
    "research_tasks": DATA_DIR / "research_tasks.json",
    "drama_overrides": DATA_DIR / "drama_overrides.json",
    "collection_jobs": DATA_DIR / "collection_jobs.json",
    "collection_targets": DATA_DIR / "collection_targets.json",
    "source_registry": DATA_DIR / "source_registry.json",
}


def available() -> bool:
    return all(path.is_file() for path in _FILES.values())


def _load(name: str) -> list[dict]:
    path = _FILES[name]
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, list) else []


def snapshot_meta() -> dict:
    return {
        "mode": "READ_ONLY_PRODUCTION_SNAPSHOT",
        "available": available(),
        "analysisRuns": len(_load("analysis_runs")),
        "researchTasks": len(_load("research_tasks")),
        "dramaOverrides": len(_load("drama_overrides")),
        "collectionJobs": len(_load("collection_jobs")),
        "collectionTargets": len(_load("collection_targets")),
        "sourceRegistry": len(_load("source_registry")),
    }


def seed_local(connect) -> dict:
    """Seed isolated local SQLite from read-only production snapshots.

    This never contacts Supabase and never writes to production. It only recreates
    the app's local publication inputs so the test frontend can run the real
    Integration publication logic against a frozen production snapshot.
    """
    if not available():
        return {"seeded": False, "reason": "snapshot_missing"}

    runs = _load("analysis_runs")
    overrides = _load("drama_overrides")
    with connect() as c:
        c.execute("DELETE FROM analysis_runs")
        c.execute("DELETE FROM drama_overrides")
        for row in runs:
            c.execute(
                """INSERT OR REPLACE INTO analysis_runs
                   (id,collection_date,platform,upload_ids_json,status,result_json,error,model,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(row.get("id") or ""),
                    str(row.get("collection_date") or ""),
                    str(row.get("platform") or ""),
                    "[]",
                    str(row.get("status") or ""),
                    json.dumps(row.get("result_json") or {}, ensure_ascii=False),
                    str(row.get("error") or ""),
                    str(row.get("model") or ""),
                    str(row.get("created_at") or ""),
                    str(row.get("updated_at") or ""),
                ),
            )
        for row in overrides:
            c.execute(
                "INSERT OR REPLACE INTO drama_overrides(drama_id,fields_json,updated_at) VALUES(?,?,?)",
                (
                    str(row.get("drama_id") or ""),
                    json.dumps(row.get("fields_json") or {}, ensure_ascii=False),
                    str(row.get("updated_at") or ""),
                ),
            )
        c.commit()
    return {"seeded": True, "analysisRuns": len(runs), "dramaOverrides": len(overrides)}


def list_research_tasks(limit: int = 500, statuses: list[str] | None = None) -> list[dict]:
    rows = _load("research_tasks")
    if statuses:
        allowed = {str(x) for x in statuses}
        rows = [row for row in rows if str(row.get("status") or "") in allowed]
    rows.sort(key=lambda row: (
        -int(row.get("priority") or 0),
        str(row.get("created_at") or ""),
        str(row.get("id") or ""),
    ))
    return rows[: max(1, min(int(limit or 500), 500))]


def research_queue_view() -> dict:
    rows = _load("research_tasks")
    status_counts = Counter(str(row.get("status") or "UNKNOWN") for row in rows)
    source_by_run = {}
    for run in _load("analysis_runs"):
        result = run.get("result_json") if isinstance(run.get("result_json"), dict) else {}
        collector = result.get("collector") if isinstance(result.get("collector"), dict) else {}
        source_type = str(collector.get("sourceType") or "LEGACY_INFERRED")
        source_by_run[str(run.get("id") or "")] = source_type

    out = []
    for row in rows:
        source_type = source_by_run.get(str(row.get("analysis_run_id") or ""), "UNKNOWN")
        sources = row.get("sources") if isinstance(row.get("sources"), list) else []
        out.append({
            "id": str(row.get("id") or ""),
            "analysisRunId": str(row.get("analysis_run_id") or ""),
            "collectionDate": str(row.get("collection_date") or ""),
            "platform": str(row.get("platform") or ""),
            "rank": row.get("rank"),
            "title": str(row.get("title") or ""),
            "status": str(row.get("status") or "UNKNOWN"),
            "sourceType": source_type,
            "confidence": str(row.get("confidence") or ""),
            "missingFields": row.get("missing_fields") if isinstance(row.get("missing_fields"), list) else [],
            "evidenceCount": len(sources),
            "reason": row.get("reason") if isinstance(row.get("reason"), list) else [],
            "error": str(row.get("error") or ""),
            "updatedAt": str(row.get("updated_at") or ""),
        })
    return {
        "available": True,
        "rowCount": len(out),
        "statusCounts": dict(sorted(status_counts.items())),
        "rows": out,
    }


def monitoring_status() -> dict:
    sources = _load("source_registry")
    targets = _load("collection_targets")
    jobs = _load("collection_jobs")

    dates = sorted({str(job.get("collection_date") or "") for job in jobs if job.get("collection_date")})
    latest_date = dates[-1] if dates else ""

    target_by_id = {str(target.get("id") or ""): target for target in targets}
    jobs_by_target: dict[str, list[dict]] = {}
    for job in jobs:
        jobs_by_target.setdefault(str(job.get("target_id") or ""), []).append(job)

    target_status = []
    for target in targets:
        target_id = str(target.get("id") or "")
        candidates = jobs_by_target.get(target_id, [])
        latest = max(
            candidates,
            key=lambda row: (
                str(row.get("collection_date") or ""),
                str(row.get("finished_at") or ""),
                str(row.get("created_at") or ""),
            ),
            default={},
        )
        target_status.append({
            "target_id": target_id,
            "source_id": str(target.get("source_id") or ""),
            "source_name": str(target.get("source_name") or ""),
            "source_group": str(target.get("source_group") or ""),
            "target_key": str(target.get("target_key") or ""),
            "ranking_type": str(target.get("ranking_type") or ""),
            "category": str(target.get("category") or "All"),
            "configured_top_n": int(target.get("top_n") or 0),
            "enabled": bool(target.get("enabled")),
            "latest_collection_date": str(latest.get("collection_date") or ""),
            "latest_job_status": str(latest.get("status") or ""),
        })

    date_jobs = [job for job in jobs if not latest_date or str(job.get("collection_date") or "") == latest_date]
    status_counts = Counter(str(job.get("status") or "UNKNOWN") for job in date_jobs)
    official_web_ids = {
        str(target.get("id") or "")
        for target in targets
        if str(target.get("source_group") or "") == "OFFICIAL_WEB"
    }
    web_jobs = [job for job in date_jobs if str(job.get("target_id") or "") in official_web_ids]
    web_platforms = {
        str(target_by_id.get(str(job.get("target_id") or ""), {}).get("source_name") or "").replace(" Official Web", "")
        for job in web_jobs
    }
    web_platforms.discard("")

    coverage = {
        "collection_date": latest_date,
        "target_jobs": len(date_jobs),
        "succeeded_targets": status_counts.get("SUCCEEDED", 0),
        "partial_targets": status_counts.get("PARTIAL", 0),
        "review_targets": status_counts.get("NEEDS_REVIEW", 0),
        "other_targets": len(date_jobs) - status_counts.get("SUCCEEDED", 0) - status_counts.get("PARTIAL", 0) - status_counts.get("NEEDS_REVIEW", 0),
        "web_platforms_with_jobs": len(web_platforms),
        "extracted_rows": sum(int(job.get("extracted_row_count") or 0) for job in web_jobs),
    }

    core_apps = [s for s in sources if str(s.get("source_group") or "") == "SHORT_DRAMA_APP"]
    official_web = [s for s in sources if str(s.get("source_group") or "") == "OFFICIAL_WEB"]

    return {
        "ok": True,
        "collectionDate": latest_date,
        "registry": {
            "coreAppPlatforms": len(core_apps),
            "officialWebSources": len(official_web),
            "totalSources": len(sources),
        },
        "coverage": coverage,
        "targetStatus": target_status,
        "jobs": date_jobs,
        "statusCounts": {
            "total": len(date_jobs),
            "succeeded": status_counts.get("SUCCEEDED", 0),
            "partial": status_counts.get("PARTIAL", 0),
            "needsReview": status_counts.get("NEEDS_REVIEW", 0),
            "other": coverage["other_targets"],
        },
    }
