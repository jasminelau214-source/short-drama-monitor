# JSM Production Readiness Standard

This standard is an additive promotion gate. It does not redefine historical collector or research statuses.

## Compatibility rule

Existing local statuses keep their original meanings:

- `PASS_CANDIDATE` / `PASS_VERIFIED`: collector path produced a structurally valid target result under its existing audit.
- GitHub Actions `success`: the workflow executed successfully.
- Research `COMPLETE`: one research task met its current research completion rules.

None of those statuses alone means the system is production-ready.

## Promotion gates

A path may be marked `PRODUCTION_READY` only when all nine gates are PASS:

1. Execution — code/workflow executes without hidden errors.
2. Structure — TopN/count/rank/duplicate/empty/format constraints hold.
3. Truth — collected facts match an independent extraction from the same official evidence.
4. Semantic — the target meaning is preserved; no shelf/ranking substitution.
5. Fault/Silent Failure — malformed, blocked, stale, shifted or partial sources fail closed.
6. Identity — platform/title/official id or URL cannot cross-contaminate another title/platform.
7. Stability — repeated real runs remain valid across source changes.
8. Path Optimization — compared against safer/cheaper/lower-maintenance official alternatives.
9. E2E — downstream dedupe/research/writeback/frontend behavior is verified in Shadow.

Any FAIL, BLOCKED, or NOT_RUN keeps promotion at `PROMOTION_BLOCKED`.

## Phase order

A. Truth Test
B. Fault Injection / Silent Failure
C. Path Optimization
D. E2E Shadow

A and B may reuse evidence, but C cannot promote a path before A/B pass. D is the final chain gate.

## Safety

Readiness audits must remain isolated from production writes unless separately approved.
