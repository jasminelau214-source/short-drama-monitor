"""Synthetic-only tests for the external-research eligibility preflight."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
for name in ("drama_identity_v2", "identity_evidence_v2", "batch_authority_v2"):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
spec = importlib.util.spec_from_file_location("research_eligibility_v2", ROOT / "research_eligibility_v2.py")
gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gate
spec.loader.exec_module(gate)

NOW = datetime(2026, 10, 9, 8, 25, tzinfo=timezone.utc)


class ResearchEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.scope = object()
        self.approval = gate.FreeProviderApproval("provider-free", NOW - timedelta(minutes=1), NOW + timedelta(minutes=1), True, False)
        self.kwargs = dict(scope=self.scope, origin_run_id="run", source_entity_id="entity", record_id="record",
                           canonical_drama_id="content", runs=(), task_state="PENDING", provider_approvals=(self.approval,),
                           existing_complete=False, now=NOW)

    def assess(self, current=True):
        return gate.ObservationDecision(current, "CURRENT_OBSERVATION_ONLY" if current else "ORIGIN_SUPERSEDED", "run:new", "a" * 64)

    def authorize(self, expect_assessment=True, **changes):
        args = dict(self.kwargs)
        args.update(changes)
        with patch.object(gate, "assess_current_observation", return_value=self.assess()) as assessor:
            decision = gate.authorize_research_preflight(**args)
        (assessor.assert_called_once if expect_assessment else assessor.assert_not_called)()
        return decision

    def test_current_pending_and_verified_free_provider_is_preflight_only(self):
        decision = self.authorize()
        self.assertTrue(decision.authorized)
        self.assertEqual(decision.reason, "PREFLIGHT_AUTHORIZED_ONLY")
        self.assertEqual(decision.provider_id, "provider-free")
        self.assertEqual(decision.authoritative_run_id, "run:new")
        self.assertEqual(decision.authority_snapshot_sha256, "a" * 64)
        self.assertFalse(decision.external_call_performed)
        self.assertFalse(decision.spend_incurred)

    def test_existing_complete_and_nonpending_states_cannot_spend_again(self):
        for changes in ({"existing_complete": True}, {"task_state": "DEFERRED_FREE_QUOTA"}, {"task_state": "REVIEW_REQUIRED"}, {"task_state": "COMPLETE"}):
            with self.subTest(changes=changes):
                decision = self.authorize(expect_assessment=False, **changes)
                self.assertFalse(decision.authorized)

    def test_provider_must_be_current_free_and_have_no_paid_fallback(self):
        bad = (
            gate.FreeProviderApproval("paid", NOW, NOW + timedelta(minutes=1), True, True),
            gate.FreeProviderApproval("unknown", NOW, NOW + timedelta(minutes=1), False, False),
            gate.FreeProviderApproval("expired", NOW - timedelta(minutes=2), NOW - timedelta(seconds=1), True, False),
            gate.FreeProviderApproval("revoked", NOW, NOW + timedelta(minutes=1), True, False, True),
        )
        decision = self.authorize(provider_approvals=bad)
        self.assertFalse(decision.authorized)
        self.assertEqual(decision.reason, "NO_VERIFIED_FREE_PROVIDER")

    def test_missing_or_stale_authority_fails_closed(self):
        with patch.object(gate, "assess_current_observation", return_value=self.assess(False)):
            decision = gate.authorize_research_preflight(**self.kwargs)
        self.assertFalse(decision.authorized)
        self.assertEqual(decision.reason, "ORIGIN_SUPERSEDED")

    def test_unknown_complete_lookup_type_is_rejected(self):
        with self.assertRaises(gate.IdentityReviewRequired):
            self.authorize(existing_complete=None)


if __name__ == "__main__":
    unittest.main()
