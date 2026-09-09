# Collector Contract V1

## Purpose

All collectors must convert source-specific data into one normalized contract before analysis. The analysis layer must not care whether evidence came from an App screenshot, web scraper, official API, import, or future device automation.

## Source layers

`source_registry.source_group` must remain dimensionally separated:

- `SHORT_DRAMA_APP`: ReelShort, DramaWave, MoboReels, NetShort, DramaBox, ShortMax, FlexTV, GoodShort, etc.
- `VIDEO_SOCIAL`: YouTube, TikTok, Instagram/Reels, Facebook, etc.
- `DATA_MONITORING`: DataEye, 广大大, Sensor Tower, AppMagic, Similarweb, etc.
- `APP_STORE`: App Store, Google Play.
- `SEARCH_TREND`: Google Trends and similar search-trend sources.
- `OFFICIAL_WEB`: official websites, release pages, help centers, public rankings.

Do not mix these groups into one platform dimension.

## Collection priority

For each target, prefer the most stable legal/public path available:

1. Official API
2. Public web page / stable structured endpoint
3. Web scraper
4. App automation
5. Manual screenshot fallback

Manual screenshot remains a supported evidence source even after automation is added.

## Normalized observation contract

Each collected ranking row should normalize to the following logical shape:

```json
{
  "source_id": "shortapp_reelshort",
  "target_key": "daily_top_all",
  "collection_date": "2026-09-09",
  "ranking_type": "Daily Top",
  "category": "All",
  "rank": 1,
  "title": "Example Title",
  "heat": "123.4K",
  "tags": ["High Fantasy", "Superhero"],
  "metrics": {
    "collect": "1.2M",
    "like": "320K",
    "followers": ""
  },
  "evidence": {
    "type": "SCREENSHOT",
    "upload_id": "...",
    "storage_path": "..."
  }
}
```

## Evidence rules

- Raw evidence must be preserved before analysis.
- Screenshot-derived facts must remain separate from inferred/researched fields.
- Rank, title, heat, source-native tags, and source-native counters cannot be overwritten by external research.
- If Top N is incomplete, duplicated, or inconsistent, mark the collection job `NEEDS_REVIEW` or `PARTIAL`; do not silently repair facts with model guesses.

## Collector adapter interface

A future adapter should implement these conceptual steps:

1. `collect(target, date)` -> raw evidence
2. `store_evidence(...)` -> persistent evidence record
3. `parse(evidence)` -> normalized observations
4. `audit(observations, target)` -> completeness / duplicate / format checks
5. `emit(...)` -> analysis queue

The first four short-drama App targets use `MANUAL_SCREENSHOT` today. Later adapters may switch individual targets to `WEB_SCRAPE`, `APP_AUTOMATION`, or `API` without changing the downstream analysis contract.

## Current V1 targets

- ReelShort / Daily Top / All / Top10
- DramaWave / Daily Top / All / Top10
- MoboReels / Daily Top / All / Top10
- NetShort / Daily Top / All / Top10

## Next engineering checkpoint

Automate one source end-to-end first (recommended: ReelShort), run it for 7 days, keep manual screenshot fallback, then expand to the other three initial Apps before adding more platform groups.