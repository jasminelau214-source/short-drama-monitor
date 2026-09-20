import unittest

from production_readiness import GATE_ORDER, empty_gate_state, evaluate_readiness


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


if __name__ == "__main__":
    unittest.main()
