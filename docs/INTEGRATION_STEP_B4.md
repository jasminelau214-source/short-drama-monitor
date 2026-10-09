# Integration Step B4 — Research Eligibility Preflight

## Scope

B4 adds an offline, fail-closed decision function immediately before a future
external research call. It combines B3's authoritative observation decision
with task-state checks, a global COMPLETE check supplied by the caller, and a
separately reviewed provider policy that must explicitly prove an active free
tier and deny paid fallback.

An allowed result means **preflight only**. It does not call a provider, spend
credits, change task state, enqueue work, write back research, or perform a
database compare-and-swap. The provider approvals and COMPLETE lookup are
caller inputs; this step does not implement or authenticate their protected
runtime boundary. The returned run ID and snapshot digest are evidence for a
future transactional check, not a lock against a concurrent update.

Only `PENDING` can be considered. `DEFERRED_FREE_QUOTA`, `REVIEW_REQUIRED`,
`NEEDS_GPT`, terminal states, and an already completed global research subject
are denied. A deferred task can only be reconsidered after quota restoration
and a fresh eligibility check. Invalid or superseded observations fail closed.
Unknown, expired, revoked, non-free, or paid-fallback provider approvals do not
authorize a provider. No provider-name fallback is inferred.

## Boundary and evidence

The implementation is synthetic/offline-only. Tests stub the B3 authority
decision; B3's independent tests exercise the actual synthetic ranking and
identity approval fixtures. This split verifies the preflight decision logic
without claiming that external evidence is true or that the worker uses it.

The JSM Current baseline remains production authority. It currently restricts
automatic research to `SHORT_DRAMA_APP`; V2's `OFFICIAL_WEB` first-seen rule
remains a candidate-contract conflict and is not activated by B4.

## Remaining integration work

- Protected provider verification and revocation boundary.
- Worker call, per-call retry, backoff, active timeout, and quota checkpoint wiring.
- Atomic eligibility recheck immediately before spend and conditional writeback.
- Concurrency/quota runtime behavior and incident-sample replay.
- Full isolated Staging E2E and required formal windows.

Promotion remains blocked; all runtime gates remain `NOT_RUN`.
