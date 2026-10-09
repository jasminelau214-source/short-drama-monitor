# Integration Step B5 — Per-call Research Attempt Policy

## Scope

B5 models one API call's bounded retry and checkpoint state transitions. It
encodes an initial attempt plus at most two technical retries, exponential
backoff (2 seconds, then 4 seconds), a cumulative 300-second active-time cap,
quota deferral, and explicit outcomes for insufficient evidence or identity
conflict. Backoff time and quota wait are not added to active processing time.

Resuming a quota-deferred checkpoint requires both explicit quota restoration
and a fresh eligibility revalidation. The checkpoint and attempt count are
preserved. A retry cannot become ready until its backoff elapses. Unknown or
business outcomes do not trigger mechanical retries; unresolved outcomes fail
closed to review.

## Boundary

This is a pure decision model. It does not call a provider, wire a Worker,
change a queue record, guarantee durable checkpoint storage, or perform a
transactional compare-and-swap. The caller must still execute B4 preflight
before every actual provider request and must persist/revalidate state safely.
Tests use synthetic timestamps and outcomes only.

Current production semantics remain governed by the 2026-09-20 Current
baselines. No real queue, provider, database, scheduler, Render service or
production resource is accessed by B5.

## Remaining integration work

- Protected provider verification/revocation boundary.
- Worker wiring for B4 revalidation and this per-call checkpoint model.
- Durable quota checkpoints and adaptive concurrency.
- Conditional eligibility checks before spend and writeback.
- Incident replays, complete isolated Staging E2E, and formal stability windows.

Promotion remains blocked; all runtime gates remain `NOT_RUN`.
