# Integration Step B6 — Reviewed Provider Policy Evidence

## Scope

B6 verifies a provider's free-tier claim against exact captured artifact bytes,
an independently approved claim digest, an unexpired approval, and an exact
trusted HTTPS host entry. The claim must explicitly say the tier is free and
automatic paid fallback is disabled. Duplicate JSON keys, unknown fields,
expired or revoked approvals, artifact changes, untrusted hosts, and unknown
policy status fail closed.

A successful review produces the provenance-bearing `FreeProviderApproval`
consumed by the B4 preflight. Its approval and artifact references are carried
along with the provider ID, verified free-tier assertion, and bounded validity.

## Boundary

The test fixture is synthetic; B6 does not retrieve, crawl, or confirm today's
provider pricing. The approval ledger and trusted-host registry are inputs and
must be injected from a protected operational boundary. B6 does not implement
that storage, reviewer authentication, revocation feed, freshness operations,
or worker wiring. A hash proves byte binding, not that the source statement is
true or the approval ledger is protected.

No provider API, queue, database, scheduler, Render service, or production
resource is contacted. Current JSM production source semantics remain in force.

## Remaining integration work

- Protected approval-ledger and host-registry injection.
- Current provider terms review, scheduled expiry, and revocation operations.
- Worker wiring that requires B4 and B5 before each call.
- Conditional checks before external spend and writeback.
- Incident replays and full isolated Staging E2E.

Promotion remains blocked; runtime gates remain `NOT_RUN`.
