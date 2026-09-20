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

TRUTH_EVIDENCE_LEVELS = {
    "SELF_CHECK": 0,
    "SAME_PARSER_REPLAY": 1,
    "INDEPENDENT_PARSER_LIVE": 2,
    "INDEPENDENT_PARSER_FROZEN": 3,
    "INDEPENDENT_OFFICIAL_CORROBORATION": 4,
}

TRUTH_MINIMUM_LEVEL = 2


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


def qualify_truth_evidence(
    mode: str,
    *,
    exact_match: bool,
    official_host: bool,
    semantic_anchor: bool,
    post_batch_delay_seconds: float | None = None,
) -> dict:
    mode = str(mode or "SELF_CHECK").upper()
    if mode not in TRUTH_EVIDENCE_LEVELS:
        raise ValueError(f"unknown truth evidence mode: {mode}")

    level = TRUTH_EVIDENCE_LEVELS[mode]
    limitations: list[str] = []

    if mode == "INDEPENDENT_PARSER_LIVE":
        limitations.append("LIVE_NEAR_TIME_NOT_SAME_SNAPSHOT")
        if post_batch_delay_seconds is None:
            limitations.append("SOURCE_TIME_DRIFT_NOT_FULLY_MEASURED")
        elif post_batch_delay_seconds > 300:
            limitations.append("POST_BATCH_DELAY_OVER_300S")

    if level < TRUTH_MINIMUM_LEVEL:
        limitations.append("INSUFFICIENT_PARSER_INDEPENDENCE")

    sufficient = (
        level >= TRUTH_MINIMUM_LEVEL
        and exact_match
        and official_host
        and semantic_anchor
        and not (
            mode == "INDEPENDENT_PARSER_LIVE"
            and post_batch_delay_seconds is not None
            and post_batch_delay_seconds > 300
        )
    )
    return {
        "mode": mode,
        "level": level,
        "minimumLevel": TRUTH_MINIMUM_LEVEL,
        "sufficient": sufficient,
        "limitations": limitations,
    }


def phase_eligibility(gates: dict[str, str], *, integration_ready: bool = False) -> dict:
    normalized = evaluate_readiness(gates)["gates"]
    a_ready = all(normalized[g] == "PASS" for g in ("execution", "structure", "truth", "semantic"))
    b_ready = a_ready and all(normalized[g] == "PASS" for g in ("fault", "identity"))
    c_ready = b_ready
    pre_d = all(normalized[g] == "PASS" for g in GATE_ORDER if g != "e2e")
    return {
        "B_FAULT_INJECTION": a_ready,
        "C_PATH_OPTIMIZATION": c_ready,
        "D_E2E_SHADOW": pre_d and bool(integration_ready),
    }


def evaluate_platform_readiness(
    platform: str,
    gates: dict[str, str],
    *,
    truth_evidence: dict | None = None,
    integration_ready: bool = False,
) -> dict:
    normalized = dict(gates)
    evidence = truth_evidence or {}
    if str(normalized.get("truth", "NOT_RUN")).upper() == "PASS" and not evidence.get("sufficient", False):
        normalized["truth"] = "BLOCKED"

    result = evaluate_readiness(normalized)
    result.update(
        {
            "platform": platform,
            "truthEvidence": evidence,
            "phaseEligibility": phase_eligibility(
                result["gates"],
                integration_ready=integration_ready,
            ),
        }
    )
    return result


def evaluate_system_readiness(
    platform_results: dict[str, dict],
    *,
    required_platforms: list[str] | tuple[str, ...],
    integration_ready: bool = False,
) -> dict:
    required = [str(x) for x in required_platforms]
    blocked = []
    for platform in required:
        result = platform_results.get(platform) or {}
        if result.get("status") != "PRODUCTION_READY":
            blocked.append(platform)

    return {
        "status": (
            "PRODUCTION_READY"
            if required and not blocked and integration_ready
            else "PROMOTION_BLOCKED"
        ),
        "requiredPlatforms": required,
        "blockingPlatforms": blocked,
        "integrationReady": bool(integration_ready),
    }
