"""Offline Top10/authority guards against independently reviewed snapshots.

Trusted target registry and review ledgers are caller inputs, not self-report
fields. This module performs no collection, persistence, enqueue or writeback.
"""
from dataclasses import dataclass
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

from drama_identity_v2 import IdentityReviewRequired, canonical_title_key
from identity_evidence_v2 import (
    EvidenceApproval, IdentityEvidence, ObservationIdentity,
    resolve_content_identity,
)

BJT = timezone(timedelta(hours=8))


def _opaque(value):
    if not isinstance(value, str) or not value or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise IdentityReviewRequired("exact nonempty identifier required")
    return value


def _aware(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise IdentityReviewRequired("timezone-aware timestamp required")
    return value


@dataclass(frozen=True)
class RunScope:
    collection_date: date
    platform: str
    source_type: str
    target_key: str

    def __post_init__(self):
        if type(self.collection_date) is not date:
            raise IdentityReviewRequired("exact collection date required")
        ObservationIdentity(self.platform, self.source_type, "scope-validation")
        _opaque(self.target_key)


@dataclass(frozen=True)
class TargetDefinition:
    platform: str
    source_type: str
    target_key: str
    ranking_type: str
    region_filter_available: bool


@dataclass(frozen=True)
class BatchRow:
    rank: int
    title: str
    source_entity_id: str
    record_id: str
    identity_evidence: tuple[IdentityEvidence, ...]


@dataclass(frozen=True)
class RankingRun:
    run_id: str
    scope: RunScope
    observed_at: datetime
    status: str
    batch_complete: bool
    top_n: int
    ranking_type: str
    language: str
    locale: str
    region: str
    rows: tuple[BatchRow, ...]
    ranking_evidence_ref: str


@dataclass(frozen=True)
class ValidatedRow:
    source_entity_id: str
    record_id: str
    canonical_drama_id: str


@dataclass(frozen=True)
class ValidatedBatch:
    run: RankingRun
    snapshot_sha256: str
    rows: tuple[ValidatedRow, ...]


@dataclass(frozen=True)
class AuthoritySelection:
    selected: ValidatedBatch | None
    rejected: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ObservationDecision:
    current: bool
    reason: str
    authoritative_run_id: str | None = None
    snapshot_sha256: str | None = None


def ranking_claim_bytes(run):
    """Deterministic bytes for independently reviewing a complete snapshot.

Including row claim AND artifact digests prevents an identity proof from being
swapped after the ranking snapshot was reviewed. No title-derived global ID.
"""
    if not isinstance(run, RankingRun) or not isinstance(run.scope, RunScope) or type(run.rows) is not tuple:
        raise IdentityReviewRequired("typed immutable ranking snapshot required")
    _aware(run.observed_at)
    rows = []
    for row in run.rows:
        if not isinstance(row, BatchRow) or type(row.identity_evidence) is not tuple:
            raise IdentityReviewRequired("typed immutable rows/evidence required")
        proofs = []
        for item in row.identity_evidence:
            if not isinstance(item, IdentityEvidence) or type(item.claim_bytes) is not bytes or type(item.artifact_bytes) is not bytes:
                raise IdentityReviewRequired("typed identity bytes required")
            proofs.append([hashlib.sha256(item.claim_bytes).hexdigest(), hashlib.sha256(item.artifact_bytes).hexdigest()])
        rows.append(dict(rank=row.rank, title=row.title, sourceEntityId=row.source_entity_id, recordId=row.record_id, identityDigests=proofs))
    data = dict(schemaVersion=1, runId=run.run_id, collectionDate=run.scope.collection_date.isoformat(), platform=run.scope.platform, sourceType=run.scope.source_type, targetKey=run.scope.target_key, observedAt=run.observed_at.astimezone(timezone.utc).isoformat(), status=run.status, batchComplete=run.batch_complete, topN=run.top_n, rankingType=run.ranking_type, language=run.language, locale=run.locale, region=run.region, rows=rows, rankingEvidenceRef=run.ranking_evidence_ref)
    try:
        return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise IdentityReviewRequired("nonserializable snapshot") from exc


def validate_batch(run, *, target_registry, verified_sources, identity_approvals, ranking_approvals, now):
    _aware(now)
    if not all(isinstance(value, Mapping) for value in (target_registry, identity_approvals, ranking_approvals)):
        raise IdentityReviewRequired("trusted registry and approval ledgers required")
    raw = ranking_claim_bytes(run)
    _opaque(run.run_id)
    _opaque(run.ranking_evidence_ref)
    scope = run.scope
    if run.observed_at > now or run.observed_at.astimezone(BJT).date() != scope.collection_date:
        raise IdentityReviewRequired("future or wrong-day capture")
    if run.status != "SUCCEEDED" or run.batch_complete is not True or type(run.top_n) is not int or run.top_n != 10 or len(run.rows) != 10:
        raise IdentityReviewRequired("incomplete/failed/non-Top10 batch")
    target = target_registry.get((scope.platform, scope.source_type, scope.target_key))
    if not isinstance(target, TargetDefinition) or (target.platform, target.source_type, target.target_key) != (scope.platform, scope.source_type, scope.target_key):
        raise IdentityReviewRequired("unknown or mismatched target")
    _opaque(target.ranking_type)
    if type(target.region_filter_available) is not bool:
        raise IdentityReviewRequired("region filter policy unresolved")
    expected_region = "US" if target.region_filter_available else "UNFILTERED"
    if run.ranking_type != target.ranking_type or (run.language, run.locale, run.region) != ("English", "en-US", expected_region):
        raise IdentityReviewRequired("source/ranking/profile mismatch")
    digest = hashlib.sha256(raw).hexdigest()
    approval = ranking_approvals.get(run.ranking_evidence_ref)
    if not isinstance(approval, EvidenceApproval) or approval.evidence_ref != run.ranking_evidence_ref or approval.claim_sha256 != digest or approval.revoked or not (approval.approved_at <= now < approval.valid_until):
        raise IdentityReviewRequired("ranking snapshot lacks current independent approval")
    ranks, titles, entities, records, contents = set(), set(), set(), set(), set()
    validated = []
    for row in run.rows:
        if type(row.rank) is not int or row.rank not in range(1, 11) or row.rank in ranks:
            raise IdentityReviewRequired("duplicate/missing/invalid rank")
        title_key = canonical_title_key(row.title).value
        entity, record = _opaque(row.source_entity_id), _opaque(row.record_id)
        identity = resolve_content_identity(ObservationIdentity(scope.platform, scope.source_type, entity), row.identity_evidence, approval_ledger=identity_approvals, verified_sources=verified_sources, now=now)
        content = identity.canonical_drama_id
        if title_key in titles or entity in entities or record in records or content in contents:
            raise IdentityReviewRequired("duplicate title/entity/record/content")
        ranks.add(row.rank)
        titles.add(title_key)
        entities.add(entity)
        records.add(record)
        contents.add(content)
        validated.append(ValidatedRow(entity, record, content))
    if ranks != set(range(1, 11)):
        raise IdentityReviewRequired("rank coverage incomplete")
    return ValidatedBatch(run, digest, tuple(validated))


def select_authoritative_run(scope, runs, **context):
    """Latest valid same-day/platform/source/target capture wins.

Invalid later runs remain visible in rejected, never replace a valid earlier
run. Distinct valid runs at the latest identical capture time require review;
run IDs and update timestamps are not a fabricated chronology.
"""
    if not isinstance(scope, RunScope) or not isinstance(runs, (tuple, list)):
        raise IdentityReviewRequired("explicit scope and run window required")
    candidates, rejected, seen = [], [], {}
    for run in runs:
        if not isinstance(run, RankingRun):
            raise IdentityReviewRequired("untyped run in authority window")
        if run.scope != scope:
            continue
        try:
            _opaque(run.run_id)
            raw = ranking_claim_bytes(run)
        except IdentityReviewRequired as exc:
            rejected.append((str(run.run_id), str(exc)))
            continue
        if run.run_id in seen:
            if seen[run.run_id] != raw:
                raise IdentityReviewRequired("conflicting payloads for same run ID")
            continue
        seen[run.run_id] = raw
        try:
            candidates.append(validate_batch(run, **context))
        except IdentityReviewRequired as exc:
            rejected.append((run.run_id, str(exc)))
    if not candidates:
        return AuthoritySelection(None, tuple(sorted(rejected)))
    latest_time = max(item.run.observed_at for item in candidates)
    latest = [item for item in candidates if item.run.observed_at == latest_time]
    if len(latest) != 1:
        raise IdentityReviewRequired("ambiguous latest capture chronology")
    return AuthoritySelection(latest[0], tuple(sorted(rejected)))


def assess_current_observation(scope, *, origin_run_id, source_entity_id, record_id, canonical_drama_id, runs, **context):
    """Authority guard only, not permission to spend or COMPLETE/write back.

Future writers must condition their transaction on this snapshot digest and
revalidate immediately before acting; this pure read cannot prevent a race.
"""
    if not isinstance(scope, RunScope):
        raise IdentityReviewRequired("explicit observation scope required")
    for value in (origin_run_id, source_entity_id, record_id, canonical_drama_id):
        _opaque(value)
    if scope.collection_date != _aware(context["now"]).astimezone(BJT).date():
        return ObservationDecision(False, "STALE_COLLECTION_DATE")
    selected = select_authoritative_run(scope, runs, **context).selected
    if selected is None:
        return ObservationDecision(False, "NO_VALID_AUTHORITATIVE_RUN")
    run_id, digest = selected.run.run_id, selected.snapshot_sha256
    if origin_run_id != run_id:
        return ObservationDecision(False, "ORIGIN_SUPERSEDED", run_id, digest)
    wanted = ValidatedRow(source_entity_id, record_id, canonical_drama_id)
    if wanted not in selected.rows:
        return ObservationDecision(False, "ENTITY_RECORD_OR_CONTENT_NOT_CURRENT", run_id, digest)
    return ObservationDecision(True, "CURRENT_OBSERVATION_ONLY", run_id, digest)
