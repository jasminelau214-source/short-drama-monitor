from __future__ import annotations

import re


# JSM Core Contract v1: release/language labels may decorate a distribution title
# without creating a new drama identity. Only anchored labels are removable.
_DUB_MARKER = r'(?:eng(?:lish)?\s+)?dub(?:bed)?'
_BRACKETED_DUB_MARKER = rf'[\[(（［【]\s*{_DUB_MARKER}\s*[\])）］】]'


def strip_release_markers(value: object) -> str:
    """Remove supported anchored dub/release labels without touching title content.

    Examples treated as the same title:
    - "(DUBBED) Justice in Blood"
    - "[ENG DUB] Flash Marriage CEO Spoils Me a Lot"
    - "Ruling Over All I See (DUBBED)"

    A word such as "dubbed" inside the actual title is preserved.
    """
    text = str(value or '').casefold().strip()
    if not text:
        return ''

    # Bracketed labels are unambiguous distribution markers at either edge.
    text = re.sub(rf'^\s*{_BRACKETED_DUB_MARKER}\s*[:|–—-]*\s*', '', text)
    text = re.sub(rf'\s*[:|–—-]*\s*{_BRACKETED_DUB_MARKER}\s*$', '', text)

    # Unbracketed leading labels require an explicit separator, except the
    # explicit ENG/ENGLISH DUB forms which are sufficiently unambiguous.
    text = re.sub(
        rf'^\s*(?:(?:eng(?:lish)?\s+)dub(?:bed)?\b\s*[:|–—-]+\s*|(?:eng(?:lish)?\s+)dub(?:bed)?\b\s+)',
        '',
        text,
    )

    # Plain trailing "dub/dubbed" requires a separator. Explicit ENG/ENGLISH
    # forms may be space-separated.
    text = re.sub(rf'\s*[:|–—-]+\s*dub(?:bed)?\s*$', '', text)
    text = re.sub(rf'\s+(?:eng(?:lish)?\s+)dub(?:bed)?\s*$', '', text)
    return text.strip()


def normalize_title(value: object) -> str:
    """Return the canonical JSM title identity key.

    Contract v1 intentionally preserves the existing ASCII identity semantics
    while centralizing release-marker handling. Non-Latin-only titles currently
    normalize to an empty key and must not be silently assigned an identity.
    """
    text = strip_release_markers(value)
    return re.sub(r'[^a-z0-9]+', '', text)


def same_title(left: object, right: object) -> bool:
    """True only when both titles produce the same non-empty identity key."""
    left_norm = normalize_title(left)
    right_norm = normalize_title(right)
    return bool(left_norm and left_norm == right_norm)
