"""Offline verification of a reviewed, artifact-bound free-provider policy."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit
import hashlib
import json
import re

from drama_identity_v2 import IdentityReviewRequired
from research_eligibility_v2 import FreeProviderApproval

_FIELDS = {"schemaVersion", "providerId", "productId", "sourceUrl", "evidenceRef", "artifactSha256", "freeTierVerified", "automaticPaidFallback", "checkedAt", "validUntil"}
_SHA = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ProviderPolicyApproval:
    evidence_ref: str
    claim_sha256: str
    approval_id: str
    approved_at: datetime
    valid_until: datetime
    revoked: bool = False


@dataclass(frozen=True)
class ProviderPolicyEvidence:
    claim_bytes: bytes
    artifact_bytes: bytes


def _unique(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise IdentityReviewRequired("duplicate provider policy claim key")
        data[key] = value
    return data


def _aware(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise IdentityReviewRequired("timezone-aware provider policy time required")
    return value


def _time(value, name):
    if not isinstance(value, str):
        raise IdentityReviewRequired(f"{name} timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IdentityReviewRequired(f"invalid {name} timestamp") from exc
    return _aware(parsed)


def verify_free_provider_policy(evidence, *, approval_ledger, trusted_provider_hosts, now):
    """Verify exact policy bytes against a separate approval and host registry.

    This validates a reviewed snapshot, not today's online provider pricing.
    The approval ledger and host registry must be supplied by a protected
    runtime boundary, which this isolated step does not implement.
    """
    _aware(now)
    if not isinstance(approval_ledger, Mapping) or not isinstance(trusted_provider_hosts, Mapping):
        raise IdentityReviewRequired("independent approval ledger and provider host registry required")
    if not isinstance(evidence, ProviderPolicyEvidence) or type(evidence.claim_bytes) is not bytes or type(evidence.artifact_bytes) is not bytes or not evidence.artifact_bytes:
        raise IdentityReviewRequired("exact provider claim and captured artifact bytes required")
    try:
        claim = json.loads(evidence.claim_bytes.decode("utf-8"), object_pairs_hook=_unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IdentityReviewRequired("malformed provider policy claim") from exc
    if type(claim) is not dict or set(claim) != _FIELDS or type(claim.get("schemaVersion")) is not int or claim["schemaVersion"] != 1:
        raise IdentityReviewRequired("unsupported or incomplete provider policy schema")
    for key in ("providerId", "productId", "sourceUrl", "evidenceRef"):
        value = claim[key]
        if not isinstance(value, str) or not value or value != value.strip() or any(ord(c) < 32 for c in value):
            raise IdentityReviewRequired(f"invalid provider claim {key}")
    if type(claim["freeTierVerified"]) is not bool or claim["freeTierVerified"] is not True or type(claim["automaticPaidFallback"]) is not bool or claim["automaticPaidFallback"] is not False:
        raise IdentityReviewRequired("provider policy is not explicitly FREE_ONLY")
    artifact_sha = claim["artifactSha256"]
    if not isinstance(artifact_sha, str) or not _SHA.fullmatch(artifact_sha) or hashlib.sha256(evidence.artifact_bytes).hexdigest() != artifact_sha:
        raise IdentityReviewRequired("provider artifact digest mismatch")
    checked_at, policy_until = _time(claim["checkedAt"], "checkedAt"), _time(claim["validUntil"], "validUntil")
    if not checked_at <= now < policy_until:
        raise IdentityReviewRequired("provider policy is not currently valid")

    parsed = urlsplit(claim["sourceUrl"])
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.query or parsed.fragment:
        raise IdentityReviewRequired("provider evidence URL must be a clean HTTPS URL")
    hosts = trusted_provider_hosts.get(claim["providerId"])
    if not isinstance(hosts, (set, frozenset, tuple, list)) or parsed.hostname.lower() not in {str(host).lower() for host in hosts}:
        raise IdentityReviewRequired("provider source host is not independently trusted")

    ref = claim["evidenceRef"]
    approval = approval_ledger.get(ref)
    if not isinstance(approval, ProviderPolicyApproval) or approval.evidence_ref != ref:
        raise IdentityReviewRequired("provider policy lacks independent approval")
    if (not isinstance(approval.approval_id, str) or not approval.approval_id or approval.approval_id != approval.approval_id.strip()
            or not isinstance(approval.claim_sha256, str) or not _SHA.fullmatch(approval.claim_sha256)
            or approval.claim_sha256 != hashlib.sha256(evidence.claim_bytes).hexdigest()
            or type(approval.revoked) is not bool or approval.revoked
            or not (_aware(approval.approved_at) <= now < _aware(approval.valid_until))):
        raise IdentityReviewRequired("provider approval revoked, expired or bound to different claim bytes")
    effective_until = min(policy_until, approval.valid_until)
    return FreeProviderApproval(
        provider_id=claim["providerId"], checked_at=checked_at, valid_until=effective_until,
        free_tier_verified=True, paid_fallback=False, revoked=False,
        review_ref=approval.approval_id, source_url=claim["sourceUrl"],
        artifact_sha256=artifact_sha, approval_id=ref,
    )
