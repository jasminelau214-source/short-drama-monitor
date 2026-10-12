# V2 candidate C4: bounded provider execution

Based on accepted C3 `d169a063118733ecb41c8cfeb57db6c2d2c9558f`. Current baselines and production remain unchanged. This is candidate runtime development under the user's 2026-10-12 full-upgrade request; it is not production promotion.

## Concrete behavior

The actual research pipeline now accepts an explicit `ResearchExecutionSession`. Keys in environment variables cannot activate it. The legacy Tavily and Gemini clients with four hidden attempts cannot run research through the default path. The App screenshot analysis client is unchanged.

A session requires a protected code callback before every attempt and again after waiting for a concurrency slot/backoff. The callback must load current authoritative-observation eligibility, reviewed public provider policy and a separate current account review bound to the exact credential hash and model/product. Billing must be disabled and quota available. Request/task/model JSON cannot supply these objects. This code validates those trusted objects; it does not implement live account inspection or verify their underlying account artifacts itself.

One shared limiter starts at two calls and drops to one on HTTP 429. The Worker processes at most two tasks concurrently, drains without the previous 20-task cutoff and uses the shared limiter. The queue's protected claim adapter remains required. No default claim of the old research queue exists.

The single-request HTTPS transport has fixed Tavily/Gemini endpoints, refuses redirects, never switches providers/models or enables billing, and has no retries. Search is basic only and model tools/paid features are excluded. Tavily status 432/433 defers; 429 retries with reduced concurrency. Gemini 429 with a bounded Retry-After retries, otherwise conservatively defers because daily quota versus rate limiting is ambiguous. Error bodies and credentials are not logged or journaled.

The candidate local SQLite journal reserves each call before dispatch, retains at most three attempts per logical call, enforces at most three logical search rounds and one analysis call, stores successful responses and a cumulative 300-second provider/backoff budget. Reopening a successful call reuses it only after current authorization and exact request/authority/credential binding checks. Quota waits do not consume this budget. An explicit freshly authorized quota resume preserves the attempt count. An INFLIGHT call after a crash is held for reconciliation, never automatically repeated. Unknown databases are rejected without changing their schema.

The actual pipeline currently searches one or two rounds as needed; the journal's maximum of three is a ceiling, not a requirement to run three. Candidate search validation recognizes the approved six platforms and excludes the two paused platforms. This does not install six collectors, establish ranking truth or change the production whitelist.

## Verification and limitations

Connected tests exercise the actual Worker -> pipeline -> session -> local journal -> local writer. Only provider responses and protected approvals are synthetic. A model-quota interruption resumes with the saved search response and exactly one additional model call. Other cases cover bounded retries, exhausted retry budgets after reopening, account expiry/revocation, authority/request changes, concurrent duplicate tasks, uncertain crash outcomes, search limits, 429 concurrency reduction, no paid tools and old-client refusal.

This journal is durable only on its selected local disk. Render's ephemeral filesystem is not a production durability solution. The live Supabase queue, atomic authority/reservation and COMPLETE/writeback adapter, RLS/PostgREST parity, protected context/review/account loaders and remote outcome reconciliation still need integration. The 300-second ledger currently covers provider calls and retry waits, not the entire collection/review/writeback wall-clock lifetime. Per-request network timeouts and post-call budget rejection do not prove a hard deadline or cancellation of an already accepted third-party request. These limits remain explicit in the Promotion Manifest.

No cloud project, deployment, official scheduler, live queue execution, external provider request or paid resource was used in C4. Supabase's quote tools remain unavailable and the dashboard remains on its sign-in page. Isolated cloud E2E, current six-platform Truth/Fault acceptance, three formal collection dates and rollback remain incomplete. Formal gates stay NOT_RUN and production promotion stays BLOCK_PROMOTION.

## Rollback

Revert the eight files changed in this candidate commit. No production restoration is necessary. Retain any candidate call journal for reconciliation rather than deleting ambiguous INFLIGHT records. Do not deploy this local journal as if it were a cloud queue or replay stored production PENDING tasks through it.
