"""Synthetic review ledger; no claim of real source truth or E2E readiness."""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
for name in ("drama_identity_v2", "identity_evidence_v2"):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
identity = sys.modules["identity_evidence_v2"]
Review = sys.modules["drama_identity_v2"].IdentityReviewRequired
NOW = datetime(2026, 10, 9, 8, tzinfo=timezone.utc)


def fixture(observation, canonical_id="content:1", ref="fixture:one", **overrides):
    artifact = b"synthetic reviewed source snapshot"
    data = dict(schemaVersion=1, platform=observation.platform, sourceType=observation.source_type, sourceEntityId=observation.source_entity_id, canonicalDramaId=canonical_id, evidenceRef=ref, artifactSha256=hashlib.sha256(artifact).hexdigest())
    data.update(overrides)
    raw = json.dumps(data, sort_keys=True).encode()
    evidence = identity.IdentityEvidence(raw, artifact)
    approval = identity.EvidenceApproval(ref, hashlib.sha256(raw).hexdigest(), "decision:" + ref, NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    return evidence, approval


class EvidenceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.observation = identity.ObservationIdentity("DramaBox", "OFFICIAL_WEB", "entity:10")
        self.evidence, self.approval = fixture(self.observation)
        self.sources = frozenset({("DramaBox", "OFFICIAL_WEB"), ("NetShort", "OFFICIAL_WEB"), ("DramaBox", "SHORT_DRAMA_APP")})

    def resolve(self, evidence=None, ledger=None, observation=None, now=NOW):
        return identity.resolve_content_identity(observation or self.observation, [self.evidence] if evidence is None else evidence, approval_ledger={self.approval.evidence_ref: self.approval} if ledger is None else ledger, verified_sources=self.sources, now=now)

    def test_valid_reviewed_binding(self):
        result = self.resolve()
        self.assertEqual(result.canonical_drama_id, "content:1")
        self.assertEqual(result.observation, self.observation)
        self.assertEqual(result.evidence_refs, ("fixture:one",))
        self.assertEqual(result.decision_ids, ("decision:fixture:one",))

    def test_candidate_platform_policy_matches_core_contract(self):
        contract = json.loads((ROOT / "core_contract_v2.json").read_text(encoding="utf-8"))
        self.assertEqual(identity.ACTIVE_PLATFORMS, frozenset(contract["sourceSemantics"]["activeOfficialWebPlatforms"]))
        self.assertTrue(identity.ACTIVE_PLATFORMS.isdisjoint(contract["sourceSemantics"]["pausedPlatforms"]))
        self.assertEqual(identity.SUPPORTED_SOURCE_TYPES, {"OFFICIAL_WEB", "SHORT_DRAMA_APP"})

    def test_no_approval_cannot_self_verify(self):
        with self.assertRaises(Review):
            self.resolve(ledger={})
        with self.assertRaises(Review):
            self.resolve(ledger={"fixture:one": {"verified": True}})

    def test_missing_evidence_cannot_mint_new_identity(self):
        with self.assertRaises(Review):
            self.resolve(evidence=[])

    def test_tampered_claim_and_artifact_rejected(self):
        changed = identity.IdentityEvidence(self.evidence.claim_bytes.replace(b"content:1", b"content:2"), self.evidence.artifact_bytes)
        with self.assertRaises(Review):
            self.resolve([changed])
        changed = identity.IdentityEvidence(self.evidence.claim_bytes, b"different snapshot")
        with self.assertRaises(Review):
            self.resolve([changed])

    def test_unverified_source_rejected(self):
        self.sources = frozenset()
        with self.assertRaises(Review):
            self.resolve()

    def test_source_entity_platform_and_type_cannot_be_reused(self):
        for observation in (identity.ObservationIdentity("DramaBox", "OFFICIAL_WEB", "entity:11"), identity.ObservationIdentity("NetShort", "OFFICIAL_WEB", "entity:10"), identity.ObservationIdentity("DramaBox", "SHORT_DRAMA_APP", "entity:10")):
            with self.subTest(observation=observation):
                with self.assertRaises(Review):
                    self.resolve(observation=observation)

    def test_cross_platform_same_content_requires_separate_binding(self):
        observation = identity.ObservationIdentity("NetShort", "OFFICIAL_WEB", "entity:20")
        evidence, approval = fixture(observation, ref="fixture:other-platform")
        result = self.resolve([evidence], {approval.evidence_ref: approval}, observation)
        self.assertEqual(result.canonical_drama_id, self.resolve().canonical_drama_id)
        self.assertNotEqual(result.observation, self.resolve().observation)

    def test_same_title_has_no_role_in_distinct_content_bindings(self):
        other = identity.ObservationIdentity("DramaBox", "OFFICIAL_WEB", "entity:11")
        evidence, approval = fixture(other, "content:2", "fixture:other-content")
        result = self.resolve([evidence], {approval.evidence_ref: approval}, other)
        self.assertNotEqual(result.canonical_drama_id, self.resolve().canonical_drama_id)
        bad, approval = fixture(self.observation, title="Same Title")
        with self.assertRaises(Review):
            self.resolve([bad], {approval.evidence_ref: approval})

    def test_conflicting_approved_bindings_require_review(self):
        evidence, approval = fixture(self.observation, "content:2", "fixture:conflict")
        with self.assertRaises(Review):
            self.resolve([self.evidence, evidence], {self.approval.evidence_ref: self.approval, approval.evidence_ref: approval})

    def test_order_and_duplicate_replay_are_deterministic(self):
        evidence, approval = fixture(self.observation, ref="fixture:two")
        ledger = {self.approval.evidence_ref: self.approval, approval.evidence_ref: approval}
        first = self.resolve([self.evidence, evidence], ledger)
        self.assertEqual(first, self.resolve([evidence, self.evidence, evidence], ledger))

    def test_revoked_expired_future_and_naive_time_rejected(self):
        for now in (NOW + timedelta(hours=1), NOW - timedelta(hours=2), NOW.replace(tzinfo=None)):
            with self.subTest(now=now):
                with self.assertRaises(Review):
                    self.resolve(now=now)
        a = self.approval
        revoked = identity.EvidenceApproval(a.evidence_ref, a.claim_sha256, a.decision_id, a.approved_at, a.valid_until, True)
        with self.assertRaises(Review):
            self.resolve(ledger={a.evidence_ref: revoked})

    def test_malformed_schema_duplicate_keys_and_types_rejected(self):
        for overrides in ({"schemaVersion": True}, {"schemaVersion": 2}, {"canonicalDramaId": ""}, {"sourceEntityId": 10}, {"canonicalDramaId": " padded "}):
            with self.subTest(overrides=overrides):
                evidence, approval = fixture(self.observation, **overrides)
                with self.assertRaises(Review):
                    self.resolve([evidence], {approval.evidence_ref: approval})
        for raw in (b"[]", b"not-json", b'{"schemaVersion":1,"schemaVersion":1}', b"\xff"):
            with self.assertRaises(Review):
                self.resolve([identity.IdentityEvidence(raw, b"snapshot")])

    def test_paused_platform_and_unknown_source_rejected(self):
        for platform, source in (("ShortMax", "OFFICIAL_WEB"), ("DramaWave", "OFFICIAL_WEB"), ("DramaBox", "SEARCH_TREND")):
            with self.assertRaises(Review):
                identity.ObservationIdentity(platform, source, "entity:10")


if __name__ == "__main__":
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")), patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
        unittest.main()
