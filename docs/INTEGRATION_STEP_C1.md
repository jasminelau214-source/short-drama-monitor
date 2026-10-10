# Step C1 — candidate completion gate and local writeback

Base: `9e968165aeedd0ced9349c23c7175df7daf5cfcf` (accepted B10), on the sole V2 integration branch. Current baselines still govern production. This phase grants no production, deployment, scheduler or live queue authorization.

## Problem and behavior

The main baseline can label research COMPLETE with missing core fields, invent citation links when the model cites none, and fall back to another platform's matching title during writeback. Those are separate failure modes: passing an AI call is not evidence of completion, and a matching title is not content identity.

`research_completion_v2.py` supplies a shared deterministic validator. It requires exact schema/types, every core field and canonical title, at least medium confidence, resolved identity, declared missing optional fields, captured public HTTPS citations, current B3 authority and B2 identity reviews. Each populated research field must have separately reviewed support bound to its exact value, canonical identity, citation and captured artifact bytes. Changed or expired/revoked reviews fail closed. Placeholder text cannot satisfy completeness. Schema/identity/authority/source conflicts route to REVIEW_REQUIRED; insufficient core/evidence routes to NEEDS_GPT. No citation is fabricated.

The candidate pipeline validates task/observation bindings before search, then validates the actual model result. The writer independently repeats completion and authority checks, selects a unique record by its bound record ID, and requires exact platform, canonical identity and source observation. Title or platform-history matching provides no fallback. Local writes use a transaction, include absent optional values to clear stale analysis, preserve unrelated manual fields, and make identical reapplication a no-op. Technical SQL errors roll back. A writeback business refusal routes to review in the candidate worker rather than COMPLETE/FAILED.

## Trust boundary and implementation limits

The field-review ledger is an input from a protected reviewer, not a model assertion. The validator verifies bindings, not whether prose is true or a reviewer's judgment is correct. No protected runtime adapter currently supplies live authority, canonical identities, captured artifacts, field reviews, free-provider policies or retry checkpoints to the legacy worker. A task or model cannot grant itself these approvals. Normal calls without that context stop before external search/model calls. Existing legacy records do not gain identity bindings from their titles.

Field review includes interpretive judgments and evidence sufficiency; it is not currently an automated review service. Operational cost and review expiry/revocation maintenance remain unresolved. The synthetic approvals in tests must never be promoted into real ledgers. This phase does not enable a live research pipeline, prove global newness, or change the provider whitelist/old queue schema.

Remote research writeback explicitly refuses while the conditional adapter is unimplemented. Local SQLite transactions protect only local overrides; authority and review ledgers remain supplied snapshots, not atomic database witnesses. Changes concurrent with a write need a protected transactional recheck and conditional remote commit in a later phase. No claim of PostgreSQL equivalence, live writeback atomicity or whole-system readiness is made.

The historical Shadow candidate at `f74798feb8941e663192e903c0b03b3b4543c093` was inspected as evidence only. Its completeness checks and same-platform idea informed the minimum change, but title/model-based identity and platform-history fallback were not carried over. No old branch was merged or cherry-picked wholesale.

## Isolated validation and remaining gates

Red/blue review distinguished the three root failures (loose completion, fabricated citations and title fallback) from their visible symptoms (incorrect COMPLETE and wrong-record updates). Business refusals, technical failures, duplicate writes and rollback were checked separately. Repeated source-sanitization signals stop before the model call; invalid or duplicate-key model JSON goes to review. Legacy application imports also run an HTML migration: tests redirect it to a temporary copy, so validation adds no UI changes. Maintained risks include protected-ledger provenance, concurrent authority changes and human field-review workload; passing these tests does not close those gaps.

Synthetic reviewed Top10 fixtures exercise the actual pipeline entry and actual app writer with a temporary SQLite file. Search/model calls and queue operations are mocked; sockets are forbidden in the new tests. Cases include missing fields, malformed JSON shapes/types, unsupported citations, substituted content/artifacts/identities, expired/revoked approvals, superseded runs, cross-platform/history/title fallback, duplicate record IDs, write-time revocation, duplicate writes, optional-field clearing, SQL failure rollback and worker refusal routing. Import-time app data paths are temporary. No server, scheduler or actual worker queue is started.

The manifest records this evidence only as synthetic completion/local-writer coverage. Incident replays, protected live adapters, global first-seen atomic uniqueness, PostgreSQL conditional writes, free-only provider/retry wiring, full staging E2E, rollback and all ten Integration Gates remain outstanding. Staging still needs its own isolated database and deployment at the exact candidate commit, plus three formal collection windows. Production stays BLOCK_PROMOTION.

Rollback for this candidate is to revert/abandon its integration-branch changes before deployment; there are no production data changes to reverse. Exact-commit verification is recorded outside the repository under the task's `evidence/` directory after committing.
