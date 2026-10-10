"""Synthetic CAS, concurrent reservation and restart replay tests."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import threading
import unittest

import test_research_preflight_integration_v2 as chain_fixture
import research_attempt_policy_v2 as policy
import research_spend_reservation_v2 as reservation

NOW = chain_fixture.NOW


class SpendReservationTests(unittest.TestCase):
    def setUp(self):
        self.chain = chain_fixture.ResearchPreflightIntegrationTests()
        self.chain.setUp()
        self.preflight = self.chain.preflight()
        self.provider = self.chain.reviewed_provider
        self.initial = reservation.ResearchTaskSnapshot(
            "task:synthetic:1", 0, "PENDING", self.preflight.authoritative_run_id,
            self.preflight.authority_snapshot_sha256, self.provider.review_ref,
        )

    def candidate(self, *, snapshot=None, task_state=None, attempt_number=1, checkpoint=None, decision=None, provider=None, now=NOW):
        current = snapshot or self.initial
        return reservation.SpendAttemptCandidate(
            current.task_id, current.revision, "SEARCH", attempt_number,
            decision or self.preflight, provider or self.provider, now, checkpoint,
        )

    def test_atomic_reservation_is_idempotent_and_survives_reloaded_snapshot(self):
        ledger = reservation.OfflineSpendReservationLedger([self.initial])
        candidate = self.candidate()
        first = ledger.reserve(candidate)
        self.assertTrue(first.authorized)
        self.assertEqual(first.reason, "ATTEMPT_RESERVED_ONLY")
        current = ledger.snapshot(self.initial.task_id)
        self.assertEqual((current.state, current.revision, len(current.reserved_attempt_keys)), ("RESEARCHING", 1, 1))

        duplicate = ledger.reserve(candidate)
        self.assertFalse(duplicate.authorized)
        self.assertEqual(duplicate.reason, "ATTEMPT_ALREADY_RESERVED")
        reloaded = reservation.OfflineSpendReservationLedger([current])
        replay = reloaded.reserve(candidate)
        self.assertEqual(replay.reason, "ATTEMPT_ALREADY_RESERVED")

    def test_parallel_duplicate_claims_produce_exactly_one_reservation(self):
        ledger = reservation.OfflineSpendReservationLedger([self.initial])
        candidate = self.candidate()
        barrier = threading.Barrier(12)

        def run():
            barrier.wait()
            return ledger.reserve(candidate)

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: run(), range(12)))
        self.assertEqual(sum(result.authorized for result in results), 1)
        self.assertEqual(sum(result.reason == "ATTEMPT_ALREADY_RESERVED" for result in results), 11)

    def test_state_revision_and_authoritative_snapshot_changes_block_stale_reservation(self):
        ledger = reservation.OfflineSpendReservationLedger([self.initial])
        self.assertTrue(ledger.supersede_authority(self.initial.task_id, run_id="run:new", snapshot_sha256="b" * 64))
        stale = ledger.reserve(self.candidate())
        self.assertEqual(stale.reason, "TASK_REVISION_CHANGED")
        current = ledger.snapshot(self.initial.task_id)
        rechecked_old_decision = ledger.reserve(self.candidate(snapshot=current))
        self.assertEqual(rechecked_old_decision.reason, "PREFLIGHT_AUTHORITY_CHANGED")

    def test_reservation_requires_current_provider_approval_and_preflight(self):
        ledger = reservation.OfflineSpendReservationLedger([self.initial])
        expired = replace(self.provider, valid_until=NOW - timedelta(seconds=1))
        self.assertEqual(ledger.reserve(self.candidate(provider=expired)).reason, "PROVIDER_APPROVAL_CHANGED")
        denied = replace(self.preflight, authorized=False)
        self.assertEqual(ledger.reserve(self.candidate(decision=denied)).reason, "PREFLIGHT_AUTHORITY_CHANGED")

    def test_ready_retry_reserves_only_the_next_bounded_attempt(self):
        checkpoint = policy.CallCheckpoint("SEARCH", 1, 20, "READY")
        decision = self.chain.preflight(task_state="RESEARCHING", retry_checkpoint=checkpoint)
        current = reservation.ResearchTaskSnapshot(
            self.initial.task_id, 4, "RESEARCHING", decision.authoritative_run_id,
            decision.authority_snapshot_sha256, self.provider.review_ref,
        )
        ledger = reservation.OfflineSpendReservationLedger([current])
        candidate = self.candidate(snapshot=current, attempt_number=2, checkpoint=checkpoint, decision=decision)
        result = ledger.reserve(candidate)
        self.assertTrue(result.authorized)
        self.assertEqual(result.current_revision, 5)
        self.assertEqual(len(ledger.snapshot(current.task_id).reserved_attempt_keys), 1)


if __name__ == "__main__":
    unittest.main()
