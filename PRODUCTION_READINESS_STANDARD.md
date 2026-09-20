# JSM Production Readiness Standard

This standard is an additive promotion gate. It does not redefine historical collector or research statuses.

## Compatibility rule

Existing local statuses keep their original meanings:

- `PASS_CANDIDATE` / `PASS_VERIFIED`: collector path produced a structurally valid target result under its existing audit.
- GitHub Actions `success`: the audit/test program executed successfully.
- Research `COMPLETE`: one research task met its current research completion rules.

None of those statuses alone means the system is production-ready.

## Two separate result planes

Every readiness workflow must report two different outcomes:

1. **Execution result** — whether the audit code itself ran successfully.
2. **Readiness result** — whether the business/quality gate passed.

A green GitHub Actions job means only **AUDIT_EXECUTED_SUCCESSFULLY**. It must never be presented as `PRODUCTION_READY`.

## Promotion gates

A platform may be marked `PRODUCTION_READY` only when all nine gates are PASS:

1. Execution — code/workflow executes without hidden errors.
2. Structure — TopN/count/rank/duplicate/empty/format constraints hold.
3. Truth — collected facts match sufficiently independent official evidence.
4. Semantic — the target meaning is preserved; no shelf/ranking substitution.
5. Fault/Silent Failure — malformed, blocked, stale, shifted or partial sources fail closed.
6. Identity — platform/title/official id or URL cannot cross-contaminate another title/platform.
7. Stability — repeated real runs remain valid across source changes.
8. Path Optimization — compared against safer/cheaper/lower-maintenance official alternatives.
9. E2E — downstream dedupe/research/writeback/frontend behavior is verified in Shadow.

Any FAIL, BLOCKED, or NOT_RUN keeps that platform at `PROMOTION_BLOCKED`.

System-level `PRODUCTION_READY` additionally requires every platform in the selected production scope to be ready and the Integration Branch gate to pass.

## Truth evidence independence

Truth evidence is graded instead of treated as binary:

- **L0 SELF_CHECK** — the collector effectively checks its own output. Never sufficient.
- **L1 SAME_PARSER_REPLAY** — the same parser is replayed. Never sufficient by itself.
- **L2 INDEPENDENT_PARSER_LIVE** — independent extraction from the same official source near the same time. Sufficient for Phase A, but explicitly carries time-drift/correlated-source limitations.
- **L3 INDEPENDENT_PARSER_FROZEN** — independent parsers operate on the same frozen official snapshot/hash. Stronger and preferred where technically possible.
- **L4 INDEPENDENT_OFFICIAL_CORROBORATION** — a second official endpoint/source independently confirms the fact. Strongest.

A live L2 comparison more than five minutes after the candidate batch is automatically blocked. Five minutes is a diagnostic ceiling, not a claim that the underlying source could not change sooner.

## Phase order and platform isolation

The system no longer waits for every platform before progressing:

- **A Truth Test** — per platform.
- **B Fault Injection / Silent Failure** — a platform may enter B after its A gates pass.
- **C Path Optimization** — a platform may enter C after its A/B safety gates pass.
- **D E2E Shadow** — global integration stage; only after the selected scope converges on an Integration Branch.

A blocked platform must not stall unrelated platforms. It remains isolated and `PROMOTION_BLOCKED`.

## Integration Branch rule

D must not run as the promotion decision on a raw Pilot, Shadow, or Audit branch.

Before D, an Integration Branch must contain the required minimum fixes from:

- current production/main baseline;
- verified Collector/Pilot work;
- Shadow research/newness/writeback safety fixes;
- Production Readiness framework and completed A/B/C changes.

The machine-readable requirements live in `INTEGRATION_MANIFEST.json`.

## Path optimization guardrail

C is comparative, not an invitation to perpetual refactoring. Keep the current path unless an alternative offers a meaningful net improvement across accuracy, stability, maintenance/human time, runtime/API cost, speed, evidence quality, identity reliability, extensibility, and external dependency risk.

## Safety

Readiness audits remain isolated from production writes unless separately approved.
