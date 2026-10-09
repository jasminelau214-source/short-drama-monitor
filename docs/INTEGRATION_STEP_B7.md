# Integration Step B7 — Synthetic Research Preflight Chain

## Scope

B7 exercises the research safety decisions together using synthetic fixtures:

1. B3 validates a complete Top10 and selects its current authoritative run.
2. B6 verifies a provider policy against exact claim/artifact bytes, an
   independent approval record, and a trusted host fixture.
3. B4 permits only the current observation and carries its authority snapshot.
4. B5 models transient retry, quota deferral, and fresh eligibility checks
   before resumption and the next attempt.

Adversarial cases include a later valid run superseding the queued origin and a
revoked provider approval. Both fail closed before a simulated provider call.

## Boundary

This is a synthetic offline integration test, not the system Integration Gate
or a production E2E. Provider results are simulated values; no external call,
credit spend, queue transition, database transaction or writeback occurs. The
trusted approval ledger/host registry remain fixtures, and no incident data
is read.

The Current baselines remain production authority, including the existing
`SHORT_DRAMA_APP` research restriction. This test does not activate the V2
`OFFICIAL_WEB` research rule.

## Remaining integration work

- Protected provider approval/host registry and live pricing review.
- Durable queue/checkpoint adapters and Worker integration.
- Transactional authoritative-run recheck before spend and conditional write.
- Isolated replay of required incident samples and complete Staging E2E.
- Truth, Fault, Identity, Writeback, and stability gates.

Promotion remains blocked; runtime gates remain `NOT_RUN`.
