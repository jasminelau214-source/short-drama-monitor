"""Disk reopen, multi-process uniqueness, crash rollback and recovery tests."""
from dataclasses import asdict, replace
from contextlib import closing
from datetime import timedelta
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

# Load the synthetic chain before importing modules with dataclass type gates.
import test_research_preflight_integration_v2 as chain_fixture
import research_attempt_policy_v2 as policy
import research_reservation_store_v2 as durable
import research_spend_reservation_v2 as reservation

NOW = chain_fixture.NOW


def fixture():
    chain = chain_fixture.ResearchPreflightIntegrationTests()
    chain.setUp()
    decision = chain.preflight()
    initial = reservation.ResearchTaskSnapshot("task:durable:synthetic", 0, "PENDING",
                                             decision.authoritative_run_id, decision.authority_snapshot_sha256,
                                             chain.reviewed_provider.review_ref)
    candidate = reservation.SpendAttemptCandidate(initial.task_id, 0, "SEARCH", 1,
                                                 decision, chain.reviewed_provider, NOW)
    return chain, initial, candidate


def process_action(path, action):
    """Executed in a separate interpreter; no network or production access."""
    _, _, candidate = fixture()
    store = durable.IsolatedReservationStore(path, synthetic_offline_only=True)
    if action == "crash_inside_reservation":
        # Real process exit after task UPDATE, before attempt INSERT/COMMIT.
        durable._provider_binding = lambda _: os._exit(19)
    decision = store.reserve(candidate)
    if action == "crash_after_reservation":
        os._exit(20)
    if action == "crash_after_handoff":
        store.begin_simulated_handoff(decision.reservation_key, owner_token="stopped:fixture",
                                      expected_revision=decision.current_revision, preflight=candidate.preflight,
                                      provider=candidate.provider_approval, now=NOW)
        os._exit(21)
    print(json.dumps(asdict(decision)), flush=True)


class DurableReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jsm-b10-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.sqlite3"
        self.store = self.open_store()
        self.chain, self.initial, self.candidate = fixture()
        self.store.seed_synthetic_task(self.initial)

    def open_store(self):
        return durable.IsolatedReservationStore(self.path, synthetic_offline_only=True)

    def child(self, action):
        return [sys.executable, "-c", "import sys; from test_research_reservation_store_v2 import process_action; process_action(sys.argv[1],sys.argv[2])",
                str(self.path), action]

    def start(self, candidate=None, owner="worker:fixture"):
        candidate = candidate or self.candidate
        reserved = self.store.reserve(candidate)
        self.assertTrue(reserved.authorized, reserved.reason)
        handoff = self.store.begin_simulated_handoff(
            reserved.reservation_key, owner_token=owner, expected_revision=reserved.current_revision,
            preflight=candidate.preflight, provider=candidate.provider_approval, now=candidate.now)
        self.assertTrue(handoff.accepted, handoff.reason)
        return reserved.reservation_key

    def test_reopened_file_retains_reservation_and_exclusive_handoff(self):
        key = self.store.reserve(self.candidate).reservation_key
        reopened = self.open_store()
        self.assertEqual(reopened.reserve(self.candidate).reason, "ATTEMPT_ALREADY_RESERVED")
        self.assertEqual(reopened.recovery_plan(), ((key, "RECHECK_BEFORE_HANDOFF"),))
        context = dict(owner_token="worker:fixture", expected_revision=1, preflight=self.candidate.preflight,
                       provider=self.candidate.provider_approval, now=NOW)
        self.assertTrue(reopened.begin_simulated_handoff(key, **context).accepted)
        self.assertEqual(self.open_store().begin_simulated_handoff(key, **context).reason, "HANDOFF_NOT_RESERVABLE")

    def test_independent_processes_reserve_exactly_once(self):
        processes = [subprocess.Popen(self.child("reserve"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, cwd=Path(__file__).parent) for _ in range(6)]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(json.loads(stdout))
        self.assertEqual(sum(result["authorized"] for result in results), 1)
        self.assertEqual(len(self.open_store().snapshot(self.initial.task_id).reserved_attempt_keys), 1)

    def test_process_exit_mid_transaction_rolls_back_task_and_attempt_together(self):
        result = subprocess.run(self.child("crash_inside_reservation"), cwd=Path(__file__).parent, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 19, result.stderr)
        reopened = self.open_store()
        self.assertEqual(reopened.snapshot(self.initial.task_id), self.initial)
        self.assertEqual(reopened.recovery_plan(), ())
        self.assertTrue(reopened.reserve(self.candidate).authorized)

    def test_process_exit_after_commit_keeps_key_and_requires_recheck(self):
        result = subprocess.run(self.child("crash_after_reservation"), cwd=Path(__file__).parent, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 20, result.stderr)
        reopened = self.open_store()
        self.assertEqual(reopened.reserve(self.candidate).reason, "ATTEMPT_ALREADY_RESERVED")
        self.assertEqual(reopened.recovery_plan()[0][1], "RECHECK_BEFORE_HANDOFF")

    def test_exit_after_handoff_requires_reconciliation_and_never_replays(self):
        result = subprocess.run(self.child("crash_after_handoff"), cwd=Path(__file__).parent, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 21, result.stderr)
        reopened = self.open_store()
        key, disposition = reopened.recovery_plan()[0]
        self.assertEqual(disposition, "RECONCILE_POSSIBLE_EXTERNAL_CALL")
        with self.assertRaises(reservation.IdentityReviewRequired):
            reopened.quarantine_stopped_owner("stopped:fixture", worker_stop_confirmed=False)
        self.assertEqual(reopened.quarantine_stopped_owner("stopped:fixture", worker_stop_confirmed=True), 1)
        self.assertEqual(reopened.quarantine_stopped_owner("stopped:fixture", worker_stop_confirmed=True), 0)
        self.assertEqual(reopened.snapshot(self.initial.task_id).state, "REVIEW_REQUIRED")
        self.assertFalse(reopened.record_simulated_outcome(key, owner_token="stopped:fixture", outcome="success", now=NOW).accepted)
        self.assertEqual(reopened.reserve(self.candidate).reason, "ATTEMPT_ALREADY_RESERVED")

    def test_handoff_rechecks_time_provider_binding_and_new_authority(self):
        key = self.store.reserve(self.candidate).reservation_key
        context = dict(owner_token="worker:fixture", expected_revision=1,
                       preflight=self.candidate.preflight, provider=self.candidate.provider_approval, now=NOW)
        expired = dict(context, now=self.candidate.provider_approval.valid_until)
        self.assertEqual(self.store.begin_simulated_handoff(key, **expired).reason, "PROVIDER_APPROVAL_CHANGED")
        changed_provider = replace(self.candidate.provider_approval, approval_id="approval:different")
        self.assertEqual(self.store.begin_simulated_handoff(key, **dict(context, provider=changed_provider)).reason, "HANDOFF_PROVIDER_BINDING_CHANGED")
        self.store.supersede_authority(self.initial.task_id, run_id="run:new", snapshot_sha256="b" * 64)
        self.assertEqual(self.store.begin_simulated_handoff(key, **context).reason, "HANDOFF_TASK_CHANGED")

    def test_new_authority_after_handoff_blocks_outcome_recording(self):
        key = self.start()
        self.store.supersede_authority(self.initial.task_id, run_id="run:new", snapshot_sha256="b" * 64)
        outcome = self.store.record_simulated_outcome(key, owner_token="worker:fixture", outcome="success", now=NOW)
        self.assertEqual(outcome.reason, "OUTCOME_TASK_OR_ATTEMPT_CHANGED")

    def test_retry_chain_uses_persisted_checkpoint_and_stops_after_three_attempts(self):
        candidate = self.candidate
        for attempt in range(1, 4):
            key = self.start(candidate)
            now = candidate.now
            result = self.store.record_simulated_outcome(key, owner_token="worker:fixture", outcome="transient", now=now, attempt_active_seconds=4)
            self.assertTrue(result.accepted)
            self.assertEqual(result.checkpoint.attempt_count, attempt)
            if attempt == 3:
                self.assertEqual(result.checkpoint.status, "FAILED")
                self.assertEqual(self.open_store().snapshot(self.initial.task_id).state, "FAILED")
                break
            self.assertEqual(self.store.prepare_retry(key, now=now).reason, "BACKOFF_NOT_ELAPSED")
            due = result.checkpoint.retry_at
            ready = self.open_store().prepare_retry(key, now=due)
            self.assertTrue(ready.accepted)
            current = self.store.snapshot(self.initial.task_id)
            decision = self.chain.preflight(task_state="RESEARCHING", retry_checkpoint=ready.checkpoint, now=due)
            candidate = replace(candidate, expected_revision=current.revision, attempt_number=attempt + 1,
                                preflight=decision, now=due, retry_checkpoint=ready.checkpoint)
            forged = replace(candidate, retry_checkpoint=replace(ready.checkpoint, checkpoint_id="checkpoint:unrecorded"))
            self.assertEqual(self.store.reserve(forged).reason, "DURABLE_RETRY_CHECKPOINT_MISMATCH")

    def test_unrecorded_retry_and_overlapping_attempt_cannot_be_reserved(self):
        first = self.store.reserve(self.candidate)
        checkpoint = policy.CallCheckpoint("SEARCH", 1, 4, "READY")
        preflight = self.chain.preflight(task_state="RESEARCHING", retry_checkpoint=checkpoint)
        candidate = replace(self.candidate, expected_revision=first.current_revision,
                            attempt_number=2, preflight=preflight, retry_checkpoint=checkpoint)
        self.assertEqual(self.store.reserve(candidate).reason, "ANOTHER_ATTEMPT_IN_FLIGHT")
        self.store.begin_simulated_handoff(first.reservation_key, owner_token="worker:fixture", expected_revision=1,
                                          preflight=self.candidate.preflight, provider=self.candidate.provider_approval, now=NOW)
        self.store.record_simulated_outcome(first.reservation_key, owner_token="worker:fixture", outcome="success", now=NOW)
        candidate = replace(candidate, expected_revision=self.store.snapshot(self.initial.task_id).revision)
        self.assertEqual(self.store.reserve(candidate).reason, "DURABLE_RETRY_CHECKPOINT_MISMATCH")

    def test_outcome_owner_and_replay_are_bound_without_marking_research_complete(self):
        key = self.start()
        self.assertEqual(self.store.record_simulated_outcome(key, owner_token="other:worker", outcome="success", now=NOW).reason, "OUTCOME_OWNER_MISMATCH")
        context = dict(owner_token="worker:fixture", outcome="success", now=NOW, attempt_active_seconds=3)
        self.assertTrue(self.store.record_simulated_outcome(key, **context).accepted)
        self.assertEqual(self.open_store().record_simulated_outcome(key, **context).reason, "OUTCOME_ALREADY_RECORDED")
        self.assertEqual(self.store.record_simulated_outcome(key, **dict(context, outcome="transient")).reason, "OUTCOME_CONFLICT")
        self.assertEqual(self.store.snapshot(self.initial.task_id).state, "RESEARCHING")

    def test_quota_is_persisted_and_cannot_be_retried_by_reopening(self):
        key = self.start()
        self.store.record_simulated_outcome(key, owner_token="worker:fixture", outcome="quota", now=NOW)
        self.assertEqual(self.open_store().snapshot(self.initial.task_id).state, "DEFERRED_FREE_QUOTA")
        self.assertEqual(self.store.prepare_retry(key, now=NOW + timedelta(days=1)).reason, "RETRY_NOT_WAITING")

    def test_explicit_temporary_fixture_boundary_and_no_network(self):
        with self.assertRaises(reservation.IdentityReviewRequired):
            durable.IsolatedReservationStore(self.path, synthetic_offline_only=False)
        with self.assertRaises(reservation.IdentityReviewRequired):
            durable.IsolatedReservationStore(Path(__file__).parent / "forbidden.sqlite3", synthetic_offline_only=True)
        foreign = Path(self.temp.name) / "foreign.sqlite3"
        with closing(sqlite3.connect(foreign)) as db:
            db.execute("CREATE TABLE existing_user_data (value TEXT)")
        with self.assertRaises(reservation.IdentityReviewRequired):
            durable.IsolatedReservationStore(foreign, synthetic_offline_only=True)
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            self.assertTrue(self.store.reserve(self.candidate).authorized)


if __name__ == "__main__":
    unittest.main()
