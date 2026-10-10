# JSM Web Continuity V2 — isolated collection recovery (2026-10-10)

**Status:** CANDIDATE / ISOLATED COLLECTION ONLY / BLOCK_PROMOTION. Not a production deployment.

## Approved narrow purpose
Restore real, date-stamped **official website observations** while the V2 source/identity/worker/integration gates remain incomplete. This branch is based on latest `main` and selectively copies only the legacy Web Pilot collector components required to obtain six candidate source Top10s. No old pilot branch merge.

## Six V2 official web targets
DramaBox — Trending; FlexTV — Top in FlexTV; GoodShort — Top in GoodShort; MoboReels — Popular Series; NetShort — Trending Now; ReelShort — TOP. Exactly 10 ordered titles for each target. English / en-US; US when an explicit region selector exists. Geo/locale observed at source must be recorded and must not be inferred solely from browser headers.

DramaWave and ShortMax are paused; they must not be added via alternate mismatched shelves. No source-to-App authority substitution.

## Runtime
`.github/workflows/isolated-web-continuity-v2.yml` runs on an isolated branch push to `continuity_triggers/YYYY-MM-DD.txt`. The first trigger is `continuity_triggers/2026-10-10.txt`. Daily **16:00 Asia/Shanghai** task is configured outside GitHub Actions to create that daily branch-only trigger; GitHub Actions `schedule` on non-default branch is NOT supported. A second daily **16:25 Asia/Shanghai** monitoring task reports exceptions. These external tasks are an operational dependency: failure or disconnection must be surfaced, not silently counted successful.

The workflow stores snapshots under `pilot_data/YYYY-MM-DD` on this **test branch only**, with raw HTML/screenshot evidence uploaded to GitHub Actions artifacts for 30 days. Successful platform snapshots are retained independently if another platform fails. The gate report `CONTINUITY_GATE.json` lists accepted observations, rejected/missing platforms, coverage and SHA-256. Collector statuses PASS_CANDIDATE/PASS_VERIFIED are **not** equivalent to Truth/Fault/Identity/Integration acceptance.

## Hard prohibitions
No modification or write to production Supabase, Render production, main, secrets, production Windows Scheduler, formal Research Queue, app overrides or API/Frontend publication. No automatic research or queued research, even for `OFFICIAL_WEB` first-seen titles. Historical 115 legacy Research PENDING remain frozen. Invalid/incomplete rows fail closed. No copying current positions backwards into dates that cannot be reconstructed from actual dated evidence.

## Red/blue fault and audit checklist
- RED: timeout, 403, bot-block, English/US drift, exact ranking semantic drift, missing/duplicate rank, stale run, missing data artifacts, partial job, duplicate daily trigger, GitHub permissions disabled, action runner unavailable, artifact expiration and GitHub push conflict.
- BLUE: strict 6-target allowlist; independent platform records and visible 0..6 coverage; fail-closed Top10 gate; source dates and evidence digests; no producer retry amplification; re-run only with same-day evidence; alert on missing/queued/failed action; branch-only parsed snapshot persistence; keep unfinished/partial as diagnostic not production success.
- Not proved: current six-platform live fetch, exact source current truth, 3 continuous formal acceptance windows, production-v2 schema parity, 115 incident replay, full staging E2E, 24/7 executor availability.
- Platform-level Truth/Fault and all ten Integration Gate components must pass separately before promotion. The V2 contract dated 2026-10-08 is candidate-only and does not supersede production authorization.

## Recovery boundaries
Historical production DB newest App run: 2026-09-17. Historical production Web run: 2026-09-16. Legacy isolated Pilot has evidence 2026-09-18 through 2026-09-22 (not production-authorized). No claim is made for missing 2026-09-23 through 2026-10-09. Backfill those dates only if independently dated source evidence is available.

## Rollback
Disable the separate scheduled trigger and alert task; stop the isolated workflow; retain branch snapshot/audit evidence for later review. Production has not been changed and requires no production rollback.
