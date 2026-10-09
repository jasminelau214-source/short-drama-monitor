"""Offline, fail-closed preflight for a future external research call.

This module authorizes no actual API call, queue transition or spend. The
caller-supplied ledgers must come from protected runtime boundaries, which
this isolated step does not implement.
"""
from dataclasses import dataclass
from datetime import datetime
import re

from batch_authority_v2 import ObservationDecision, assess_current_observation
from drama_identity_v2 import IdentityReviewRequired

_SHA256 = re.compile(r"[0-9a-f]{64}")

@dataclass(frozen=True)
class FreeProviderApproval:
    provider_id: str
    checked_at: datetime
    valid_until: datetime
    free_tier_verified: bool
    paid_fallback: bool
    revoked: bool = False
    review_ref: str | None = None
    source_url: str | None = None
    artifact_sha256: str | None = None
    approval_id: str | None = None


@dataclass(frozen=True)
class ResearchPreflightDecision:
    authorized: bool
    reason: str
    provider_id: str | None = None
    authoritative_run_id: str | None = None
    authority_snapshot_sha256: str | None = None
    external_call_performed: bool = False
    spend_incurred: bool = False


def authorize_research_preflight(
    *, scope, origin_run_id, source_entity_id, record_id, canonical_drama_id,
    runs, task_state, provider_approvals, existing_complete, now, **authority_context
):
    """Return a synthetic preflight decision; never call a provider.

    Every execution must have a current authoritative observation and a
    separately approved, unexpired free-only provider. Only PENDING tasks can
    enter this path. DEFERRED tasks need quota restoration and a fresh call to
    this function; all other states fail closed.
    """
    if type(existing_complete) is not bool:
        raise IdentityReviewRequired("exact COMPLETE lookup result required")
    if existing_complete:
        return ResearchPreflightDecision(False, "GLOBAL_RESEARCH_ALREADY_COMPLETE")
    if task_state != "PENDING":
        return ResearchPreflightDecision(False, "TASK_STATE_NOT_CALLABLE")
    if not isinstance(provider_approvals, (tuple, list)) or not provider_approvals:
        return ResearchPreflightDecision(False, "NO_VERIFIED_FREE_PROVIDER")
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise IdentityReviewRequired("timezone-aware decision time required")

    authority = assess_current_observation(
        scope, origin_run_id=origin_run_id, source_entity_id=source_entity_id,
        record_id=record_id, canonical_drama_id=canonical_drama_id,
        runs=runs, now=now, **authority_context,
    )
    if not authority.current:
        return ResearchPreflightDecision(False, authority.reason, authoritative_run_id=authority.authoritative_run_id,
                                         authority_snapshot_sha256=authority.snapshot_sha256)

    candidates = []
    for approval in provider_approvals:
        if not isinstance(approval, FreeProviderApproval):
            continue
        if (not approval.provider_id or approval.provider_id != approval.provider_id.strip()
                or type(approval.free_tier_verified) is not bool or not approval.free_tier_verified
                or type(approval.paid_fallback) is not bool or approval.paid_fallback
                or type(approval.revoked) is not bool or approval.revoked
                or not isinstance(approval.review_ref, str) or not approval.review_ref.strip()
                or not isinstance(approval.approval_id, str) or not approval.approval_id.strip()
                or not isinstance(approval.source_url, str) or not approval.source_url.startswith("https://")
                or not isinstance(approval.artifact_sha256, str) or not _SHA256.fullmatch(approval.artifact_sha256)
                or not isinstance(approval.checked_at, datetime) or approval.checked_at.utcoffset() is None
                or not isinstance(approval.valid_until, datetime) or approval.valid_until.utcoffset() is None
                or not (approval.checked_at <= now < approval.valid_until)):
            continue
        candidates.append(approval.provider_id)
    if not candidates:
        return ResearchPreflightDecision(False, "NO_VERIFIED_FREE_PROVIDER", authoritative_run_id=authority.authoritative_run_id,
                                         authority_snapshot_sha256=authority.snapshot_sha256)
    return ResearchPreflightDecision(
        True, "PREFLIGHT_AUTHORIZED_ONLY", provider_id=sorted(candidates)[0],
        authoritative_run_id=authority.authoritative_run_id,
        authority_snapshot_sha256=authority.snapshot_sha256,
    )
