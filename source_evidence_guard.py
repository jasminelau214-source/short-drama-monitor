from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse


def _canonical_host(value: object) -> str:
    host = (urlparse(str(value or '')).hostname or '').casefold().strip('.')
    return host[4:] if host.startswith('www.') else host


def audit_source_evidence(
    *,
    expected_url: str,
    evidence: dict,
    now: datetime | None = None,
    max_age_seconds: int = 900,
    require_semantic: bool = True,
) -> dict:
    """Fail-closed evidence gate for collector promotion.

    A structurally valid row set is insufficient. PASS also requires a healthy
    HTTP response, expected official host, verified target semantics and fresh
    evidence. The caller may disable semantic checking only for a source whose
    contract explicitly does not depend on a named ranking/shelf target.
    """
    evidence = evidence if isinstance(evidence, dict) else {}
    errors: list[str] = []

    raw_status = evidence.get('httpStatus', evidence.get('http_status'))
    try:
        http_status = int(raw_status)
    except (TypeError, ValueError):
        http_status = 0
    if not 200 <= http_status < 400:
        errors.append('HTTP_STATUS_INVALID')

    expected_host = _canonical_host(expected_url)
    page_url = str(
        evidence.get('pageUrl')
        or evidence.get('finalUrl')
        or evidence.get('final_url')
        or ''
    ).strip()
    actual_host = _canonical_host(page_url)
    if not expected_host or not actual_host or actual_host != expected_host:
        errors.append('OFFICIAL_HOST_MISMATCH')

    semantic_verified = evidence.get('semanticVerified', evidence.get('semantic_verified'))
    if require_semantic and semantic_verified is not True:
        errors.append('TARGET_SEMANTIC_UNVERIFIED')

    fetched_raw = str(evidence.get('fetchedAt') or evidence.get('fetched_at') or '').strip()
    age_seconds = None
    if not fetched_raw:
        errors.append('FETCH_TIME_MISSING')
    else:
        try:
            fetched = datetime.fromisoformat(fetched_raw.replace('Z', '+00:00'))
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            age_seconds = (
                current.astimezone(timezone.utc) - fetched.astimezone(timezone.utc)
            ).total_seconds()
            if age_seconds < -60 or age_seconds > max_age_seconds:
                errors.append('FETCH_EVIDENCE_STALE')
        except (TypeError, ValueError):
            errors.append('FETCH_TIME_INVALID')

    return {
        'pass': not errors,
        'errors': errors,
        'httpStatus': http_status or None,
        'expectedHost': expected_host,
        'actualHost': actual_host,
        'pageUrl': page_url,
        'semanticVerified': semantic_verified is True,
        'fetchedAt': fetched_raw,
        'ageSeconds': age_seconds,
        'maxAgeSeconds': max_age_seconds,
    }


def gate_collector_status(raw_status: str, source_control: dict) -> str:
    """A candidate/verified row parse cannot stay PASS when source control fails."""
    raw_status = str(raw_status or '')
    if raw_status in {'PASS_VERIFIED', 'PASS_CANDIDATE'} and not bool(source_control.get('pass')):
        return 'FAIL'
    return raw_status
