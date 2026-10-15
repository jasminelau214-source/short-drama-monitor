# Integration Step C7 — protected candidate subject queue

C7 adds a separate queue and atomic publication adapter for the isolated candidate. It does not activate a worker, scheduler, live research queue or production persistence.

The versioned data-only context codec restores only listed contract dataclasses, dates, binary evidence and collections. Decoding grants no authority: the existing C1/B3 validators run over a freshly loaded snapshot at every research/writeback boundary. The adapter compares that validated snapshot digest with the current protected database authority.

The isolated migration refuses legacy research/analysis tables. Protected records bind their reviewed ranking digest to the current authority. Registration creates one global subject per protected canonical ID and attaches platform observations separately. Explicit claim request IDs reconcile lost replies, owner capabilities stay out of task/model JSON, and the database serializes claims to a maximum of two active subjects. Queue length itself has no fixed cap.

The server checks the Beijing collection date, current run/digest, context expiry/revocation, enabled worker and task deadline. A completed result requires the independently approved exact result hash, context hash and current authority/context revisions. Result COMPLETE and same-platform publication occur in one transaction. A failed publication rolls both back. Exact retries preserve the original publication timestamp and provenance; changed provenance is refused rather than rewritten.

Validation: nine offline codec/adapter scenarios and sixteen real disposable PostgreSQL scenarios were added. Auth identities, ranking/source/field approvals and provider results in these tests are synthetic. Some queue-only extra ledger rows intentionally test SQL metadata uniqueness/capacity; they do not prove live evidence truth or completed research for those rows. Earlier failures are retained outside the repository as diagnostic evidence.

C7 does not implement protected live ledger ingestion/review, automatic quota resume, unknown provider reconciliation, an API/frontend publication reader, real provider execution or a hard cancellation guarantee for an external call. Expired/ambiguous active claims remain held for explicit reconciliation; they are not automatically reissued. Directly approved fixtures are not an autonomous real-AI E2E.

The free professional crawler benchmark is separate evidence in the project workspace. It does not authorize a paid fallback or replace the formal Truth/Fault/Identity/Writeback gates. Firecrawl + Gemini runtime wiring is subsequent work. The previously reported Gemini key location is the Render staging service; its actual environment isolation still requires verification.

Rollback before activation is to leave this candidate uninstalled. Any later staging migration uses only the approved isolated project. Production, main, Render production, formal scheduler, legacy 115 tasks and live Research Queue remain unchanged.
