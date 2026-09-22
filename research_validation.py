from __future__ import annotations

from typing import Iterable


CORE_FIELDS = [
    'synopsis','genre','lane','audience','storyCore','storySkin','conflict','payoff',
    'localizationLevel','localizationJudgment','mismatch',
]
OPTIONAL_FIELDS = ['openingSummary','openingType','payEpisode','paywallSummary','paywallType']
GENRE_VALUES = {
    '现代都市','校园青春','悬疑惊悚','犯罪黑帮','科幻','奇幻超自然','西幻','历史古装',
    '动作冒险','家庭伦理','其他',
}
AUDIENCE_VALUES = {'男频','女频','泛受众','待确认'}
NEWNESS_VALUES = {'new','old','uncertain'}
CONFIDENCE_VALUES = {'high','medium','low'}
EXTRA_FIELDS = [
    'canonicalTitle','newnessResolution','confidence','missingFields','auditNotes','sourceUrls','needsGPT',
]
REQUIRED_FIELDS = CORE_FIELDS + OPTIONAL_FIELDS + EXTRA_FIELDS


def validate_research_payload(payload: object, *, allowed_source_urls: Iterable[str] | None = None) -> dict:
    """Deterministic final gate for model research output.

    JSON-schema-constrained generation is not write authorization. A result must
    pass this validator again before it can become COMPLETE or write overrides.
    """
    errors: list[str] = []
    if not isinstance(payload, dict):
        return {'ok': False, 'errors': ['payload_not_object'], 'blankFields': list(CORE_FIELDS), 'missingKeys': list(REQUIRED_FIELDS)}

    missing_keys = [key for key in REQUIRED_FIELDS if key not in payload]
    if missing_keys:
        errors.append('missing_keys:' + ','.join(sorted(missing_keys)))

    for key in CORE_FIELDS + OPTIONAL_FIELDS + ['canonicalTitle','newnessResolution','confidence']:
        if key in payload and not isinstance(payload.get(key), str):
            errors.append(f'{key}_not_string')

    genre = payload.get('genre')
    if isinstance(genre, str) and genre not in GENRE_VALUES:
        errors.append('invalid_genre:' + genre)

    audience = payload.get('audience')
    if isinstance(audience, str) and audience not in AUDIENCE_VALUES:
        errors.append('invalid_audience:' + audience)

    newness = payload.get('newnessResolution')
    if isinstance(newness, str) and newness not in NEWNESS_VALUES:
        errors.append('invalid_newnessResolution:' + newness)

    confidence = payload.get('confidence')
    if isinstance(confidence, str) and confidence not in CONFIDENCE_VALUES:
        errors.append('invalid_confidence:' + confidence)

    for key in ('missingFields', 'auditNotes', 'sourceUrls'):
        value = payload.get(key)
        if key in payload and not isinstance(value, list):
            errors.append(f'{key}_not_array')
        elif isinstance(value, list) and any(not isinstance(x, str) for x in value):
            errors.append(f'{key}_contains_non_string')

    if 'needsGPT' in payload and not isinstance(payload.get('needsGPT'), bool):
        errors.append('needsGPT_not_boolean')

    allowed = {str(x).strip() for x in (allowed_source_urls or []) if str(x).strip()}
    source_urls = payload.get('sourceUrls')
    if allowed and isinstance(source_urls, list):
        unexpected = sorted({str(x).strip() for x in source_urls if str(x).strip()} - allowed)
        if unexpected:
            errors.append('sourceUrls_outside_evidence:' + ','.join(unexpected))

    blank_fields = [
        field for field in CORE_FIELDS
        if field in payload and isinstance(payload.get(field), str) and not payload.get(field, '').strip()
    ]
    declared_missing = payload.get('missingFields')
    if isinstance(declared_missing, list):
        undeclared = sorted(set(blank_fields) - {str(x) for x in declared_missing})
        if undeclared:
            errors.append('blank_core_not_declared_missing:' + ','.join(undeclared))

    return {
        'ok': not errors,
        'errors': errors,
        'blankFields': blank_fields,
        'missingKeys': missing_keys,
    }
