# JSM Promotion Surface Audit

Updated: 2026-09-21
Scope: `main@a206bc9...` -> `integration/core-contract-v1-2026-09-20@e20ff930...`

## Executive finding

The Integration branch is now technically well-tested, but it is no longer a
small patch surface.  It is 206 commits ahead of main with 95 changed files.
A green Integration Gate therefore proves the integrated branch is internally
consistent; it does not by itself justify merging the entire branch to
production.

Promotion should remain blocked until the intended production surface is
explicitly selected.

## Current changed-file surface

- Runtime / production-candidate files: 37
- Contract tests and fixtures: 34
- Integration-only validation / audit files: 20
- Documentation files: 2
- UI-specific files: 2
- Total: 95

The largest single product-facing change is `index.html`:
+3573 / -1501 lines relative to main.  The branch also contains
`docs/UI_V2_LOCK.md` and frontend contract-rebase tests.

## Why this matters

The original Integration rule was to consolidate verified minimal changes
without whole-merging divergent Pilot/Audit/Shadow branches.  That rule is
still respected at the source-branch level, but the accumulated Integration
surface has grown enough that a direct whole-branch production merge would
reintroduce a different review problem: validation scaffolding, backend
contracts, collectors, deployment changes, and UI V2 would all be promoted in
one operation.

## Promotion units that must stay distinguishable

### Core runtime / data contracts
Includes identity, source semantics, ranking lifecycle, research guards,
writeback guards, state semantics, persistence/readiness and required DB
migration.

### Collector / scheduler runtime
Includes hardened NetShort V6 / MoboReels V3, App/Web sync runners and verified
official-Web collectors.

### UI V2
Includes the large `index.html` rebase and UI-specific runtime patch/lock.
This is product-facing and should not be silently bundled merely because the
Integration tests are green.

### Validation-only assets
Includes ephemeral staging services, fault-audit scripts, Integration-only
workflows and historical incident fixtures.  These are useful evidence but are
not automatically production runtime requirements.

## Current evidence

At `e20ff930...`:
- JSM Integration Contract Gate: PASS
- 189 Python contract/regression tests: PASS
- PostgreSQL identity contract: PASS
- Collector direct import: PASS
- Ephemeral Full Integration Staging Phase A: PASS
- Ephemeral Full Integration Staging Phase B: PASS
- Phase B recovery, idempotent replay and pinned-main rollback reader: PASS

Production main remains `a206bc9...`.

## Gate before production

Before any production merge, explicitly decide whether UI V2 is part of the
same production promotion as the backend/core contracts.

After that decision, build the production candidate from the latest main with
an explicit manifest of included runtime files and required tests.  Do not use
“merge the entire Integration branch” as the promotion mechanism merely
because the Integration branch is green.

No production write, merge, deployment or database mutation is authorized by
this document.
