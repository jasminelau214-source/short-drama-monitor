# Integration Step B8 — Retry-State Reauthorization

## Scope

B8 closes a state-machine gap between the research contract and the preflight
gate. The initial external call is eligible only from `PENDING`. A technical
retry while the task is `RESEARCHING` requires a B5 checkpoint that is ready,
belongs to a supported stage, has a prior attempt, and remains under the
three-attempt cap. A not-yet-due or deferred checkpoint cannot authorize a
call.

For `DEFERRED_FREE_QUOTA`, the gate can revalidate the current authoritative
run and reviewed provider policy, but returns `authorized=false`. B5 can
resume only when quota restoration and this fresh eligibility result both
hold. The resumed task must pass preflight again with its ready checkpoint
before another call is permitted.

## Validation boundary

Synthetic tests now cover the full allowed state transitions across B3, B4,
B5, and B6. This fixes the earlier test shortcut that represented every retry
as `PENDING`. It remains an offline policy model: no Worker, durable queue,
provider request, transaction, or production resource is involved.

## Remaining work

- Durable task/checkpoint storage with atomic compare-and-set.
- Protected approval registry and live provider-term verification.
- Worker integration and crash/recovery behavior around reservation vs call.
- Required incident replays and complete isolated Staging E2E.

Promotion remains blocked; runtime gates remain `NOT_RUN`.
