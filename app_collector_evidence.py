from __future__ import annotations


APP_XML_METHODS = {'APP_UI_XML', 'APP_UI_XML_SCROLL'}


def app_collector_evidence_error(payload: object) -> str:
    """Fail closed for structured App UI collector imports."""
    if not isinstance(payload, dict):
        return ''

    source_type = str(payload.get('source_type') or '').strip()
    method = str(payload.get('collection_method') or '').strip()
    if source_type != 'SHORT_DRAMA_APP' or method not in APP_XML_METHODS:
        return ''

    if not str(payload.get('collector_version') or '').strip():
        return 'APP_EVIDENCE_COLLECTOR_VERSION_MISSING'
    if not str(payload.get('collected_at') or '').strip():
        return 'APP_EVIDENCE_COLLECTED_AT_MISSING'
    if not str(payload.get('source_id') or '').strip():
        return 'APP_EVIDENCE_SOURCE_ID_MISSING'
    if not str(payload.get('target_key') or '').strip():
        return 'APP_EVIDENCE_TARGET_KEY_MISSING'

    evidence = payload.get('evidence') if isinstance(payload.get('evidence'), dict) else {}
    if evidence.get('originalSourceType') != 'SHORT_DRAMA_APP':
        return 'APP_EVIDENCE_ORIGINAL_SOURCE_INVALID'
    if evidence.get('semanticVerified') is not True:
        return 'APP_EVIDENCE_TARGET_SEMANTIC_UNVERIFIED'
    if evidence.get('appFocusVerified') is not True:
        return 'APP_EVIDENCE_APP_FOCUS_UNVERIFIED'

    conflicts = payload.get('rank_conflicts')
    if not isinstance(conflicts, list):
        return 'APP_EVIDENCE_RANK_CONFLICT_AUDIT_MISSING'
    if conflicts:
        return 'APP_EVIDENCE_RANK_CONFLICT'

    if method == 'APP_UI_XML':
        if not str(evidence.get('ui_xml') or '').strip():
            return 'APP_EVIDENCE_UI_XML_MISSING'
    else:
        pages = evidence.get('ui_xml_pages')
        if not isinstance(pages, list) or not [x for x in pages if str(x or '').strip()]:
            return 'APP_EVIDENCE_UI_XML_PAGES_MISSING'

    return ''
