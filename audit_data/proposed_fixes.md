# Proposed fixes — audit branch only

These are implementation candidates derived from the 2026-09-16/17 audit. They are **not applied to production**.

1. **Run supersession before research enqueue**
   - Scope key: `collection_date + platform + sourceType + targetKey`.
   - When a later complete run becomes authoritative, tasks unique to the superseded run must stop contributing to daily research/counts.
   - Reuse existing `REVIEW_REQUIRED` if avoiding a schema change; set an explicit `SUPERSEDED_BY_LATER_RUN:<run_id>` marker.

2. **Validate research output before COMPLETE/apply_override**
   - Enforce current controlled genre/audience values.
   - Enforce all current schema required keys.
   - Require evidence/source URL consistency.
   - Failed validation => `NEEDS_GPT` or `REVIEW_REQUIRED`, never `COMPLETE`.

3. **Make source semantics part of every metric**
   - Never aggregate App and Official Web into one ranking count.
   - Never aggregate different target_key/ranking_type shelves into one Top10.
   - Dashboard counts should explicitly name raw/effective/structural/current-scope/research quantities.

4. **Historical repair order**
   - Rebuild facts first.
   - Freeze corrected fact set.
   - Derive research candidates from corrected latest facts.
   - Re-run/revalidate research.
   - Only then generate market/content conclusions and front-end summaries.

5. **Keep raw evidence immutable**
   - All 9/16 and 9/17 raw rows remain preserved.
   - Corrections are separate candidate artifacts until explicit user approval.
