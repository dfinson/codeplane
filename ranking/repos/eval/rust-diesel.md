# diesel-rs/diesel

| Field | Value |
|-------|-------|
| **URL** | https://github.com/diesel-rs/diesel |
| **License** | MIT/Apache-2.0 |
| **Language** | Rust |
| **Scale** | Large |
| **Category** | ORM |
| **Set** | eval |
| **Commit** | `f5e93c0125694914dca6888ae09f8d84528353f6` |

## Why this repo

- **Multi-crate workspace**: Core ORM (`diesel`), derive macros (`diesel_derives`), CLI (`diesel_cli`), migrations (`diesel_migrations`), dynamic schema (`diesel_dynamic_schema`)
- **Multi-backend**: PostgreSQL, MySQL, SQLite backends with shared abstractions and backend-specific query builders
- **Complex type system**: Extensive use of Rust's type system for compile-time SQL validation, trait-based query building, and zero-cost abstractions

## Structure overview

```
diesel/
├── diesel/src/              # Core ORM: connection, query_builder, expression, types
│   ├── connection/          # Connection trait, transaction manager, statement cache
│   ├── query_builder/       # AST pass, bind collector, SQL generation, clauses
│   ├── query_dsl/           # QueryDsl trait: filter, select, join, order, limit
│   ├── expression/          # SQL expressions: operators, functions, case_when, cast
│   ├── pg/                  # PostgreSQL backend, COPY, arrays, ranges, JSON, upsert
│   ├── mysql/               # MySQL backend, connection, bind, types
│   ├── sqlite/              # SQLite backend, connection, custom functions
│   ├── associations/        # BelongsTo, HasMany relationship macros
│   ├── migration/           # Migration trait definitions
│   └── upsert/              # ON CONFLICT (upsert) support
├── diesel_cli/src/          # CLI: setup, migration, schema inference, print_schema
├── diesel_derives/src/      # Proc macros: Queryable, Insertable, AsChangeset, table!
├── diesel_migrations/src/   # Embedded + file-based migration runner, harness
├── diesel_dynamic_schema/   # Runtime-defined tables and columns
└── dsl_auto_type/src/       # Automatic type inference for DSL expressions
```

## Scale indicators

- ~833 Rust source files
- ~147K lines of code
- Three database backends with shared abstractions
- Proc macro crate with extensive derive support

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `AnsiTransactionManager` not resetting state after connection-level error during commit

When `commit_transaction` encounters a connection-level error (not a database error), the `TransactionManagerStatus` is left in `InError` state but the actual database transaction may have been committed. Subsequent operations on the connection panic with "Transaction manager is in an error state" even though the connection is usable.

### N2: Fix `COPY FROM` sink not flushing partial buffer on early drop

In `diesel/src/pg/connection/copy.rs`, the `CopyFromSink` uses `Write` trait methods. If the sink is dropped without calling `finish()`, any buffered data in the underlying `RawConnection` is silently lost. The `Drop` impl should send the remaining buffer and signal end-of-copy to PostgreSQL.

### N3: Add `returning` clause support for `UPDATE` statements on SQLite

The `returning_clause.rs` in `diesel/src/sqlite/query_builder/` implements returning for INSERT but the `UpdateStatement` query fragment impl for SQLite doesn't include a path for the `RETURNING` clause, even though SQLite 3.35+ supports it.

### N4: Fix `debug_query` displaying incorrect bind parameter order for MySQL `LIMIT ... OFFSET`

In `diesel/src/mysql/query_builder/limit_offset.rs`, MySQL uses `LIMIT ?, ?` syntax. The `DebugQuery` output shows the offset and limit parameters reversed compared to what's actually sent to MySQL, because the bind collector visits them in declaration order rather than MySQL's expected order.

### N5: Add `CacheSize::Disabled` handling for the MySQL statement cache

