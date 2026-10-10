# Step C2 — isolated PostgreSQL subject and conditional write experiment

Base: `bdc05bcbc4c29568494969375c8029c24a54e3bf`; same sole integration branch. Current still governs production. This is a synthetic schema experiment, not a deployable Supabase migration or live adapter.

## Why this step

C1 validated the actual Python completion/local writer, but its SQLite transaction could not protect supplied authority/review snapshots from concurrent changes. Global first discovery also had no database uniqueness experiment. C2 checks those two missing database properties using real PostgreSQL, preserving the existing application's blocked live path.

## Isolation and reproducibility

The harness accepts an explicit binary directory and creates a fresh cluster under the OS temporary directory. It accepts no DSN, cloud URL or existing data directory. It clears inherited PostgreSQL connection/options variables, uses random credentials without printing them, binds only `127.0.0.1` on an allocated port and disables user psql startup files. It neither installs a system service nor changes PATH. The SQL refuses to initialize without both the fixture database name and its server scope marker. Startup and teardown inspect/stop only the newly owned cluster, including a partial startup failure; tests remove that temporary cluster after shutdown. Downloaded binaries remain in the task's separate `tools/` cache.

The runtime used for this verification is PostgreSQL 17.11 (EDB Windows binaries), downloaded through the Windows link on the [officially linked binary distribution page](https://www.enterprisedb.com/download-postgresql-binaries). The local archive digest and resolved source URL are recorded outside the repository; that digest is an integrity record, not a claim of a vendor signature or published checksum. [PostgreSQL's Windows page](https://www.postgresql.org/download/windows/) points to this distribution route. No paid service is used.

Run explicitly:

```text
python test_postgres_integration_v2.py --bin-dir <absolute path to PostgreSQL bin> -v
```

Missing binaries cause an error rather than a skipped/green database test. The existing offline suite remains separately runnable.

## Database experiment

Synthetic records bind canonical identity, platform, source entity, scope, run and separately protected review rows. Canonical ID uniqueness creates one research subject across concurrent platform discoveries while preserving per-record observations. Titles cannot merge identities. C1 validates each seeded result and platform authority before its digest enters the fixture review ledger; fixture administration is the only source of review approvals.

The worker login cannot edit protected authority/reviews, research subjects or overrides directly, and cannot call the internal binding guard. It can execute only the two public fixture operations. Those functions run with a fixed safe search path, minimal grants and one lock order: authority, review, record, subject. They recheck current run, snapshot/revisions, identity, platform and live review validity before a write. Exact approved payload bytes are hashed with [PostgreSQL SHA-256](https://www.postgresql.org/docs/17/functions-binarystring.html); modification requires new protected approval. The conditional upsert, terminal research state and override commit together. Reapplication preserves timestamps, and a COMPLETE global result can be reused only for another independently bound/reviewed platform record.

Concurrency tests exercise actual independent PostgreSQL connections: discovery races, duplicate writes and revocation committing while a writer waits on the review lock. Revocation before the writer acquires the locked review blocks that write. Revocation after the writer has locked it is serialized after that write; this is a defined transaction order, not a promise to undo already committed publication. Rollback removes both the override and terminal state change. See [PostgreSQL locking](https://www.postgresql.org/docs/17/explicit-locking.html) and [ON CONFLICT](https://www.postgresql.org/docs/17/sql-insert.html).

## Red/blue assessment and remaining work

Definer functions explicitly search trusted schemas before `pg_temp`; a worker-created temporary review table cannot mask the protected table. Schema creation and privilege revocation/grants commit together, avoiding a temporary public-execute window. These controls follow [PostgreSQL's definer-function guidance](https://www.postgresql.org/docs/17/sql-createfunction.html#SQL-CREATEFUNCTION-SECURITY).

The root failures being tested are duplicate global subject creation, self-authorized evidence and a stale read followed by an unconditional write. The blue controls are uniqueness, protected database privileges, row locks, exact payload binding and revision rechecks. Red cases try a foreign environment target, missing fixture marker, direct worker mutations, altered identity/platform/payload, stale run/review revisions, expired/revoked approvals, concurrent registration/writeback and transaction rollback.

Evidence remains synthetic. The schema is intentionally different from production, and does not prove Supabase RLS/PostgREST/schema parity or all possible state transitions. Tests administer the RESEARCHING transition without calling a provider. Real protected identity/field-review/provider adapters, durable worker claims/retries, production-compatible schema design, incident samples (including the 115 legacy tasks), full staging E2E, rollback and the formal windows/gates remain outstanding. Application, production Supabase, Render, scheduler and live queue code/configuration are not modified in C2. Promotion remains BLOCK_PROMOTION; the manifest never treats this test as a passed business gate.

Exact-commit offline/database logs, archive identity and temporary-cluster shutdown evidence are kept under the task's `evidence/` directory. Candidate rollback is abandoning/reverting these six branch-only files; no production data rollback is involved.
