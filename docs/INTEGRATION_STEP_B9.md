# Integration Step B9 — Atomic Attempt Reservation Model

## Scope

B9 adds a process-local model for the final check immediately before a future
provider request. A call attempt is reserved only when the task revision,
authoritative run and snapshot digest, current provider approval, B4 decision,
and B5 attempt checkpoint still agree. Reserving the first attempt advances
the synthetic task from `PENDING` to `RESEARCHING` and records a deterministic
idempotency key under one lock.

Repeated attempts with the same key are rejected. Concurrent callers using
the same revision produce at most one reservation. A changed task revision,
superseded authority snapshot, expired provider approval, or mismatched retry
checkpoint blocks reservation. A restored synthetic snapshot retains consumed
keys, demonstrating how a durable adapter must prevent replay after restart.

## Boundary and limitations

The lock and snapshot are in memory. This is a concurrency/replay model, not a
database transaction, durable queue, or production guarantee. A real adapter
needs an atomic compare-and-set plus a unique constraint covering task,
stage, and attempt, and must define recovery when a process stops after
reservation but before the request begins. The API call itself is never made
here; no external credit is spent.

The preflight timestamp and provider evidence are checked again as part of
reservation. Current JSM production semantics remain unchanged; all production
gates stay `NOT_RUN`.

## Remaining work

- Durable database-backed compare-and-set and idempotency constraint.
- Worker crash recovery and reservation-to-call handoff.
- Protected provider review registry and live terms freshness.
- Real incident fixture replays, full isolated Staging E2E, and remaining gates.