The `CacheSize` enum in `connection/mod.rs` supports `Disabled` and `Fixed(usize)` variants. The SQLite and PostgreSQL backends respect `CacheSize::Disabled` by bypassing the statement cache, but the MySQL `statement_cache` implementation in `mysql/connection/stmt/mod.rs` always caches regardless of the setting.

### N6: Fix `FrameCollection::filter` in `diesel_cli` schema inference skipping composite foreign keys

In `diesel_cli/src/infer_schema_internals/foreign_keys.rs`, when inferring foreign key relationships that span multiple columns (composite keys), only the first column is captured. The `remove_unsafe_foreign_keys_for_codegen` function groups by constraint name but doesn't preserve multi-column mappings. Update `guide_drafts/migration_guide.md` to document composite foreign key handling.

### N7: Add `is not distinct from` expression support for PostgreSQL

PostgreSQL supports `IS NOT DISTINCT FROM` which treats NULL as a comparable value (unlike `=`). The expression module in `diesel/src/expression/operators.rs` defines `Eq` and `NotEq` but has no `IsNotDistinctFrom` operator. Add it as a PG-specific expression method.

### N8: Fix `MigrationHarness::pending_migrations` returning wrong order when applied table has gaps

In `diesel_migrations/src/migration_harness.rs`, `pending_migrations` filters source migrations against applied versions. When applied migrations have version gaps (e.g., hotfix applied out of order), the returned pending list doesn't sort properly, causing migrations to run in wrong order.

### N9: Add `truncate_table` DSL function for PostgreSQL and MySQL

The query builder supports `INSERT`, `UPDATE`, `DELETE`, and `SELECT` but not `TRUNCATE TABLE`. Add a `truncate` function in `diesel/src/query_builder/functions.rs` that generates `TRUNCATE TABLE <table>` with optional `CASCADE`/`RESTRICT` for PostgreSQL.

### N10: Fix `InferConnection::from_url` not respecting SSL parameters in PostgreSQL connection URLs

In `diesel_cli/src/database.rs`, `InferConnection::from_url` parses the database URL but strips query parameters like `sslmode=require` and `sslcert=...`. The CLI's schema inference connects without SSL even when the database requires it.

### N11: Fix `CHANGELOG.md` missing migration guidance section for breaking SQLite changes

The `CHANGELOG.md` documents breaking changes as bullet points but lacks structured migration guidance for SQLite-specific breaking changes (e.g., RETURNING clause behavior changes, WAL mode defaults). Add a "Migration Guide" subsection per release in `CHANGELOG.md`, cross-reference the corresponding `guide_drafts/migration_guide.md` content, and update `CONTRIBUTING.md` to require migration notes for any breaking change PR.

## Medium

### M1: Implement batch insert with automatic chunking for SQLite

SQLite has a limit on the number of bind parameters per statement (default 999). When inserting a large `Vec` of records, `insert_into(table).values(&records).execute(conn)` fails if `records.len() * columns > 999`. Implement automatic chunking in the SQLite `InsertStatement` that splits large inserts into multiple statements within a transaction.

### M2: Add query logging with execution timing to the Instrumentation system

The `InstrumentationEvent` enum in `connection/instrumentation.rs` tracks `StartEstablishConnection`, `FinishEstablishConnection`, `StartQuery`, `FinishQuery`, and `CacheQuery`. Extend it to include query execution duration measurement by capturing timestamps and adding a `QueryTiming` struct that's passed to `FinishQuery`. Update all three backend implementations.

### M3: Implement `LATERAL JOIN` support for PostgreSQL

PostgreSQL supports `LATERAL JOIN` which allows subqueries in `FROM` to reference columns from preceding tables. Add `lateral_join` and `lateral_left_join` methods to `QueryDsl`, implement the query fragment for PostgreSQL's `PgQueryBuilder`, and add the necessary type-level machinery in `query_builder/select_statement/`.

### M4: Add schema migration diffing to `diesel_cli`

