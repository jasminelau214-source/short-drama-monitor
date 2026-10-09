"""Synthetic provider policy claim/artifact/approval binding tests."""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent
for name in ("drama_identity_v2", "identity_evidence_v2", "batch_authority_v2", "research_eligibility_v2"):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
spec = importlib.util.spec_from_file_location("provider_policy_evidence_v2", ROOT / "provider_policy_evidence_v2.py")
verification = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verification
spec.loader.exec_module(verification)

NOW = datetime(2026, 10, 9, 8, 25, tzinfo=timezone.utc)
HOSTS = {"provider-free": {"api.vendor.example"}}


class ProviderPolicyEvidenceTests(unittest.TestCase):
    def fixture(self, **changes):
        artifact = b"captured official pricing and free-tier terms"
        claim = dict(schemaVersion=1, providerId="provider-free", productId="product-1", sourceUrl="https://api.vendor.example/pricing",
                     evidenceRef="evidence:provider:1", artifactSha256=hashlib.sha256(artifact).hexdigest(), freeTierVerified=True,
                     automaticPaidFallback=False, checkedAt="2026-10-09T08:00:00+00:00", validUntil="2026-10-10T00:00:00+00:00")
        claim.update(changes)
        raw = json.dumps(claim, sort_keys=True, separators=(",", ":")).encode()
        approval = verification.ProviderPolicyApproval(claim["evidenceRef"], hashlib.sha256(raw).hexdigest(), "approval:provider:1",
                                                       NOW - timedelta(minutes=10), NOW + timedelta(days=1))
        return verification.ProviderPolicyEvidence(raw, artifact), {approval.evidence_ref: approval}

    def verify(self, evidence, ledger, *, now=NOW, hosts=HOSTS):
        return verification.verify_free_provider_policy(evidence, approval_ledger=ledger, trusted_provider_hosts=hosts, now=now)

    def test_approved_free_policy_returns_provenance_bound_preflight_approval(self):
        evidence, ledger = self.fixture()
        result = self.verify(evidence, ledger)
        self.assertEqual(result.provider_id, "provider-free")
        self.assertTrue(result.free_tier_verified)
        self.assertFalse(result.paid_fallback)
        self.assertEqual(result.review_ref, "approval:provider:1")
        self.assertEqual(result.source_url, "https://api.vendor.example/pricing")
        self.assertEqual(result.artifact_sha256, hashlib.sha256(evidence.artifact_bytes).hexdigest())

    def test_paid_fallback_false_free_claim_host_and_artifact_are_required(self):
        for changes in ({"freeTierVerified": False}, {"automaticPaidFallback": True}, {"sourceUrl": "https://evil.example/pricing"}):
            with self.subTest(changes=changes):
                evidence, ledger = self.fixture(**changes)
                # Re-bind the approval to exercise claim semantics instead of digest rejection.
                approval = next(iter(ledger.values()))
                ledger[approval.evidence_ref] = verification.ProviderPolicyApproval(approval.evidence_ref, hashlib.sha256(evidence.claim_bytes).hexdigest(), approval.approval_id, approval.approved_at, approval.valid_until)
                with self.assertRaises(verification.IdentityReviewRequired):
                    self.verify(evidence, ledger)
        evidence, ledger = self.fixture()
        corrupted = verification.ProviderPolicyEvidence(evidence.claim_bytes, evidence.artifact_bytes + b"tampered")
        with self.assertRaises(verification.IdentityReviewRequired):
            self.verify(corrupted, ledger)

    def test_exact_claim_bytes_require_unexpired_unrevoked_independent_approval(self):
        evidence, ledger = self.fixture()
        with self.assertRaises(verification.IdentityReviewRequired):
            self.verify(evidence, {})
        approval = next(iter(ledger.values()))
        ledger[approval.evidence_ref] = verification.ProviderPolicyApproval(approval.evidence_ref, approval.claim_sha256, approval.approval_id, approval.approved_at, approval.valid_until, True)
        with self.assertRaises(verification.IdentityReviewRequired):
            self.verify(evidence, ledger)
        evidence, ledger = self.fixture()
        with self.assertRaises(verification.IdentityReviewRequired):
            self.verify(evidence, ledger, now=NOW + timedelta(days=2))

    def test_duplicate_keys_unknown_schema_and_untrusted_host_rejected(self):
        evidence, ledger = self.fixture()
        duplicate = b'{"schemaVersion":1,"schemaVersion":1}'
        with self.assertRaises(verification.IdentityReviewRequired):
            self.verify(verification.ProviderPolicyEvidence(duplicate, b"artifact"), ledger)
        evidence, ledger = self.fixture(unapprovedExtra="value")
        with self.assertRaises(verification.IdentityReviewRequired):
            self.verify(evidence, ledger)
        evidence, ledger = self.fixture()
        with self.assertRaises(verification.IdentityReviewRequired):
            self.verify(evidence, ledger, hosts={"provider-free": {"other.vendor.example"}})


if __name__ == "__main__":
    unittest.main()
