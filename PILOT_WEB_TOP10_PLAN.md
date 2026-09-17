# JSM Web Top10 8-Platform Pilot

## Goal

Test one Web-first Top10 path for the original eight-platform scope without writing any pilot result into production Supabase tables or changing the production App ranking logic.

## Locked scope

DramaBox, DramaWave, FlexTV, GoodShort, MoboReels, NetShort, ReelShort, ShortMax.

FreeReels remains registered in the wider system but is excluded from this first pilot because it was the later ninth-source expansion.

## Rules

- Exactly one selected Web target per platform.
- Target quantity is Top10 for every platform.
- `PASS` requires ranks 1-10, 10 non-empty titles and no duplicate ranks/titles.
- 1-9 verified rows are `PARTIAL`; zero verified rows are `FAIL`/`NEEDS_ADAPTER`.
- Do not pad missing ranks, guess titles, or convert a category shelf into a main ranking silently.
- Save the source URL, collection date, target key, ranking type, extraction strategy, audit result and evidence hashes.
- Raw browser evidence is uploaded as a GitHub Actions artifact; parsed pilot JSON is committed only to this pilot branch.
- No automatic deep research is triggered from pilot results.
- No production database writes are permitted from the pilot runner.
- Production promotion requires explicit user confirmation after the whole scope is stable.

## Automatic progression

A cloud GitHub Actions run executes daily around 17:10 China time. It checks out this branch, runs the eight Web probes, stores parsed results under `pilot_data/YYYY-MM-DD/`, uploads raw evidence/screenshots as workflow artifacts, and commits only pilot outputs back to this branch.

The runner tracks consecutive valid Top10 runs. A platform is considered path-stable only after three consecutive valid runs. When all eight are stable, the generated status changes to `AWAITING_USER_CONFIRMATION`; it never merges into production automatically.

## Fallback policy

Web is the primary route. Existing NetShort/MoboReels emulator collectors remain as fallback/cross-check evidence only; this pilot does not require the user's computer, GPT desktop session, BlueStacks, or local ADB.
