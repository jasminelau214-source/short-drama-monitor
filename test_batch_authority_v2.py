"""Synthetic Top10 snapshots/review ledgers; no production incident replay."""
from dataclasses import replace
from datetime import date, timedelta, timezone, datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
for name in ("drama_identity_v2", "identity_evidence_v2", "batch_authority_v2"):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
b = sys.modules["batch_authority_v2"]
i = sys.modules["identity_evidence_v2"]
Review = sys.modules["drama_identity_v2"].IdentityReviewRequired
NOW = datetime(2026, 10, 9, 8, 25, tzinfo=timezone.utc)


class BatchAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.context = dict(target_registry={}, verified_sources=set(), identity_approvals={}, ranking_approvals={}, now=NOW)
        self.scope = b.RunScope(date(2026, 10, 9), "DramaBox", "OFFICIAL_WEB", "fixture_top10_v2")
        self.run = self.make_run()

    def approve(self, ref, raw):
        return i.EvidenceApproval(ref, hashlib.sha256(raw).hexdigest(), "decision:" + ref, NOW - timedelta(days=2), NOW + timedelta(hours=1))

    def review(self, run):
        self.context["ranking_approvals"][run.ranking_evidence_ref] = self.approve(run.ranking_evidence_ref, b.ranking_claim_bytes(run))
        return run

    def make_run(self, name="run:1", scope=None, at=None, offset=0):
        scope = scope or self.scope
        self.context["target_registry"][(scope.platform, scope.source_type, scope.target_key)] = b.TargetDefinition(scope.platform, scope.source_type, scope.target_key, "FIXTURE_VERIFIED_TOP", True)
        self.context["verified_sources"].add((scope.platform, scope.source_type))
        rows = []
        for rank in range(1, 11):
            entity = "entity:" + str(rank + offset)
            ref = scope.platform + ":" + scope.source_type + ":" + entity
            artifact = ("fixture source " + ref).encode()
            raw = json.dumps(dict(schemaVersion=1, platform=scope.platform, sourceType=scope.source_type, sourceEntityId=entity, canonicalDramaId="content:" + str(rank + offset), evidenceRef=ref, artifactSha256=hashlib.sha256(artifact).hexdigest()), sort_keys=True).encode()
            evidence = i.IdentityEvidence(raw, artifact)
            self.context["identity_approvals"][ref] = self.approve(ref, raw)
            rows.append(b.BatchRow(rank, "Drama " + str(rank + offset), entity, "record:" + str(rank + offset), (evidence,)))
        run = b.RankingRun(name, scope, at or NOW - timedelta(minutes=25), "SUCCEEDED", True, 10, "FIXTURE_VERIFIED_TOP", "English", "en-US", "US", tuple(rows), "ranking:" + name)
        return self.review(run)

    def validate(self, run):
        return b.validate_batch(run, **self.context)

    def select(self, runs, scope=None):
        return b.select_authoritative_run(scope or self.scope, runs, **self.context)

    def assess(self, runs, run=None, **overrides):
        run = run or self.run
        row = run.rows[0]
        args = dict(origin_run_id=run.run_id, source_entity_id=row.source_entity_id, record_id=row.record_id, canonical_drama_id="content:" + row.source_entity_id.split(":")[-1])
        args.update(overrides)
        return b.assess_current_observation(run.scope, runs=runs, **args, **self.context)

    def test_complete_top10_binds_identity_and_snapshot(self):
        validated = self.validate(self.run)
        self.assertEqual(len(validated.rows), 10)
        self.assertEqual(validated.rows[0].canonical_drama_id, "content:1")
        self.assertEqual(validated.snapshot_sha256, hashlib.sha256(b.ranking_claim_bytes(self.run)).hexdigest())

    def test_partial_failed_and_fake_boolean_cannot_publish(self):
        for changed in (replace(self.run, batch_complete=False), replace(self.run, batch_complete=1), replace(self.run, status="FAILED"), replace(self.run, status="PARTIAL"), replace(self.run, top_n=60), replace(self.run, top_n=True)):
            with self.subTest(changed=changed.status):
                with self.assertRaises(Review):
                    self.validate(self.review(changed))

    def test_missing_extra_duplicate_and_noninteger_ranks_rejected(self):
        for rows in (self.run.rows[:-1], self.run.rows + (self.run.rows[0],), (replace(self.run.rows[0], rank=2),) + self.run.rows[1:], (replace(self.run.rows[0], rank=True),) + self.run.rows[1:], (replace(self.run.rows[0], rank="1"),) + self.run.rows[1:], (replace(self.run.rows[0], rank=11),) + self.run.rows[1:]):
            with self.subTest(size=len(rows)):
                with self.assertRaises(Review):
                    self.validate(self.review(replace(self.run, rows=rows)))

    def test_duplicate_titles_and_collision_hints_rejected(self):
        for first, second in (("Drama 1", "Drama 1"), ("A-B", "AB")):
            rows = (replace(self.run.rows[0], title=first), replace(self.run.rows[1], title=second)) + self.run.rows[2:]
            with self.assertRaises(Review):
                self.validate(self.review(replace(self.run, rows=rows)))

    def test_empty_titles_entities_records_and_unresolved_identity_rejected(self):
        for row in (replace(self.run.rows[0], title=""), replace(self.run.rows[0], source_entity_id=""), replace(self.run.rows[0], record_id=""), replace(self.run.rows[0], identity_evidence=())):
            with self.assertRaises(Review):
                self.validate(self.review(replace(self.run, rows=(row,) + self.run.rows[1:])))

    def test_duplicate_record_and_content_rejected(self):
        rows = (self.run.rows[0], replace(self.run.rows[1], record_id=self.run.rows[0].record_id)) + self.run.rows[2:]
        with self.assertRaises(Review):
            self.validate(self.review(replace(self.run, rows=rows)))
        row = self.run.rows[1]
        evidence = row.identity_evidence[0]
        raw = evidence.claim_bytes.replace(b"content:2", b"content:1")
        ref = json.loads(raw)["evidenceRef"]
        self.context["identity_approvals"][ref] = self.approve(ref, raw)
        row = replace(row, identity_evidence=(i.IdentityEvidence(raw, evidence.artifact_bytes),))
        with self.assertRaises(Review):
            self.validate(self.review(replace(self.run, rows=(self.run.rows[0], row) + self.run.rows[2:])))

    def test_profile_target_and_ranking_semantics_rejected(self):
        for changed in (replace(self.run, ranking_type="PAGE_ORDER"), replace(self.run, locale="en-GB"), replace(self.run, language="Unknown"), replace(self.run, region="UK"), replace(self.run, scope=replace(self.scope, target_key="unknown"))):
            with self.assertRaises(Review):
                self.validate(self.review(changed))

    def test_unfiltered_region_must_be_explicit(self):
        target_key = (self.scope.platform, self.scope.source_type, self.scope.target_key)
        self.context["target_registry"][target_key] = replace(self.context["target_registry"][target_key], region_filter_available=False)
        with self.assertRaises(Review):
            self.validate(self.run)
        self.assertEqual(len(self.validate(self.review(replace(self.run, region="UNFILTERED"))).rows), 10)

    def test_self_report_complete_does_not_replace_ranking_witness(self):
        self.context["ranking_approvals"].clear()
        with self.assertRaises(Review):
            self.validate(self.run)

    def test_snapshot_or_identity_swap_after_review_rejected(self):
        with self.assertRaises(Review):
            self.validate(replace(self.run, rows=tuple(reversed(self.run.rows))))
        row = replace(self.run.rows[0], identity_evidence=self.run.rows[1].identity_evidence)
        with self.assertRaises(Review):
            self.validate(replace(self.run, rows=(row,) + self.run.rows[1:]))

    def test_latest_capture_wins_independent_of_input_order(self):
        later = self.make_run("run:2", at=NOW - timedelta(minutes=10), offset=10)
        for runs in ([self.run, later], [later, self.run]):
            self.assertEqual(self.select(runs).selected.run.run_id, later.run_id)

    def test_later_failed_incomplete_or_malformed_cannot_override(self):
        later = self.make_run("run:2", at=NOW - timedelta(minutes=10))
        for bad in (replace(later, status="FAILED"), replace(later, batch_complete=False), replace(later, rows=later.rows[:-1]), replace(later, rows=[*later.rows])):
            selection = self.select([self.run, bad])
            self.assertEqual(selection.selected.run.run_id, self.run.run_id)
            self.assertEqual(len(selection.rejected), 1)

    def test_other_date_platform_source_or_target_cannot_supersede(self):
        scopes = (replace(self.scope, collection_date=date(2026, 10, 8)), replace(self.scope, platform="NetShort"), replace(self.scope, source_type="SHORT_DRAMA_APP"), replace(self.scope, target_key="fixture_other_top10"))
        for idx, scope in enumerate(scopes):
            later = self.make_run("other:" + str(idx), scope=scope, at=NOW - timedelta(minutes=10))
            self.assertEqual(self.select([self.run, later]).selected.run.run_id, self.run.run_id)

    def test_same_timestamp_and_conflicting_run_id_require_review(self):
        other = self.make_run("run:2")
        with self.assertRaises(Review):
            self.select([self.run, other])
        other = replace(other, run_id=self.run.run_id)
        with self.assertRaises(Review):
            self.select([self.run, other])
        self.assertEqual(self.select([self.run, self.run]).selected.run.run_id, self.run.run_id)

    def test_superseded_origin_rejected_even_if_same_entity_remains(self):
        later = self.make_run("run:2", at=NOW - timedelta(minutes=10))
        decision = self.assess([self.run, later])
        self.assertFalse(decision.current)
        self.assertEqual(decision.reason, "ORIGIN_SUPERSEDED")
        self.assertEqual(decision.authoritative_run_id, later.run_id)

    def test_removed_entity_and_wrong_record_or_content_rejected(self):
        for args in (dict(source_entity_id="entity:missing"), dict(record_id="record:other"), dict(canonical_drama_id="content:other")):
            decision = self.assess([self.run], **args)
            self.assertFalse(decision.current)
            self.assertEqual(decision.reason, "ENTITY_RECORD_OR_CONTENT_NOT_CURRENT")

    def test_current_observation_has_snapshot_for_future_conditional_write(self):
        decision = self.assess([self.run])
        self.assertTrue(decision.current)
        self.assertEqual(decision.reason, "CURRENT_OBSERVATION_ONLY")
        self.assertEqual(decision.snapshot_sha256, self.validate(self.run).snapshot_sha256)

    def test_guard_rechecks_after_run_changes_between_call_and_write(self):
        self.assertTrue(self.assess([self.run]).current)
        later = self.make_run("run:2", at=NOW - timedelta(minutes=10), offset=10)
        self.assertFalse(self.assess([self.run, later]).current)

    def test_stale_collection_date_not_relabelled_today(self):
        old = self.make_run("old:1", scope=replace(self.scope, collection_date=date(2026, 10, 8)), at=NOW - timedelta(days=1))
        self.assertIsNotNone(self.select([old], old.scope).selected)
        decision = self.assess([old], old)
        self.assertFalse(decision.current)
        self.assertEqual(decision.reason, "STALE_COLLECTION_DATE")

    def test_future_wrong_day_or_naive_capture_rejected(self):
        for at in (NOW + timedelta(minutes=1), NOW - timedelta(days=1), NOW.replace(tzinfo=None)):
            with self.assertRaises(Review):
                self.validate(replace(self.run, observed_at=at))
        with self.assertRaises(Review):
            b.RunScope(NOW, "DramaBox", "OFFICIAL_WEB", "target")

    def test_expired_or_revoked_review_rechecked_at_guard_time(self):
        approval = self.context["ranking_approvals"][self.run.ranking_evidence_ref]
        for bad in (replace(approval, revoked=True), replace(approval, valid_until=NOW)):
            self.context["ranking_approvals"][self.run.ranking_evidence_ref] = bad
            self.assertFalse(self.assess([self.run]).current)

    def test_five_of_six_isolation_preserves_failed_platform_gap(self):
        selected, missing = [], []
        for platform in sorted(i.ACTIVE_PLATFORMS):
            scope = replace(self.scope, platform=platform)
            run = self.make_run("six:" + platform, scope=scope)
            if platform == "MoboReels":
                run = replace(run, status="FAILED", batch_complete=False, rows=())
            selection = self.select([run], scope)
            (selected if selection.selected else missing).append(platform)
        self.assertEqual(len(selected), 5)
        self.assertEqual(missing, ["MoboReels"])


if __name__ == "__main__":
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")), patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
        unittest.main()
