# JSM Core Monitoring Platform Expansion Pilot

## Isolation

- Branch: `pilot/platform-expansion-p9-p10`
- Baseline: `pilot/web-top10-8platform@3783f4809640c2abde4b4771b10136bd961c4db4`
- `production_write = false`
- Do not modify `main`, `pilot/web-top10-8platform`, production Scheduler, production Supabase, production frontend, schema/RLS/env vars, or production deep-research queues.
- Generated evidence stays inside this pilot and GitHub Actions artifacts.
- No platform is promoted automatically.

## Scope

### Platform 9 — FreeReels
Technical validation may proceed independently.

Collection priority:
1. Official API
2. Official Web / structured endpoint
3. Web browser DOM
4. App automation
5. Manual evidence fallback only

Ranking integrity rules:
- Never turn recommendation order into ranking.
- Never merge several shelves into one chart.
- Never pad missing ranks.
- `RANKING` requires explicit rank evidence and identifiable ranking semantics.
- Ordered content without explicit rank evidence is `ORDERED_SHELF`.
- Personalized/recommendation content is `RECOMMENDATION_FEED`.
- Top20 may only be evaluated after a verified Top10 and only when the same source exposes explicit ranks 1–20.

Allowed FreeReels final states:
- `PASS_CANDIDATE`
- `PARTIAL`
- `NO_RANKING_SOURCE`
- `BLOCKED`

### Platform 10
Research only. No Collector may be developed until the user explicitly selects the platform.

Current state after candidate research:
`AWAITING_USER_PLATFORM10_SELECTION`

## Output layout

- `pilot_expansion/FreeReels/` — probe code and parsed results
- `pilot_expansion/platform10_candidates/` — candidate research
- `pilot_expansion/evidence/` — raw runtime evidence (artifact-only; not committed by workflow)
- `pilot_expansion/acceptance/` — machine-readable pilot state

## Source dimensions

Target apps remain `SHORT_DRAMA_APP`.
Evidence/market inputs remain separate source layers such as `OFFICIAL_WEB`, `APP_STORE`, and `DATA_MONITORING`.
Third-party rankings never substitute for an app's own ranking.
