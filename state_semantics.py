from __future__ import annotations

"""Stage-specific state contract for JSM Integration.

The production database contains historical free-text statuses.  This module
does not rewrite that history.  It prevents new Integration writes from
inventing additional states and keeps analysis-run and research-task semantics
separate.
"""

ANALYSIS_RUN_ANALYZING = "分析中"
ANALYSIS_RUN_COLLECTED = "已采集"
ANALYSIS_RUN_RESEARCH_PENDING = "已识别-待深研"
ANALYSIS_RUN_COMPLETE = "已分析"
ANALYSIS_RUN_FAILED = "分析失败"

ANALYSIS_RUN_STATUSES = frozenset({
    ANALYSIS_RUN_ANALYZING,
    ANALYSIS_RUN_COLLECTED,
    ANALYSIS_RUN_RESEARCH_PENDING,
    ANALYSIS_RUN_COMPLETE,
    ANALYSIS_RUN_FAILED,
})

RESEARCH_TASK_STATUSES = frozenset({
    "PENDING",
    "RESEARCHING",
    "COMPLETE",
    "NEEDS_GPT",
    "REVIEW_REQUIRED",
    "FAILED",
})

COLLECTION_JOB_STATUSES = frozenset({
    "PENDING",
    "RUNNING",
    "SUCCEEDED",
    "PARTIAL",
    "NEEDS_REVIEW",
    "FAILED",
})


def require_stage_state(stage: str, status: object) -> str:
    value = str(status or "").strip()
    allowed = {
        "analysis_run": ANALYSIS_RUN_STATUSES,
        "research_task": RESEARCH_TASK_STATUSES,
        "collection_job": COLLECTION_JOB_STATUSES,
    }.get(str(stage or "").strip())
    if allowed is None:
        raise ValueError(f"UNKNOWN_STATE_STAGE:{stage}")
    if value not in allowed:
        raise ValueError(f"INVALID_{stage.upper()}_STATE:{value or '<blank>'}")
    return value


def analysis_run_status_for(*, source_type: str, has_new_titles: bool) -> str:
    if str(source_type or "").strip() != "SHORT_DRAMA_APP":
        return ANALYSIS_RUN_COLLECTED
    return ANALYSIS_RUN_RESEARCH_PENDING if has_new_titles else ANALYSIS_RUN_COMPLETE


def is_analysis_run_terminal(status: object) -> bool:
    value = require_stage_state("analysis_run", status)
    return value in {
        ANALYSIS_RUN_COLLECTED,
        ANALYSIS_RUN_RESEARCH_PENDING,
        ANALYSIS_RUN_COMPLETE,
        ANALYSIS_RUN_FAILED,
    }
