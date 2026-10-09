"""Offline identity resolution against a separately trusted review ledger.

The caller supplies the ledger from a protected verification boundary, never
from model output or the same request as evidence. B2 does not implement that
boundary or prove source truth. No network, persistence, ID minting or enqueue.
"""
from dataclasses import dataclass
from collections.abc import Mapping
from datetime import datetime
import hashlib
import json
import re

from drama_identity_v2 import IdentityReviewRequired

ACTIVE_PLATFORMS = frozenset({"DramaBox", "FlexTV", "GoodShort", "MoboReels", "NetShort", "ReelShort"})
SUPPORTED_SOURCE_TYPES = frozenset({"OFFICIAL_WEB", "SHORT_DRAMA_APP"})
_FIELDS = {"schemaVersion", "platform", "sourceType", "sourceEntityId", "canonicalDramaId", "evidenceRef", "artifactSha256"}
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _opaque(value, name):
    if not isinstance(value, str) or not value or value.strip() != value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise IdentityReviewRequired(f"{name}: exact nonempty identifier required")
    return value


def _aware(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise IdentityReviewRequired("timezone-aware decision time required")
    return value


@dataclass(frozen=True)
class ObservationIdentity:
    platform: str
    source_type: str
    source_entity_id: str

    def __post_init__(self):
        for value, name in ((self.platform, "platform"), (self.source_type, "source_type"), (self.source_entity_id, "source_entity_id")):
            _opaque(value, name)
        if self.platform not in ACTIVE_PLATFORMS or self.source_type not in SUPPORTED_SOURCE_TYPES:
            raise IdentityReviewRequired("platform/source is outside candidate scope")


@dataclass(frozen=True)
class EvidenceApproval:
    """Pre-existing external review, bound to exact claim bytes and expiry."""
    evidence_ref: str
    claim_sha256: str
    decision_id: str
    approved_at: datetime
    valid_until: datetime
    revoked: bool = False

    def __post_init__(self):
        _opaque(self.evidence_ref, "evidence_ref")
        _opaque(self.decision_id, "decision_id")
        if not isinstance(self.claim_sha256, str) or not _SHA256.fullmatch(self.claim_sha256):
            raise IdentityReviewRequired("invalid approved claim digest")
        if type(self.revoked) is not bool or _aware(self.valid_until) <= _aware(self.approved_at):
            raise IdentityReviewRequired("invalid approval validity")


@dataclass(frozen=True)
class IdentityEvidence:
    claim_bytes: bytes
    artifact_bytes: bytes


@dataclass(frozen=True)
class ResolvedContentIdentity:
    canonical_drama_id: str
    observation: ObservationIdentity
    evidence_refs: tuple[str, ...]
    decision_ids: tuple[str, ...]
    resolver_version: str = "v2-reviewed-binding-1"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IdentityReviewRequired("duplicate JSON claim key")
        result[key] = value
    return result


def _claim(evidence):
    if not isinstance(evidence, IdentityEvidence) or type(evidence.claim_bytes) is not bytes or type(evidence.artifact_bytes) is not bytes or not evidence.artifact_bytes:
        raise IdentityReviewRequired("exact claim and nonempty artifact bytes required")
    try:
        data = json.loads(evidence.claim_bytes.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IdentityReviewRequired("malformed identity claim") from exc
    if type(data) is not dict or set(data) != _FIELDS or type(data["schemaVersion"]) is not int or data["schemaVersion"] != 1:
        raise IdentityReviewRequired("unsupported or incomplete identity claim schema")
    for key in _FIELDS - {"schemaVersion"}:
        _opaque(data[key], key)
    if not _SHA256.fullmatch(data["artifactSha256"]) or hashlib.sha256(evidence.artifact_bytes).hexdigest() != data["artifactSha256"]:
        raise IdentityReviewRequired("artifact digest mismatch")
    return data


def resolve_content_identity(observation, evidence_items, *, approval_ledger, verified_sources, now):
    """Resolve an existing reviewed binding; reject all ambiguity.

Titles and normalized title keys are intentionally absent. Source entity IDs
are namespaced by platform AND source type. All supplied evidence must bind
that exact observation; a different subject cannot authorize this subject.
Concurrent global first-seen uniqueness and authoritative-run validity are
separate gates and must be rechecked by future callers.
"""
    if not isinstance(observation, ObservationIdentity):
        raise IdentityReviewRequired("typed observation required")
    _aware(now)
    if not isinstance(approval_ledger, Mapping) or not isinstance(verified_sources, (set, frozenset)):
        raise IdentityReviewRequired("independent ledger and source registry required")
    if (observation.platform, observation.source_type) not in verified_sources:
        raise IdentityReviewRequired("source has no independent verification")
    if not isinstance(evidence_items, (tuple, list)) or not evidence_items:
        raise IdentityReviewRequired("identity evidence missing")
    identities = set()
    decisions = {}
    for evidence in evidence_items:
        data = _claim(evidence)
        subject = ObservationIdentity(data["platform"], data["sourceType"], data["sourceEntityId"])
        if subject != observation:
            raise IdentityReviewRequired("evidence subject mismatch")
        ref = data["evidenceRef"]
        approval = approval_ledger.get(ref)
        if not isinstance(approval, EvidenceApproval) or approval.evidence_ref != ref:
            raise IdentityReviewRequired("independent approval missing")
        if approval.revoked or not (approval.approved_at <= now < approval.valid_until):
            raise IdentityReviewRequired("approval revoked, expired or not yet valid")
        if hashlib.sha256(evidence.claim_bytes).hexdigest() != approval.claim_sha256:
            raise IdentityReviewRequired("claim differs from independently approved bytes")
        identities.add(data["canonicalDramaId"])
        decisions[ref] = approval.decision_id
    if len(identities) != 1:
        raise IdentityReviewRequired("conflicting content identity evidence")
    refs = tuple(sorted(decisions))
    return ResolvedContentIdentity(next(iter(identities)), observation, refs, tuple(decisions[ref] for ref in refs))
