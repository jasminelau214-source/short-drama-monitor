# Step C3 — connected research handoff and historical incident review

Base: `ed5b2e1e4a66c84480272e64e1ffad506a232f40`, on the existing sole integration branch. On 2026-10-12 the user expanded the objective from Step A to the complete V2 upgrade and eventual production-flow validation. This authorizes continued candidate implementation; production activation still requires completed gates and a concrete cutover/rollback approval. The two Current references remain unchanged and authoritative for production.

## Problems found and repairs

C1 checked the model result, then inserted `searchMeta` into that result. The actual writer rechecks an exact schema and consequently refuses a supposedly COMPLETE pipeline result. Keep search progress in the outer outcome envelope and preserve the exact research payload.

The worker also omitted the protected completion context at both calls, and its legacy claim path could alter old tasks before discovering that context was absent. A trusted-code `ResearchHandoff` now supplies a dedicated V2 claim/update boundary and fresh contexts before research, after the actual model/source result, and before writeback. Default app startup and admin scheduling without this boundary cannot claim the legacy queue. No live adapter is installed. A protected post-research reviewer can provide exact-value evidence after seeing the actual result; a task/model cannot self-approve. Business review failures do not become technical retries. The candidate drain has no default 20-task cutoff; parallelism/free quota/retry runtime still need implementation.

The actual public-data route also left SQLite read connections open. Close the override reader and the connections passed into the publication reader, so results become visible and the temporary database can be removed reliably on Windows.

## Evidence and honest limits

New connected tests use the actual worker, research gate, local writer and `/api/data` handler; only search/model calls and the protected adapter are synthetic. They check payload compatibility, source/result review timing, review loss/revocation, business refusals, no unconfigured legacy claims, idempotent draining beyond 20 tasks and publication through the actual data route. This is not a browser, real provider, cloud persistence or full Staging E2E test. C1's remote writer remains explicitly blocked. The separate status update and local override transaction are not proof of a remote atomic COMPLETE/writeback.

Read-only production sampling captured 162 research tasks and 36 historical analysis runs in one SELECT: 115 old Web PENDING tasks, 47 COMPLETE items and five incomplete runs carrying success status. Private projections, capture SQL, reviewed current function digest, replay script/log and per-row decisions stay outside Git under the task evidence directory.

Ten local PostgreSQL checks replay eight original Web parent runs and the five incomplete runs against the inspected current enqueue function. They generate zero new tasks; the five incorrect success labels survive, proving the enqueue guard does not repair state semantics. Forty-seven stored COMPLETE results cannot grant themselves V2 approval: 46 fail the current exact schema and one lacks protected context. This does not prove false content. Optional-field absence alone is not grounds to invalidate the 46 items; the original 13 uncertain identities still require evidence review.

The negative control changes newness to `new` and removes only the local source guard; it generates 88 tasks, not 115. Twenty-seven stored ShortMax category rows lack rank. The experiment deliberately does not invent ranks to manufacture historical reproduction. It does not prove the historical creation path. A separate synthetic control confirms that the current production function defaults a missing source type to App. These are candidate repair targets, not production modifications performed in C3.

The snapshot exporter was corrected to preserve absent versus JSON-null keys. Version 1 is retained as superseded; only version 2 supports the replay. Earlier harness/test failures were repaired and the final replay passed. Formal incident gates stay NOT_RUN because current-snapshot classification and controlled mutations do not establish complete historical causality or truth/identity/operational acceptance.

## Remaining complete-upgrade work and rollback

The 2026-10-12 read-only inventory found one production Supabase project and no development branches. Render integration staging still serves V1 `e9846a57591c3b09c7b4e2dab44590591f3fdb60`, with auto-deploy off. The user approved a separate staging project in the existing organization only at $0; the organization is Free, but the connector's get_cost method is unavailable and the dashboard requires sign-in. No cloud project, deployment or paid resource was created.

Next integrate the live protected identity/review/provider/queue adapters and six-platform collectors, durable claim/reservation/retry/quota/concurrency, compatible schema/Edge API, publication, scheduler and visible partial coverage. Verify current Truth/Fault cases, stage the exact candidate with a separate database, run genuine provider-to-frontend E2E and rollback, then complete three formal collection windows. Only then present a production cutover; afterward test the production flow and observe seven days. Free-plan and model access must be verified without paid fallback. The current main/production/live queue remain unchanged.

Candidate rollback is reverting these seven integration-branch files or abandoning the candidate. No production data needs restoration. Tests or inventory success do not change BLOCK_PROMOTION.
