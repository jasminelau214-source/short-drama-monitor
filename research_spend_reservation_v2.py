"""Offline atomic reservation model; not a durable or production datastore."""
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
import threading

from drama_identity_v2 import IdentityReviewRequired
from research_attempt_policy_v2 import CallCheckpoint, STAGES, retry_checkpoint_ready
from research_eligibility_v2 import FreeProviderApproval, ResearchPreflightDecision

_SHA = set("0123456789abcdef")


@dataclass(frozen=True)
class ResearchTaskSnapshot:
    task_id: str
    revision: int
    state: str
    authoritative_run_id: str
    authority_snapshot_sha256: str
    provider_review_ref: str
    reserved_attempt_keys: frozenset[str] = frozenset()

    def __post_init__(self):
        if (not isinstance(self.task_id, str) or not self.task_id or self.task_id != self.task_id.strip()
                or type(self.revision) is not int or self.revision < 0
                or self.state not in {"PENDING", "RESEARCHING", "DEFERRED_FREE_QUOTA", "REVIEW_REQUIRED", "COMPLETE", "NEEDS_GPT", "FAILED"}
                or not isinstance(self.authoritative_run_id, str) or not self.authoritative_run_id
                or not isinstance(self.authority_snapshot_sha256, str) or len(self.authority_snapshot_sha256) != 64
                or not set(self.authority_snapshot_sha256) <= _SHA
                or not isinstance(self.provider_review_ref, str) or not self.provider_review_ref
                or not isinstance(self.reserved_attempt_keys, frozenset)
                or any(not isinstance(key, str) or len(key) != 64 or not set(key) <= _SHA for key in self.reserved_attempt_keys)):
            raise IdentityReviewRequired("malformed research task snapshot")


@dataclass(frozen=True)
class SpendAttemptCandidate:
    task_id: str
    expected_revision: int
    stage: str
    attempt_number: int
    preflight: ResearchPreflightDecision
    provider_approval: FreeProviderApproval
    now: datetime
    retry_checkpoint: CallCheckpoint | None = None


@dataclass(frozen=True)
class ReservationDecision:
    authorized: bool
    reason: str
    reservation_key: str | None = None
    current_revision: int | None = None


def attempt_reservation_key(task_id, stage, attempt_number):
    if not isinstance(task_id, str) or not task_id or stage not in STAGES or type(attempt_number) is not int or not 1 <= attempt_number <= 3:
        raise IdentityReviewRequired("exact task, supported stage and bounded attempt required")
    payload = json.dumps([task_id, stage, attempt_number], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def reservation_context_reason(current, preflight, provider, now):
    """Shared offline context gate for reservation and durable handoff."""
    if (not isinstance(preflight, ResearchPreflightDecision)
            or preflight.authorized is not True or preflight.eligibility_revalidated is not True
            or preflight.external_call_performed is not False or preflight.spend_incurred is not False
            or preflight.authoritative_run_id != current.authoritative_run_id
            or preflight.authority_snapshot_sha256 != current.authority_snapshot_sha256):
        return "PREFLIGHT_AUTHORITY_CHANGED"
    if (not isinstance(provider, FreeProviderApproval)
            or preflight.provider_id != provider.provider_id
            or provider.free_tier_verified is not True or provider.paid_fallback is not False
            or provider.revoked is not False or provider.review_ref != current.provider_review_ref
            or not isinstance(provider.approval_id, str) or not provider.approval_id.strip()
            or not isinstance(provider.source_url, str) or not provider.source_url.startswith("https://")
            or not isinstance(provider.checked_at, datetime) or provider.checked_at.utcoffset() is None
            or not isinstance(provider.valid_until, datetime) or provider.valid_until.utcoffset() is None
            or not (provider.checked_at <= now < provider.valid_until)
            or not isinstance(provider.artifact_sha256, str) or len(provider.artifact_sha256) != 64
            or not set(provider.artifact_sha256) <= _SHA):
        return "PROVIDER_APPROVAL_CHANGED"
    return None


class OfflineSpendReservationLedger:
    """Process-local CAS fixture for testing reserve-before-call invariants.

    A real worker needs a durable database transaction and unique constraint;
    this in-memory lock is only a deterministic concurrency model.
    """
    def __init__(self, snapshots):
        if not isinstance(snapshots, (tuple, list)) or any(not isinstance(item, ResearchTaskSnapshot) for item in snapshots):
            raise IdentityReviewRequired("typed task snapshots required")
        self._lock = threading.Lock()
        self._snapshots = {item.task_id: item for item in snapshots}
        if len(self._snapshots) != len(snapshots):
            raise IdentityReviewRequired("duplicate task snapshots")

    def snapshot(self, task_id):
        with self._lock:
            return self._snapshots.get(task_id)

    def supersede_authority(self, task_id, *, run_id, snapshot_sha256):
        """Synthetic concurrent collector update, atomically revising scope."""
        if not isinstance(run_id, str) or not run_id or type(snapshot_sha256) is not str or len(snapshot_sha256) != 64 or not set(snapshot_sha256) <= _SHA:
            raise IdentityReviewRequired("valid new authority binding required")
        with self._lock:
            current = self._snapshots.get(task_id)
            if current is None:
                return False
            self._snapshots[task_id] = replace(current, revision=current.revision + 1,
                                               authoritative_run_id=run_id,
                                               authority_snapshot_sha256=snapshot_sha256)
            return True

    def reserve(self, candidate):
        """Atomically reserve a single external-call attempt, without calling it."""
        if not isinstance(candidate, SpendAttemptCandidate):
            raise IdentityReviewRequired("typed spend candidate required")
        if (not isinstance(candidate.now, datetime)
                or candidate.now.utcoffset() is None
                or type(candidate.expected_revision) is not int):
            raise IdentityReviewRequired("aware reservation time and exact task revision required")
        key = attempt_reservation_key(candidate.task_id, candidate.stage, candidate.attempt_number)
        with self._lock:
            current = self._snapshots.get(candidate.task_id)
            if current is None:
                return ReservationDecision(False, "TASK_NOT_FOUND")
            if key in current.reserved_attempt_keys:
                return ReservationDecision(False, "ATTEMPT_ALREADY_RESERVED", key, current.revision)
            if type(candidate.expected_revision) is not int or candidate.expected_revision != current.revision:
                return ReservationDecision(False, "TASK_REVISION_CHANGED", key, current.revision)
            reason = reservation_context_reason(current, candidate.preflight, candidate.provider_approval, candidate.now)
            if reason:
                return ReservationDecision(False, reason, key, current.revision)

            if current.state == "PENDING":
                if candidate.attempt_number != 1 or candidate.retry_checkpoint is not None:
                    return ReservationDecision(False, "INITIAL_ATTEMPT_STATE_MISMATCH", key, current.revision)
            elif current.state == "RESEARCHING":
                if (candidate.retry_checkpoint is None
                        or not retry_checkpoint_ready(candidate.retry_checkpoint, now=candidate.now)
                        or candidate.retry_checkpoint.stage != candidate.stage
                        or candidate.attempt_number != candidate.retry_checkpoint.attempt_count + 1):
                    return ReservationDecision(False, "RETRY_CHECKPOINT_MISMATCH", key, current.revision)
            else:
                return ReservationDecision(False, "TASK_STATE_NOT_RESERVABLE", key, current.revision)

            updated = replace(current, revision=current.revision + 1, state="RESEARCHING",
                              reserved_attempt_keys=current.reserved_attempt_keys | {key})
            self._snapshots[candidate.task_id] = updated
            return ReservationDecision(True, "ATTEMPT_RESERVED_ONLY", key, updated.revision)
