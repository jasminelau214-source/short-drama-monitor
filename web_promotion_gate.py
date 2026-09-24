from __future__ import annotations

from datetime import datetime

from source_evidence_guard import audit_source_evidence


def audit_official_web_payload(
    payload: object,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 1800,
) -> dict:
    """Promotion gate for live OFFICIAL_WEB / WEB_SCRAPE payloads.

    Parsing rows is not sufficient for promotion. A live web payload must also
    prove healthy transport, official final host, current evidence and target
    semantics. Historical/manual imports should use an explicit IMPORT workflow
    rather than relabeling stale WEB_SCRAPE evidence.
    """
    if not isinstance(payload, dict):
        return {'pass': False, 'errors': ['WEB_PAYLOAD_INVALID']}

    source_type = str(payload.get('source_type') or '').strip()
    method = str(payload.get('collection_method') or '').strip()
    if source_type != 'OFFICIAL_WEB' or method != 'WEB_SCRAPE':
        return {'pass': True, 'errors': [], 'notApplicable': True}

    evidence = payload.get('evidence') if isinstance(payload.get('evidence'), dict) else {}
    requested_url = str(
        evidence.get('requestedUrl')
        or evidence.get('requested_url')
        or evidence.get('url')
        or ''
    ).strip()
    if not requested_url:
        return {'pass': False, 'errors': ['WEB_EVIDENCE_REQUESTED_URL_MISSING']}

    fetches = evidence.get('pageFetchEvidence')
    if isinstance(fetches, list) and fetches:
        controls = [
            audit_source_evidence(
                expected_url=str(item.get('requestedUrl') or requested_url),
                evidence=item,
                now=now,
                max_age_seconds=max_age_seconds,
                require_semantic=True,
            )
            for item in fetches
            if isinstance(item, dict)
        ]
        if len(controls) != len(fetches):
            return {'pass': False, 'errors': ['WEB_PAGE_FETCH_EVIDENCE_INVALID']}
    else:
        controls = [
            audit_source_evidence(
                expected_url=requested_url,
                evidence={
                    'httpStatus': evidence.get('httpStatus'),
                    'pageUrl': evidence.get('pageUrl') or evidence.get('finalUrl'),
                    'semanticVerified': evidence.get('semanticVerified'),
                    'fetchedAt': evidence.get('fetchedAt'),
                },
                now=now,
                max_age_seconds=max_age_seconds,
                require_semantic=True,
            )
        ]

    errors: list[str] = []
    for index, control in enumerate(controls, start=1):
        for error in control.get('errors') or []:
            errors.append(f'PAGE_{index}:{error}' if len(controls) > 1 else error)

    return {
        'pass': not errors,
        'errors': errors,
        'controls': controls,
        'requestedUrl': requested_url,
        'maxAgeSeconds': max_age_seconds,
    }


def web_promotion_error(payload: object, *, now: datetime | None = None) -> str:
    result = audit_official_web_payload(payload, now=now)
    if result.get('pass'):
        return ''
    errors = result.get('errors') or ['WEB_PROMOTION_GATE_FAILED']
    return 'WEB_PROMOTION_GATE_FAILED:' + ','.join(str(x) for x in errors)
