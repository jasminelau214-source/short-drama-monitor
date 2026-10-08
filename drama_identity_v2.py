"""V2 identity primitives. Pure, offline; not wired into application writers.

A title key is a search/matching hint, NEVER proof of global content identity.
Release-marker evidence must be verified by the caller before constructing a
proof. This module validates its shape and anchoring, not the external evidence.
"""
from dataclasses import dataclass
import re

TITLE_KEY_VERSION = "v2-ascii-evidence-anchored-1"
_MARKER = re.compile(r"(?:(?:eng|english)\s+)?dub(?:bed)?", re.IGNORECASE)
_BRACKETS = {"(": ")", "[": "]", "（": "）", "［": "］", "【": "】"}
_SEPARATORS = ":|–—-"


class IdentityReviewRequired(ValueError):
    """Input cannot safely produce an identity primitive."""


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise IdentityReviewRequired(f"{name}: nonempty text required")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise IdentityReviewRequired(f"{name}: control character")
    return value.strip()


@dataclass(frozen=True)
class ReleaseMarkerProof:
    label: str
    edge: str
    evidence_ref: str

    def __post_init__(self):
        label = _text(self.label, "label")
        _text(self.evidence_ref, "evidence_ref")
        if self.edge not in ("prefix", "suffix"):
            raise IdentityReviewRequired("marker must be anchored at prefix or suffix")
        token = label
        if token[0] in _BRACKETS:
            if token[-1] != _BRACKETS[token[0]]:
                raise IdentityReviewRequired("mismatched release-marker brackets")
            token = token[1:-1].strip()
        if not _MARKER.fullmatch(token):
            raise IdentityReviewRequired("unsupported release marker")
        object.__setattr__(self, "label", label)


@dataclass(frozen=True)
class TitleKey:
    value: str
    version: str
    removed_marker_evidence: tuple[str, ...]


def canonical_title_key(title, *, release_proofs=()):
    """Return a versioned title hint, stripping only evidenced edge labels.

No evidence => preserve the label. Invalid/misanchored evidence => review,
not a best-effort strip. Proofs are applied in the supplied order; a proof for
an interior word cannot silently become an edge proof. Non-ASCII letters or
digits require a separately reviewed language contract, never lossy deletion.
"""
    text = _text(title, "title")
    if any(c.isalnum() and not c.isascii() for c in text):
        raise IdentityReviewRequired("unsupported non-ASCII title letters/digits")
    if not isinstance(release_proofs, (tuple, list)):
        raise IdentityReviewRequired("release_proofs must be an ordered sequence")
    evidence = []
    for proof in release_proofs:
        if not isinstance(proof, ReleaseMarkerProof):
            raise IdentityReviewRequired("typed release-marker proof required")
        size = len(proof.label)
        if proof.edge == "prefix":
            candidate, remainder = text[:size], text[size:]
            boundary = remainder[:1]
        else:
            candidate, remainder = text[-size:], text[:-size]
            boundary = remainder[-1:]
        if candidate.casefold() != proof.label.casefold():
            raise IdentityReviewRequired("release marker not at asserted edge")
        # Prevent DUB from being removed from Dublin/Dubbedness, even with a
        # wrongly attributed evidence reference.
        bracketed = proof.label[0] in _BRACKETS
        if boundary and not bracketed and not (boundary.isspace() or boundary in _SEPARATORS):
            raise IdentityReviewRequired("release marker lacks token boundary")
        text = remainder.strip().lstrip(_SEPARATORS).strip() if proof.edge == "prefix" else remainder.strip().rstrip(_SEPARATORS).strip()
        evidence.append(proof.evidence_ref)
    key = re.sub(r"[^a-z0-9]+", "", text.casefold())
    if not key:
        raise IdentityReviewRequired("empty title key")
    return TitleKey(key, TITLE_KEY_VERSION, tuple(evidence))


def publication_identity_key(*, canonical_drama_id, platform, record_id):
    """Bind an upstream-resolved content ID to an exact platform record.

This tuple is not write authorization: COMPLETE, current authoritative run,
source eligibility and conditional/idempotent writes remain separate gates.
Never pass a normalized title as canonical_drama_id.
"""
    values = (
        (canonical_drama_id, "canonical_drama_id"),
        (platform, "platform"),
        (record_id, "record_id"),
    )
    for value, name in values:
        if _text(value, name) != value:
            raise IdentityReviewRequired(f"{name}: opaque identifier has surrounding whitespace")
    return tuple(value for value, _ in values)
