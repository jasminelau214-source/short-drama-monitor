# Integration Step B10 — Temporary Durable Reservation and Crash Recovery

B10 extends the B9 synthetic model with a disk-backed SQLite fixture. The
fixture accepts only explicitly opted-in `.sqlite3` files under the OS temporary
directory. It imports no application Worker, network client, production database
configuration, Scheduler, or live queue. No dependency installation is needed.

## Changes and reasons

- Store task revision, attempt key and checkpoint in one transaction. A task
  update cannot commit without its attempt record. SQLite enforces both a
  primary key and unique `(task_id, stage, attempt_number)` constraint.
- Reuse the B9 context gate at reservation and simulated handoff. The gate now
  also rejects malformed/unrevalidated preflight objects, previously performed
  calls, incomplete provider approvals, and timezone-naive approval times.
- Persist an exclusive `DISPATCH_STARTED` marker before a **simulated** handoff.
  A second owner cannot take the same attempt. Fresh context must still match
  the persisted revision, authority snapshot and exact provider approval.
- Record synthetic outcome, task revision and B5 checkpoint together. Only the
  recorded owner can submit the outcome; contradictory replays are rejected.
  A successful individual call does not mark the whole Research COMPLETE.
- Permit bounded technical retries only from the checkpoint already stored in
  the database, after B5 backoff. Caller-supplied retry readiness cannot bypass
  an unfinished attempt or a successful previous call. Quota suspension remains
  suspended across file reopen.

## Restart decisions

`RESERVED` means no handoff has been marked. The existing reservation may proceed
only through fresh context/revision checking; it is never reserved a second time.

`DISPATCH_STARTED` is deliberately ambiguous: a future external request could
have happened before the result was saved. Opening the fixture never retries it.
After a **confirmed stopped owner**, the synthetic recovery action marks that
attempt and task `REVIEW_REQUIRED`. A timeout alone is insufficient. This avoids
claiming exactly-once delivery to an external provider without its cooperation.

## Adversarial verification

Tests use independent interpreter processes against one temporary file and
actual process termination at three points: inside an uncommitted reservation,
after reservation commit, and after handoff commit. They check rollback, retained
keys, exclusive claims, stale authority/provider rejection, owner binding,
duplicate/conflicting outcomes, all three technical attempts, and quota pause.

The underlying problem is the gap between saving a reservation, making a network
request and saving its result. Durable keys solve local duplicate claims; they do
not close the external side-effect gap. Ambiguity is preserved for reconciliation
rather than represented as a successful recovery or automatically retried.

## Boundaries and remaining work

This is local disk durability under the tested process crashes, not evidence of
Supabase/PostgreSQL equivalence, machine-power-loss recovery, real provider
idempotency, or multi-host Worker behavior. The authority/provider context is
synthetic and caller supplied; protected live reads and approval revocation are
not implemented. Initial handoff uses the reserved eligibility plus a context
recheck; production needs a protected, reservation-aware revalidation path.

Cross-stage progression, durable quota resume, a real Worker adapter, reconciliation
tools, retention/cleanup, and authoritative-run update delivery remain unwired.
Stale reserved entries intentionally stay blocked; operational cleanup must be
designed without deleting consumed keys. No automatic lease expiry is provided.

Current baseline source semantics remain production authority. Required real
incident replays, Truth/Fault/Identity/Writeback gates, Staging E2E, rollback and
stability evidence remain `NOT_RUN`; promotion remains `BLOCK_PROMOTION`.
