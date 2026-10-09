"""Synthetic integration of B3 authority, B6 provider review, B4 gate, B5 policy."""
from dataclasses import replace
from datetime import timedelta
import unittest

import test_batch_authority_v2 as batch_fixture
import test_provider_policy_evidence_v2 as provider_fixture
import research_eligibility_v2 as eligibility
import research_attempt_policy_v2 as attempt_policy
import provider_policy_evidence_v2 as provider_policy

NOW = batch_fixture.NOW


class ResearchPreflightIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.batch = batch_fixture.BatchAuthorityTests()
        self.batch.setUp()
        self.source_review = provider_fixture.ProviderPolicyEvidenceTests()
        self.provider_evidence, self.provider_ledger = self.source_review.fixture()
        self.reviewed_provider = provider_policy.verify_free_provider_policy(
            self.provider_evidence,
            approval_ledger=self.provider_ledger,
            trusted_provider_hosts={"provider-free": {"api.vendor.example"}},
            now=NOW,
        )

    def preflight(self, *, runs=None, origin=None, provider=None, now=NOW):
        row = self.batch.run.rows[0]
        context = dict(self.batch.context)
        context["now"] = now
        return eligibility.authorize_research_preflight(
            scope=self.batch.scope,
            origin_run_id=origin or self.batch.run.run_id,
            source_entity_id=row.source_entity_id,
            record_id=row.record_id,
            canonical_drama_id="content:1",
            runs=tuple(runs or (self.batch.run,)),
            task_state="PENDING",
            provider_approvals=(provider or self.reviewed_provider,),
            existing_complete=False,
            **context,
        )

    def test_full_offline_chain_rechecks_eligibility_after_retry_and_quota_resume(self):
        initial = self.preflight()
        self.assertTrue(initial.authorized)
        self.assertEqual(initial.authoritative_run_id, self.batch.run.run_id)
        self.assertEqual(initial.provider_id, "provider-free")
        checkpoint = attempt_policy.CallCheckpoint("SEARCH")

        # Simulated transient provider response; this code performs no call.
        checkpoint = attempt_policy.record_call_result(checkpoint, outcome="transient", now=NOW, attempt_active_seconds=4).checkpoint
        ready = attempt_policy.prepare_due_retry(checkpoint, now=checkpoint.retry_at).checkpoint
        self.assertTrue(self.preflight().authorized)  # Fresh B3 authority and B6-reviewed provider before retry.
        checkpoint = attempt_policy.record_call_result(ready, outcome="quota", now=checkpoint.retry_at, attempt_active_seconds=2).checkpoint
        self.assertEqual(checkpoint.status, "DEFERRED_FREE_QUOTA")

        # Restoring quota alone is insufficient; first re-run current eligibility.
        resume_time = NOW + timedelta(minutes=10)
        before_resume = self.preflight(now=resume_time)
        self.assertTrue(before_resume.authorized)
        resumed = attempt_policy.resume_quota_checkpoint(
            checkpoint, now=resume_time, quota_restored=True,
            eligibility_revalidated=before_resume.authorized,
        )
        self.assertEqual((resumed.checkpoint.status, resumed.checkpoint.attempt_count), ("READY", 2))
        final_preflight = self.preflight(now=resume_time)
        self.assertTrue(final_preflight.authorized)
        self.assertFalse(final_preflight.external_call_performed)
        self.assertFalse(final_preflight.spend_incurred)

    def test_superseded_run_cannot_reach_provider_even_with_valid_free_approval(self):
        later = self.batch.make_run(name="run:later", at=NOW - timedelta(minutes=1), offset=20)
        decision = self.preflight(runs=(self.batch.run, later))
        self.assertFalse(decision.authorized)
        self.assertEqual(decision.reason, "ORIGIN_SUPERSEDED")
        self.assertFalse(decision.external_call_performed)

    def test_revoked_provider_review_blocks_authority_valid_candidate(self):
        approval = next(iter(self.provider_ledger.values()))
        self.provider_ledger[approval.evidence_ref] = replace(approval, revoked=True)
        with self.assertRaises(provider_policy.IdentityReviewRequired):
            provider_policy.verify_free_provider_policy(
                self.provider_evidence, approval_ledger=self.provider_ledger,
                trusted_provider_hosts={"provider-free": {"api.vendor.example"}}, now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
