"""Synthetic-only tests for bounded per-call retry and quota transitions."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent
if "drama_identity_v2" not in sys.modules:
    spec = importlib.util.spec_from_file_location("drama_identity_v2", ROOT / "drama_identity_v2.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
spec = importlib.util.spec_from_file_location("research_attempt_policy_v2", ROOT / "research_attempt_policy_v2.py")
policy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = policy
spec.loader.exec_module(policy)

NOW = datetime(2026, 10, 9, 8, 25, tzinfo=timezone.utc)


class ResearchAttemptPolicyTests(unittest.TestCase):
    def test_only_two_technical_retries_after_initial_attempt(self):
        cp = policy.CallCheckpoint("SEARCH")
        first = policy.record_call_result(cp, outcome="transient", now=NOW, attempt_active_seconds=3).checkpoint
        self.assertEqual((first.status, first.attempt_count, first.retry_at), ("RETRY_WAIT", 1, NOW + timedelta(seconds=2)))
        self.assertFalse(policy.retry_is_due(first, now=NOW))
        ready = policy.prepare_due_retry(first, now=first.retry_at).checkpoint
        second = policy.record_call_result(ready, outcome="transient", now=NOW + timedelta(seconds=4), attempt_active_seconds=5).checkpoint
        self.assertEqual((second.status, second.attempt_count, second.retry_at), ("RETRY_WAIT", 2, NOW + timedelta(seconds=8)))
        ready = policy.prepare_due_retry(second, now=second.retry_at).checkpoint
        failed = policy.record_call_result(ready, outcome="transient", now=second.retry_at, attempt_active_seconds=7)
        self.assertEqual((failed.checkpoint.status, failed.checkpoint.attempt_count, failed.reason), ("FAILED", 3, "TECHNICAL_ATTEMPTS_EXHAUSTED"))

    def test_business_outcomes_never_mechanically_retry(self):
        for outcome, status in (("insufficient_evidence", "NEEDS_GPT"), ("identity_conflict", "REVIEW_REQUIRED"), ("unexpected", "REVIEW_REQUIRED")):
            with self.subTest(outcome=outcome):
                result = policy.record_call_result(policy.CallCheckpoint("ANALYZE"), outcome=outcome, now=NOW)
                self.assertEqual(result.checkpoint.status, status)
                self.assertEqual(result.checkpoint.attempt_count, 1)

    def test_quota_defers_without_consuming_active_timeout_and_requires_revalidation(self):
        deferred = policy.record_call_result(policy.CallCheckpoint("SEARCH", active_seconds=22), outcome="quota", now=NOW, attempt_active_seconds=4).checkpoint
        self.assertEqual((deferred.status, deferred.attempt_count, deferred.active_seconds), ("DEFERRED_FREE_QUOTA", 1, 26))
        for restored, eligible in ((False, True), (True, False)):
            result = policy.resume_quota_checkpoint(deferred, now=NOW + timedelta(days=4), quota_restored=restored, eligibility_revalidated=eligible)
            self.assertEqual(result.checkpoint, deferred)
        resumed = policy.resume_quota_checkpoint(deferred, now=NOW + timedelta(days=4), quota_restored=True, eligibility_revalidated=True)
        self.assertEqual((resumed.checkpoint.status, resumed.checkpoint.attempt_count, resumed.checkpoint.active_seconds), ("READY", 1, 26))

    def test_timeout_is_cumulative_and_cannot_reset_at_retry(self):
        result = policy.record_call_result(policy.CallCheckpoint("VALIDATE", attempt_count=1, active_seconds=298), outcome="transient", now=NOW, attempt_active_seconds=3)
        self.assertEqual((result.checkpoint.status, result.checkpoint.active_seconds), ("FAILED", 300))
        self.assertEqual(result.reason, "ACTIVE_TIMEOUT_EXHAUSTED")

    def test_invalid_state_and_attempt_coercion_fail_closed(self):
        with self.assertRaises(policy.IdentityReviewRequired):
            policy.record_call_result(policy.CallCheckpoint("SEARCH", attempt_count=True), outcome="success", now=NOW)
        with self.assertRaises(policy.IdentityReviewRequired):
            policy.record_call_result(policy.CallCheckpoint("SEARCH", status="DEFERRED_FREE_QUOTA"), outcome="success", now=NOW)


if __name__ == "__main__":
    unittest.main()
