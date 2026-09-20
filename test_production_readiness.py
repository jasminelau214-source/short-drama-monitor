import unittest

from production_readiness import (
    GATE_ORDER,
    empty_gate_state,
    evaluate_platform_readiness,
    evaluate_readiness,
    evaluate_system_readiness,
    phase_eligibility,
    qualify_truth_evidence,
)


class ProductionReadinessTests(unittest.TestCase):
    def test_legacy_local_success_does_not_imply_production_ready(self):
        gates = empty_gate_state()
        gates["execution"] = "PASS"
        gates["structure"] = "PASS"
        result = evaluate_readiness(gates)
        self.assertEqual(result["status"], "PROMOTION_BLOCKED")
        self.assertIn("truth", result["blockingGates"])
        self.assertIn("e2e", result["blockingGates"])

    def test_all_nine_gates_required(self):
        result = evaluate_readiness({gate: "PASS" for gate in GATE_ORDER})
        self.assertEqual(result["status"], "PRODUCTION_READY")
        self.assertEqual(result["blockingGates"], [])

    def test_fail_or_blocked_always_blocks_promotion(self):
        gates = {gate: "PASS" for gate in GATE_ORDER}
        gates["fault"] = "FAIL"
        self.assertEqual(evaluate_readiness(gates)["status"], "PROMOTION_BLOCKED")
        gates["fault"] = "BLOCKED"
        self.assertEqual(evaluate_readiness(gates)["status"], "PROMOTION_BLOCKED")

    def test_unknown_state_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_readiness({"truth": "GREEN"})

    def test_self_check_cannot_satisfy_truth(self):
        evidence = qualify_truth_evidence(
            "SELF_CHECK",
            exact_match=True,
            official_host=True,
            semantic_anchor=True,
        )
        self.assertFalse(evidence["sufficient"])
        self.assertEqual(evidence["level"], 0)

    def test_independent_live_parser_is_l2_with_limitation(self):
        evidence = qualify_truth_evidence(
            "INDEPENDENT_PARSER_LIVE",
            exact_match=True,
            official_host=True,
            semantic_anchor=True,
            post_batch_delay_seconds=30,
        )
        self.assertTrue(evidence["sufficient"])
        self.assertEqual(evidence["level"], 2)
        self.assertIn("LIVE_NEAR_TIME_NOT_SAME_SNAPSHOT", evidence["limitations"])

    def test_live_evidence_over_five_minutes_blocks_truth(self):
        evidence = qualify_truth_evidence(
            "INDEPENDENT_PARSER_LIVE",
            exact_match=True,
            official_host=True,
            semantic_anchor=True,
            post_batch_delay_seconds=301,
        )
        self.assertFalse(evidence["sufficient"])

    def test_frozen_same_snapshot_is_stronger_than_live(self):
        evidence = qualify_truth_evidence(
            "INDEPENDENT_PARSER_FROZEN",
            exact_match=True,
            official_host=True,
            semantic_anchor=True,
        )
        self.assertTrue(evidence["sufficient"])
        self.assertEqual(evidence["level"], 3)

    def test_platform_truth_pass_is_downgraded_when_evidence_is_weak(self):
        gates = empty_gate_state()
        for gate in ("execution", "structure", "truth", "semantic"):
            gates[gate] = "PASS"
        result = evaluate_platform_readiness(
            "Example",
            gates,
            truth_evidence=qualify_truth_evidence(
                "SAME_PARSER_REPLAY",
                exact_match=True,
                official_host=True,
                semantic_anchor=True,
            ),
        )
        self.assertEqual(result["gates"]["truth"], "BLOCKED")
        self.assertFalse(result["phaseEligibility"]["B_FAULT_INJECTION"])

    def test_platforms_can_progress_to_b_independently(self):
        gates = empty_gate_state()
        for gate in ("execution", "structure", "truth", "semantic"):
            gates[gate] = "PASS"
        evidence = qualify_truth_evidence(
            "INDEPENDENT_PARSER_LIVE",
            exact_match=True,
            official_host=True,
            semantic_anchor=True,
            post_batch_delay_seconds=10,
        )
        result = evaluate_platform_readiness("ReelShort", gates, truth_evidence=evidence)
        self.assertTrue(result["phaseEligibility"]["B_FAULT_INJECTION"])
        self.assertFalse(result["phaseEligibility"]["C_PATH_OPTIMIZATION"])

    def test_d_requires_integration_and_all_pre_d_gates(self):
        gates = {gate: "PASS" for gate in GATE_ORDER}
        gates["e2e"] = "NOT_RUN"
        self.assertFalse(phase_eligibility(gates, integration_ready=False)["D_E2E_SHADOW"])
        self.assertTrue(phase_eligibility(gates, integration_ready=True)["D_E2E_SHADOW"])

    def test_system_readiness_reports_blocking_platforms(self):
        platform_results = {
            "A": {"status": "PRODUCTION_READY"},
            "B": {"status": "PROMOTION_BLOCKED"},
        }
        result = evaluate_system_readiness(
            platform_results,
            required_platforms=["A", "B"],
            integration_ready=True,
        )
        self.assertEqual(result["status"], "PROMOTION_BLOCKED")
        self.assertEqual(result["blockingPlatforms"], ["B"])


if __name__ == "__main__":
    unittest.main()