Implement a `diesel migration diff` command in `diesel_cli` that compares the current database schema against the expected schema from migration files. Add a `diff_schema.rs` module that queries information_schema, compares against `print_schema` output, and reports missing tables, columns, indices, and type mismatches. Add CI workflow steps in `.github/workflows/ci.yml` to run migration diff checks, and update `CONTRIBUTING.md` with instructions for validating schema changes.

### M5: Implement connection health checking for the r2d2 pool integration

The `r2d2.rs` module provides basic `ManageConnection` implementation. Add configurable health check queries (not just `SELECT 1`) that verify the connection's transaction state is clean, the statement cache is valid, and the backend-specific settings (e.g., `search_path` for PostgreSQL) are correct. Support periodic health checks and connection age limits.

### M6: Add `INSERT ... ON CONFLICT` support for MySQL using `ON DUPLICATE KEY UPDATE`

The `upsert` module in `diesel/src/upsert/` implements PostgreSQL's `ON CONFLICT` and SQLite's `ON CONFLICT`. Add MySQL's equivalent `ON DUPLICATE KEY UPDATE` syntax. Implement the necessary query fragment traits in `mysql/query_builder/`, add the `on_duplicate_key_update` extension method, and handle MySQL's `VALUES()` function for referencing proposed values.

### M7: Implement compile-time query validation for `diesel_dynamic_schema`

The `diesel_dynamic_schema` crate allows runtime-defined tables and columns but provides no validation. Add an optional compile-time validator that checks dynamic schema definitions against a cached database schema snapshot. Store the schema snapshot in a JSON file, add a CLI command to refresh it, and validate at build time via a proc macro.

### M8: Add support for PostgreSQL advisory locks in the transaction manager

Implement `pg_advisory_lock`, `pg_try_advisory_lock`, and `pg_advisory_unlock` functions that integrate with `AnsiTransactionManager`. Ensure advisory locks are properly tracked and released on transaction rollback. Add them as methods on `PgConnection` and implement the corresponding SQL generation.

### M9: Implement typed PostgreSQL `COPY TO` with column selection

The existing `CopyToBuffer` in `pg/connection/copy.rs` reads raw bytes. Implement a typed `COPY TO` interface that deserializes rows into Rust structs using `Queryable` derivation. Support column selection (not just `*`), CSV and binary formats, and streaming iteration via the `Iterator` trait.

### M10: Add stored procedure and function call support

Diesel supports `sql_function!` for SQL functions in expressions but not `CALL` for stored procedures or standalone `SELECT function()`. Implement a `call_procedure` DSL function that generates `CALL proc_name(args)` for MySQL/PostgreSQL and handles output parameters. Add return type mapping for functions that return tables.

### M11: Add CI workflow for cross-database integration testing with Docker Compose

Create a unified CI pipeline using `docker-compose.yml` to spin up PostgreSQL, MySQL, and SQLite test environments. Extend `.github/workflows/ci.yml` with a matrix strategy that runs the full test suite against each backend. Update `.env.sample` with documented environment variables for each database, add a Docker-based development setup guide to `CONTRIBUTING.md`, and configure `docker/` with backend-specific initialization scripts.

## Wide

### W1: Implement async connection support across all backends

Add an async connection trait `AsyncConnection` alongside the existing sync `Connection` trait. Implement async versions of `PgConnection`, `MysqlConnection`, and `SqliteConnection` using tokio-postgres, mysql_async, and tokio-rusqlite respectively. Share query builder infrastructure between sync and async paths. Changes span `connection/`, all three backend modules, `r2d2.rs`, and `query_dsl/`.

### W2: Add database-level change tracking and event notification system

Implement a change tracking system that captures INSERT, UPDATE, DELETE operations and notifies subscribers. For PostgreSQL use `LISTEN/NOTIFY`, for MySQL use binlog-like polling, and for SQLite use `update_hook`. Add a `ChangeEvent` type, subscription API, and integrate with the connection lifecycle. Changes span all three backends, connection management, and add a new `events/` module.

