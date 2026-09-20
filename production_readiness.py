from __future__ import annotations

GATE_ORDER = (
    "execution",
    "structure",
    "truth",
    "semantic",
    "fault",
    "identity",
    "stability",
    "path_optimization",
    "e2e",
)

VALID_GATE_STATES = {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}


def empty_gate_state() -> dict[str, str]:
    return {gate: "NOT_RUN" for gate in GATE_ORDER}


def evaluate_readiness(gates: dict[str, str]) -> dict:
    normalized = empty_gate_state()
    for gate in GATE_ORDER:
        value = str(gates.get(gate, "NOT_RUN") or "NOT_RUN").upper()
        if value not in VALID_GATE_STATES:
            raise ValueError(f"invalid readiness state for {gate}: {value}")
        normalized[gate] = value

    blockers = [gate for gate, state in normalized.items() if state != "PASS"]
    return {
        "status": "PRODUCTION_READY" if not blockers else "PROMOTION_BLOCKED",
        "gates": normalized,
        "blockingGates": blockers,
    }
