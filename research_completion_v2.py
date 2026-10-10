"""Shared candidate completion/writeback gate using reviewed field evidence.

Context/ledgers must come from a protected adapter, never model or task JSON.
This module checks bindings, not the truth of source prose or review decisions.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from urllib.parse import urlparse

from batch_authority_v2 import RunScope, assess_current_observation
from drama_identity_v2 import IdentityReviewRequired, publication_identity_key
from identity_evidence_v2 import EvidenceApproval
from research_safety import safe_public_url

CORE_FIELDS = ['synopsis','genre','lane','audience','storyCore','storySkin','conflict','payoff',
               'localizationLevel','localizationJudgment','mismatch']
OPTIONAL_FIELDS = ['openingSummary','openingType','payEpisode','paywallSummary','paywallType']
GENRE_VALUES = ['现代都市','校园青春','悬疑惊悚','犯罪黑帮','科幻','奇幻超自然','西幻','历史古装','动作冒险','家庭伦理','其他']
AUDIENCE_VALUES = ['男频','女频','泛受众','待确认']
TEXT_FIELDS = CORE_FIELDS + OPTIONAL_FIELDS + ['canonicalTitle']
LIST_FIELDS = ['missingFields','auditNotes','sourceUrls']
RESULT_FIELDS = set(TEXT_FIELDS + LIST_FIELDS + ['newnessResolution','confidence','needsGPT'])
_SHA256 = re.compile(r'[0-9a-f]{64}')
_PLACEHOLDERS = {'unknown','n/a','na','none','null','tbd','not available','not provided',
                 '待确认','待补充','未知','暂无','无法确认','无资料','未提供'}


def research_schema():
    props = {name: {'type':'string'} for name in TEXT_FIELDS}
    props['genre'] = {'type':'string','enum':GENRE_VALUES}
    props['audience'] = {'type':'string','enum':AUDIENCE_VALUES}
    props.update({
        'newnessResolution':{'type':'string','enum':['new','old','uncertain']},
        'confidence':{'type':'string','enum':['high','medium','low']},
        **{name:{'type':'array','items':{'type':'string'}} for name in LIST_FIELDS},
        'needsGPT':{'type':'boolean'},
    })
    return {'type':'object','properties':props,'required':list(props),'additionalProperties':False}


def field_value_sha256(value):
    if type(value) is not str:
        raise IdentityReviewRequired('field value must be exact text')
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def parse_research_response(text):
    def unique_result(pairs):
        result = {}
        for key,value in pairs:
            if key in result:
                raise IdentityReviewRequired('RESEARCH_RESULT_DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    if type(text) is not str:
        raise IdentityReviewRequired('RESEARCH_RESULT_INVALID_JSON')
    try:
        return json.loads(text, object_pairs_hook=unique_result)
    except json.JSONDecodeError as exc:
        raise IdentityReviewRequired('RESEARCH_RESULT_INVALID_JSON') from exc


@dataclass(frozen=True)
class FieldEvidence:
    claim_bytes: bytes
    artifact_bytes: bytes


@dataclass(frozen=True)
class CompletionContext:
    scope: RunScope
    origin_run_id: str
    source_entity_id: str
    record_id: str
    canonical_drama_id: str
    runs: tuple
    authority_context: Mapping
    captured_sources: tuple
    field_evidence: tuple
    field_approvals: Mapping


@dataclass(frozen=True)
class CompletionDecision:
    status: str
    reason: str
    missing_fields: tuple[str, ...] = ()
    result_sha256: str | None = None
    authoritative_run_id: str | None = None
    authority_snapshot_sha256: str | None = None


class ResearchWritebackRejected(RuntimeError):
    """A business safety refusal; Worker must route it to review, not retry."""


def validate_completion_context(task, context, *, now):
    """Re-run B3/B2 over supplied snapshots; no cached decision authorizes IO."""
    if not isinstance(context, CompletionContext) or not isinstance(context.scope, RunScope):
        raise IdentityReviewRequired('PROTECTED_COMPLETION_CONTEXT_REQUIRED')
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise IdentityReviewRequired('AWARE_COMPLETION_TIME_REQUIRED')
    if type(task) is not dict or task.get('status') != 'RESEARCHING':
        raise IdentityReviewRequired('RESEARCH_TASK_NOT_RESEARCHING')
    bindings = {
        'collection_date':context.scope.collection_date.isoformat(), 'platform':context.scope.platform,
        'source_type':context.scope.source_type, 'target_key':context.scope.target_key,
        'analysis_run_id':context.origin_run_id, 'source_entity_id':context.source_entity_id,
        'record_id':context.record_id, 'canonical_drama_id':context.canonical_drama_id,
    }
    if any(type(task.get(key)) is not str or task.get(key) != value for key, value in bindings.items()):
        raise IdentityReviewRequired('RESEARCH_TASK_BINDING_MISMATCH')
    if not isinstance(context.authority_context, Mapping) or set(context.authority_context) != {
        'target_registry','verified_sources','identity_approvals','ranking_approvals'
    }:
        raise IdentityReviewRequired('PROTECTED_AUTHORITY_INPUTS_REQUIRED')
    decision = assess_current_observation(
        context.scope, origin_run_id=context.origin_run_id, source_entity_id=context.source_entity_id,
        record_id=context.record_id, canonical_drama_id=context.canonical_drama_id,
        runs=context.runs, now=now, **context.authority_context,
    )
    if not decision.current:
        raise IdentityReviewRequired(decision.reason)
    run = next(run for run in context.runs if run.run_id == decision.authoritative_run_id)
    row = next(row for row in run.rows if row.record_id == context.record_id)
    if type(task.get('title')) is not str or task['title'] != row.title:
        raise IdentityReviewRequired('RESEARCH_TASK_TITLE_DIFFERS_FROM_BOUND_OBSERVATION')
    return decision


def _usable_text(value):
    return bool(value.strip()) and value.strip().casefold().strip('.。') not in _PLACEHOLDERS


def _schema_error(result):
    if type(result) is not dict or not RESULT_FIELDS <= set(result) or set(result) - RESULT_FIELDS - {'searchMeta'}:
        return True
    if 'searchMeta' in result and type(result['searchMeta']) is not dict:
        return True
    if any(type(result[field]) is not str or len(result[field]) > 6000 or '\x00' in result[field] for field in TEXT_FIELDS):
        return True
    if (type(result['needsGPT']) is not bool
            or type(result['confidence']) is not str or type(result['newnessResolution']) is not str
            or result['confidence'] not in {'high','medium','low'}
            or result['newnessResolution'] not in {'new','old','uncertain'}
            or result['genre'] not in GENRE_VALUES or result['audience'] not in AUDIENCE_VALUES):
        return True
    for field in LIST_FIELDS:
        values = result[field]
        if type(values) is not list or any(type(value) is not str or not value.strip() or len(value) > 2048 for value in values):
            return True
        if len(values) != len(set(values)):
            return True
    return not set(result['missingFields']) <= set(CORE_FIELDS + OPTIONAL_FIELDS + ['canonicalTitle'])


def _unique(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise IdentityReviewRequired('DUPLICATE_FIELD_EVIDENCE_KEY')
        data[key] = value
    return data


def _reviewed_fields(result, sources, context, now):
    """Bind cited URLs + exact captured bytes + exact values to reviewed claims."""
    if not isinstance(sources, (tuple,list)) or not isinstance(context.field_approvals, Mapping):
        raise IdentityReviewRequired('RESEARCH_EVIDENCE_INPUTS_REQUIRED')
    artifacts = {}
    for source in sources:
        if (type(source) is not dict or type(source.get('url')) is not str
                or type(source.get('content')) is not str or not source['content']
                or source['url'] in artifacts):
            raise IdentityReviewRequired('RESEARCH_SOURCE_SNAPSHOT_INVALID')
        artifacts[source['url']] = source['content'].encode('utf-8')
    cited = set(result['sourceUrls'])
    for url in cited:
        parsed = urlparse(url)
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or safe_public_url(url) != url or url not in artifacts):
            raise IdentityReviewRequired('RESEARCH_CITATION_NOT_IN_CAPTURED_SOURCES')
    if not isinstance(context.field_evidence, (tuple,list)) or not context.field_evidence:
        return set()
    supported, reviewed_urls, seen_refs = set(), set(), set()
    fields = {'schemaVersion','canonicalDramaId','url','artifactSha256','fieldValueSha256','evidenceRef'}
    for evidence in context.field_evidence:
        if (not isinstance(evidence, FieldEvidence) or type(evidence.claim_bytes) is not bytes
                or type(evidence.artifact_bytes) is not bytes or not evidence.artifact_bytes):
            raise IdentityReviewRequired('RESEARCH_FIELD_EVIDENCE_INVALID')
        try:
            claim = json.loads(evidence.claim_bytes.decode('utf-8'), object_pairs_hook=_unique)
        except (UnicodeError,json.JSONDecodeError) as exc:
            raise IdentityReviewRequired('RESEARCH_FIELD_EVIDENCE_INVALID_JSON') from exc
        if (type(claim) is not dict or set(claim) != fields
                or type(claim['schemaVersion']) is not int or claim['schemaVersion'] != 1
                or any(type(claim[key]) is not str or not claim[key].strip() for key in fields - {'schemaVersion','fieldValueSha256'})
                or type(claim['fieldValueSha256']) is not dict or not claim['fieldValueSha256']
                or not set(claim['fieldValueSha256']) <= set(TEXT_FIELDS)
                or any(type(digest) is not str or not _SHA256.fullmatch(digest) for digest in claim['fieldValueSha256'].values())):
            raise IdentityReviewRequired('RESEARCH_FIELD_CLAIM_SCHEMA_INVALID')
        ref, url = claim['evidenceRef'], claim['url']
        approval = context.field_approvals.get(ref)
        if (ref in seen_refs or not isinstance(approval, EvidenceApproval) or approval.evidence_ref != ref
                or approval.revoked or not (approval.approved_at <= now < approval.valid_until)
                or hashlib.sha256(evidence.claim_bytes).hexdigest() != approval.claim_sha256):
            raise IdentityReviewRequired('RESEARCH_FIELD_REVIEW_MISSING_OR_CHANGED')
        seen_refs.add(ref)
        if claim['canonicalDramaId'] != context.canonical_drama_id:
            raise IdentityReviewRequired('RESEARCH_EVIDENCE_CONTENT_IDENTITY_CONFLICT')
        if (not _SHA256.fullmatch(claim['artifactSha256'])
                or hashlib.sha256(evidence.artifact_bytes).hexdigest() != claim['artifactSha256']
                or artifacts.get(url) != evidence.artifact_bytes):
            raise IdentityReviewRequired('RESEARCH_EVIDENCE_ARTIFACT_CHANGED')
        for field, digest in claim['fieldValueSha256'].items():
            if digest != field_value_sha256(result[field]):
                raise IdentityReviewRequired('RESEARCH_EVIDENCE_FIELD_VALUE_CHANGED')
        if url in cited:
            supported.update(claim['fieldValueSha256'])
            reviewed_urls.add(url)
    if cited != reviewed_urls:
        raise IdentityReviewRequired('RESEARCH_CITATION_WITHOUT_REVIEWED_SUPPORT')
    return supported


def assess_research_completion(result, *, task, sources, context, now):
    if _schema_error(result):
        return CompletionDecision('REVIEW_REQUIRED','RESEARCH_RESULT_SCHEMA_INVALID')
    missing = set(result['missingFields']) | {field for field in TEXT_FIELDS if not _usable_text(result[field])}
    try:
        authority = validate_completion_context(task, context, now=now)
        if result['newnessResolution'] == 'uncertain':
            return CompletionDecision('REVIEW_REQUIRED','RESEARCH_IDENTITY_UNRESOLVED',tuple(sorted(missing)))
        if result['needsGPT'] or result['confidence'] == 'low' or set(CORE_FIELDS + ['canonicalTitle']) & missing or not result['sourceUrls']:
            return CompletionDecision('NEEDS_GPT','RESEARCH_CORE_OR_EVIDENCE_INSUFFICIENT',tuple(sorted(missing)))
        empty_optional = {field for field in OPTIONAL_FIELDS if not _usable_text(result[field])}
        if not empty_optional <= set(result['missingFields']):
            return CompletionDecision('REVIEW_REQUIRED','RESEARCH_OPTIONAL_MISSING_NOT_DECLARED',tuple(sorted(missing)))
        if any(field in result['missingFields'] and _usable_text(result[field]) for field in OPTIONAL_FIELDS):
            return CompletionDecision('REVIEW_REQUIRED','RESEARCH_OPTIONAL_MISSING_CONTRADICTION',tuple(sorted(missing)))
        supported = _reviewed_fields(result, sources, context, now)
        required = {field for field in TEXT_FIELDS if _usable_text(result[field])}
        if not required <= supported:
            return CompletionDecision('NEEDS_GPT','RESEARCH_FIELD_SUPPORT_INSUFFICIENT',tuple(sorted(missing | (required-supported))))
        payload = json.dumps({key:result[key] for key in sorted(RESULT_FIELDS)},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
        return CompletionDecision('COMPLETE','RESEARCH_COMPLETE_VALIDATED',tuple(sorted(missing)),
                                  hashlib.sha256(payload).hexdigest(),authority.authoritative_run_id,authority.snapshot_sha256)
    except (IdentityReviewRequired,ValueError,TypeError,KeyError,StopIteration) as exc:
        return CompletionDecision('REVIEW_REQUIRED',str(exc) or 'RESEARCH_CONTEXT_INVALID',tuple(sorted(missing)))


def select_same_platform_record(records, *, context):
    if not isinstance(context, CompletionContext) or type(records) is not list:
        raise IdentityReviewRequired('PROTECTED_WRITEBACK_CONTEXT_REQUIRED')
    candidates = [record for record in records if type(record) is dict and record.get('id') == context.record_id]
    if len(candidates) != 1:
        raise IdentityReviewRequired('RESEARCH_RECORD_NOT_FOUND_OR_AMBIGUOUS')
    record = candidates[0]
    if record.get('app') != context.scope.platform:
        raise IdentityReviewRequired('RESEARCH_RECORD_NOT_FOUND_SAME_PLATFORM')
    actual = publication_identity_key(canonical_drama_id=record.get('canonicalDramaId'),
                                      platform=record.get('app'),record_id=record.get('id'))
    expected = publication_identity_key(canonical_drama_id=context.canonical_drama_id,
                                        platform=context.scope.platform,record_id=context.record_id)
    if actual != expected or record.get('sourceEntityId') != context.source_entity_id or record.get('sourceType') != context.scope.source_type:
        raise IdentityReviewRequired('RESEARCH_RECORD_IDENTITY_BINDING_MISMATCH')
    return record