### W3: Implement cross-database query federation

Add the ability to join queries across different database backends (e.g., PostgreSQL table joined with SQLite table). Implement a `FederatedConnection` that holds multiple backend connections, a query planner that splits cross-backend queries into backend-local subqueries, and a result merger. Changes span `connection/`, `query_builder/`, `query_dsl/`, and add a federation module.

### W4: Add automatic schema migration generation from Rust struct changes

Implement a `diesel migration generate-from-diff` command that compares Rust model structs (annotated with `#[derive(Queryable)]`) against the current schema and generates migration SQL. Detect added/removed columns, type changes, new tables, and index modifications. Changes span `diesel_cli`, `diesel_derives` (to extract struct metadata), `diesel_migrations`, and schema inference.

### W5: Implement query result caching with automatic invalidation

Add a query result cache layer that stores query results in memory and automatically invalidates them when the cached table is modified via Diesel. Track which tables are read by each cached query and which tables are written by mutations. Changes span `connection/`, `query_builder/` (mutation tracking), `query_dsl/` (cache API), and add a `cache/` module.

### W6: Add comprehensive database testing framework

Implement a `diesel::testing` module with test transaction isolation, automatic schema setup/teardown, fixture loading from TOML/JSON, factory pattern for generating test data, and database state snapshots for assertions. Support all three backends. Changes span `connection/`, `migration/`, and add a new `testing/` module with backend-specific implementations.

### W7: Implement row-level security policy management for PostgreSQL

Add support for defining and managing PostgreSQL Row Level Security (RLS) policies through Diesel. Implement `enable_rls`, `create_policy`, `alter_policy`, `drop_policy` DSL functions. Add a `CurrentUser` expression type that maps to `current_user`. Integrate with connection setup so policies are automatically applied. Changes span `pg/query_builder/`, `pg/connection/`, `expression/`, migrations, and CLI.

### W8: Add GraphQL-to-Diesel query translation layer

Implement a module that translates GraphQL queries into Diesel query builder calls. Parse GraphQL field selections into `select()` calls, filters into `filter()` chains, nested objects into `inner_join`/`left_join`, and pagination into `limit`/`offset`. Changes span `query_dsl/`, `query_builder/`, `expression/`, `associations/`, and add a `graphql/` translation module.

### W9: Implement multi-tenant data isolation at the ORM level

Add tenant-aware query building that automatically injects tenant ID filters on all queries and sets tenant ID on all inserts. Support PostgreSQL RLS integration, MySQL database-per-tenant, and SQLite file-per-tenant strategies. Changes span `query_builder/` (filter injection), `connection/` (tenant context), all three backends, `diesel_derives` (tenant-aware derives), and migrations.

### W10: Add database schema versioning and compatibility checking system

Implement a schema registry that tracks schema versions, validates application compatibility with the current database schema, and supports blue-green deployment scenarios. Add schema fingerprinting, compatibility matrices, and automatic migration path computation. Changes span `diesel_cli` (registry commands), `diesel_migrations` (version tracking), `connection/` (compatibility check on connect), and add a `schema_registry/` module.

### W11: Overhaul contributor documentation and developer guides

Restructure the project's non-code documentation: consolidate `guide_drafts/` drafts (README.md, backend_installation.md, custom_types.md, migration_guide.md, trait_derives.md) into published documentation linked from `README.md`; update `CONTRIBUTING.md` with build instructions for all three backends using `docker-compose.yml`; add architecture decision records in a new `docs/adr/` directory; update `code_of_conduct.md` to the latest Contributor Covenant version; and add a `RELEASING.md` with the release workflow referencing `.github/workflows/release.yml`. Changes span `guide_drafts/`, `CONTRIBUTING.md`, `README.md`, `code_of_conduct.md`, `docker-compose.yml`, and `.github/workflows/`.
