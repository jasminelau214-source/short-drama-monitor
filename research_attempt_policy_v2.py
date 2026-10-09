"""Offline state transitions for one research API call; no API or queue IO."""
from dataclasses import dataclass
from datetime import datetime, timedelta

from drama_identity_v2 import IdentityReviewRequired

MAX_ATTEMPTS = 3
MAX_ACTIVE_SECONDS = 300
STAGES = frozenset({"SEARCH", "ANALYZE", "VALIDATE", "WRITEBACK"})


@dataclass(frozen=True)
class CallCheckpoint:
    stage: str
    attempt_count: int = 0
    active_seconds: int = 0
    status: str = "READY"
    retry_at: datetime | None = None
    checkpoint_id: str = "checkpoint:synthetic"


@dataclass(frozen=True)
class CallTransition:
    checkpoint: CallCheckpoint
    reason: str


def _valid(checkpoint, now):
    if not isinstance(checkpoint, CallCheckpoint) or checkpoint.stage not in STAGES:
        raise IdentityReviewRequired("typed supported stage checkpoint required")
    if (type(checkpoint.attempt_count) is not int or not 0 <= checkpoint.attempt_count <= MAX_ATTEMPTS
            or type(checkpoint.active_seconds) is not int or not 0 <= checkpoint.active_seconds <= MAX_ACTIVE_SECONDS
            or not isinstance(checkpoint.checkpoint_id, str) or not checkpoint.checkpoint_id.strip()
            or not isinstance(now, datetime) or now.utcoffset() is None):
        raise IdentityReviewRequired("malformed call checkpoint or decision time")


def record_call_result(checkpoint, *, outcome, now, attempt_active_seconds=0):
    """Record one completed provider-call outcome and return a new checkpoint.

    `outcome` is one of success, transient, quota, insufficient_evidence,
    identity_conflict, or unknown. Backoff is 2s, 4s; business outcomes never
    retry. Quota suspends with the call checkpoint and is not FAILED.
    """
    _valid(checkpoint, now)
    if checkpoint.status != "READY" or checkpoint.attempt_count >= MAX_ATTEMPTS:
        raise IdentityReviewRequired("checkpoint is not callable")
    if type(attempt_active_seconds) is not int or attempt_active_seconds < 0:
        raise IdentityReviewRequired("nonnegative integer active duration required")
    attempts = checkpoint.attempt_count + 1
    active = checkpoint.active_seconds + attempt_active_seconds
    if active > MAX_ACTIVE_SECONDS:
        return CallTransition(CallCheckpoint(checkpoint.stage, attempts, MAX_ACTIVE_SECONDS, "FAILED", checkpoint_id=checkpoint.checkpoint_id), "ACTIVE_TIMEOUT_EXHAUSTED")
    if outcome == "success":
        return CallTransition(CallCheckpoint(checkpoint.stage, attempts, active, "SUCCEEDED", checkpoint_id=checkpoint.checkpoint_id), "CALL_SUCCEEDED")
    if outcome == "quota":
        return CallTransition(CallCheckpoint(checkpoint.stage, attempts, active, "DEFERRED_FREE_QUOTA", checkpoint_id=checkpoint.checkpoint_id), "FREE_QUOTA_UNAVAILABLE")
    if outcome == "transient":
        if attempts >= MAX_ATTEMPTS:
            return CallTransition(CallCheckpoint(checkpoint.stage, attempts, active, "FAILED", checkpoint_id=checkpoint.checkpoint_id), "TECHNICAL_ATTEMPTS_EXHAUSTED")
        delay = 2 ** attempts
        return CallTransition(CallCheckpoint(checkpoint.stage, attempts, active, "RETRY_WAIT", now + timedelta(seconds=delay), checkpoint.checkpoint_id), "TRANSIENT_RETRY_SCHEDULED")
    if outcome == "insufficient_evidence":
        status, reason = "NEEDS_GPT", "INSUFFICIENT_EVIDENCE"
    elif outcome == "identity_conflict":
        status, reason = "REVIEW_REQUIRED", "IDENTITY_OR_SOURCE_CONFLICT"
    else:
        status, reason = "REVIEW_REQUIRED", "UNKNOWN_BUSINESS_OUTCOME_FAILS_CLOSED"
    return CallTransition(CallCheckpoint(checkpoint.stage, attempts, active, status, checkpoint_id=checkpoint.checkpoint_id), reason)


def retry_is_due(checkpoint, *, now):
    _valid(checkpoint, now)
    if checkpoint.status != "RETRY_WAIT" or not isinstance(checkpoint.retry_at, datetime) or checkpoint.retry_at.utcoffset() is None:
        return False
    return now >= checkpoint.retry_at


def prepare_due_retry(checkpoint, *, now):
    """Make one bounded retry ready only after its backoff has elapsed."""
    _valid(checkpoint, now)
    if checkpoint.status != "RETRY_WAIT" or not isinstance(checkpoint.retry_at, datetime) or checkpoint.retry_at.utcoffset() is None:
        raise IdentityReviewRequired("retry-wait checkpoint required")
    if now < checkpoint.retry_at:
        return CallTransition(checkpoint, "BACKOFF_NOT_ELAPSED")
    return CallTransition(CallCheckpoint(checkpoint.stage, checkpoint.attempt_count, checkpoint.active_seconds, "READY", checkpoint_id=checkpoint.checkpoint_id), "RETRY_READY")


def retry_checkpoint_ready(checkpoint, *, now):
    """Validate a checkpoint for a reauthorized attempt while RESEARCHING."""
    try:
        _valid(checkpoint, now)
    except IdentityReviewRequired:
        return False
    return checkpoint.status == "READY" and 1 <= checkpoint.attempt_count < MAX_ATTEMPTS and checkpoint.retry_at is None


def resume_quota_checkpoint(checkpoint, *, now, quota_restored, eligibility_revalidated):
    """Resume only after both quota restoration and fresh eligibility checks."""
    _valid(checkpoint, now)
    if checkpoint.status != "DEFERRED_FREE_QUOTA":
        raise IdentityReviewRequired("checkpoint is not quota-deferred")
    if type(quota_restored) is not bool or type(eligibility_revalidated) is not bool:
        raise IdentityReviewRequired("explicit quota and eligibility decisions required")
    if not quota_restored or not eligibility_revalidated:
        return CallTransition(checkpoint, "QUOTA_OR_ELIGIBILITY_NOT_REVALIDATED")
    if checkpoint.attempt_count >= MAX_ATTEMPTS:
        return CallTransition(CallCheckpoint(checkpoint.stage, checkpoint.attempt_count, checkpoint.active_seconds, "FAILED", checkpoint_id=checkpoint.checkpoint_id), "TECHNICAL_ATTEMPTS_EXHAUSTED")
    # Quota waiting is excluded; active_seconds and the per-call attempt budget
    # are preserved, and the caller still performs the B4 preflight.
    return CallTransition(CallCheckpoint(checkpoint.stage, checkpoint.attempt_count, checkpoint.active_seconds, "READY", checkpoint_id=checkpoint.checkpoint_id), "QUOTA_RESTORED_AND_ELIGIBILITY_REVALIDATED")
