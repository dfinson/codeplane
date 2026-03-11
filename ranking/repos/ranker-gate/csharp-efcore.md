# dotnet/efcore

| Field | Value |
|-------|-------|
| **URL** | https://github.com/dotnet/efcore |
| **License** | MIT |
| **Language** | C# |
| **Scale** | Large (multi-team project) |
| **Category** | Object-relational mapper (ORM) |
| **Set** | ranker-gate |
| **Commit** | `45eaef017ea29a75046bfe15b76b4f2d155335c3` |

## Why this repo

- **No single developer knows it all**: Model building (conventions, annotations,
  fluent API), change tracking, query pipeline (LINQ translation → SQL
  generation), migrations, database providers (SQL Server, SQLite, PostgreSQL,
  Cosmos DB), scaffolding/reverse engineering — each a deep subsystem with
  substantial internal complexity.
- **Well-structured**: Clear project split — `EFCore/` (core abstractions),
  `EFCore.Relational/` (relational database layer), `EFCore.SqlServer/`,
  `EFCore.Sqlite/`, `EFCore.Cosmos/`, `EFCore.Design/` (migrations tooling).
  Query pipeline has clear phases (model, expression tree, SQL generation).
- **Rich history**: 15K+ commits, Microsoft-maintained with regular releases.
  Dense PR history covering performance work, query translation edge cases,
  provider-specific behavior, and API evolution.
- **Permissive**: MIT license.

## Structure overview

```
src/
├── EFCore/                          # Core abstractions
│   ├── DbContext.cs                 # Unit of work
│   ├── ChangeTracking/              # Entity state tracking
│   ├── Metadata/                    # Model metadata (entity types, properties)
│   │   ├── Builders/                # Fluent API model builders
│   │   └── Conventions/             # Convention-based configuration
│   ├── Query/                       # Query pipeline core
│   │   ├── Internal/                # Expression visitors, compilation
│   │   └── ResultOperators/         # LINQ operator translation
│   ├── Storage/                     # Value conversion, type mapping
│   └── Infrastructure/              # Service collection, DI
├── EFCore.Relational/               # Relational database layer
│   ├── Query/                       # SQL generation
│   │   ├── SqlExpressions/          # SQL expression tree
│   │   └── Internal/                # Query SQL generator
│   ├── Migrations/                  # Schema migration engine
│   ├── Storage/                     # Relational type mapping
│   └── Update/                      # Command batching
├── EFCore.SqlServer/                # SQL Server provider
├── EFCore.Sqlite/                   # SQLite provider
├── EFCore.Cosmos/                   # Cosmos DB provider
├── EFCore.Design/                   # Migrations tooling, scaffolding
└── EFCore.Proxies/                  # Lazy loading proxies
```

## Scale indicators

- ~3,000 C# source files
- ~500K+ lines of code
- Deep module hierarchies (5+ levels)
- Cross-cutting query pipeline, change tracking, and provider abstractions

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `Include()` generating duplicate JOINs with `AsSplitQuery()`

When using `Include()` with `AsSplitQuery()`, including the same
navigation property through two different paths generates duplicate
SQL queries for the included entity. The query splitting logic does
not deduplicate navigations that resolve to the same table. Fix the
split query generator to detect and merge duplicate includes.

### N2: Fix `DbContext.Find` not applying value converter during identity map lookup

When using `DbContext.Find<T>(id)` on an entity whose primary key
property has a `ValueConverter` (e.g., a strongly-typed ID wrapper
configured via `HasConversion`), the `Find` method in `DbContext.cs`
does not apply `ValueConverter.ConvertToProvider` on the passed key
value before performing the identity map lookup in
`EFCore/ChangeTracking/Internal/`. This causes already-tracked
entities to not be found, falling through to a database query that
applies the converter correctly on the SQL parameter but produces a
duplicate tracked entity on materialisation.

### N3: Fix `ExecuteUpdate` not translating `DateOnly` arithmetic

When using `ExecuteUpdate` with date arithmetic on `DateOnly`
properties (e.g., `.SetProperty(e => e.ExpiryDate, e => e.ExpiryDate.AddDays(30))`), the LINQ translator throws
"could not be translated" because `DateOnly.AddDays` is not
registered as a translatable method. Add SQL translation for
`DateOnly.AddDays`, `AddMonths`, and `AddYears` across all providers.

### N4: Fix `GroupBy` translation losing `HAVING` clause with owned types

When grouping by a property on an owned type and applying a `HAVING`
filter (e.g., `.GroupBy(o => o.Address.City).Where(g => g.Count() > 5)`),
the relational query SQL generator drops the `HAVING` clause because
the owned-type property reference is not correctly resolved during
SQL generation. Fix the `SqlExpression` visitor in
`EFCore.Relational/Query/Internal/` to propagate column bindings
through owned-type table expansions so `HAVING` is preserved.

### N5: Fix value converter not applied during `Contains()` translation

When an entity property has a value converter (e.g., enum stored as
string via `HasConversion<string>()`) and the query uses
`.Where(e => list.Contains(e.Status))`, the in-memory list values
are not passed through the converter before being sent as SQL
parameters. The generated `WHERE Status IN (...)` clause sends
integer enum values instead of strings. Fix the parameter binding
in `EFCore/Storage/` to apply the configured `ValueConverter` to
each element of the list.

### N6: Fix migration idempotency for index rename operations

Running `dotnet ef database update` twice when a migration renames
an index (via `RenameIndex`) fails on the second run because the
generated SQL does not include an existence check. The migration SQL
generator in `EFCore.Relational/Migrations/` should emit idempotent
SQL that checks whether the old index name still exists before
attempting the rename, consistent with how `RenameTable` and
`RenameColumn` already behave.

### N7: Fix `AsNoTracking` queries leaking `ChangeTracker` entries for owned types

When running an `AsNoTracking` query against an entity with owned
types, the top-level entity is correctly untracked but the owned
sub-entities are still added to the `ChangeTracker`. The issue is in
`EFCore/ChangeTracking/` where `InternalEntityEntry` instantiation
for owned types does not check the tracking flag propagated from the
parent materializer. Fix the entry factory so owned-type entries
respect the no-tracking flag.

### N8: Fix `string.Contains` generating case-sensitive comparison on SQLite

Using `.Where(e => e.Name.Contains("foo"))` generates
`instr("Name", 'foo') > 0` on SQLite, which is case-sensitive for
non-ASCII characters because SQLite's `instr()` function uses the default
byte-sequence collation. The SQLite method call translator in
`EFCore.Sqlite.Core/` should wrap both operands with `lower()` to produce
`instr(lower("Name"), lower('foo')) > 0` so that ASCII comparisons
match the case-insensitive semantics of `string.Contains` on other
providers.

### N9: Fix `decimal` precision loss in Cosmos DB provider

When storing a `decimal` property with precision configured via
`HasPrecision(18, 6)`, the Cosmos DB provider serialises the value
as a JSON `number`, which loses precision beyond 15–17 significant
digits. The serialiser in `EFCore.Cosmos/` should detect properties
with `HasPrecision` and serialise them as JSON strings, then
deserialise back to `decimal` on read, preserving full precision.

### N10: Fix `NavigationExpandingExpressionVisitor` stack overflow on deep self-referencing Include

Calling `.Include(e => e.Parent.Parent.Parent...)` on a
self-referencing entity (e.g., `Category` with `ParentCategory`)
more than ~15 levels deep causes a `StackOverflowException` in the
`NavigationExpandingExpressionVisitor` in `EFCore/Query/Internal/`.
Convert the recursive navigation expansion to an iterative loop with
an explicit stack to handle arbitrary Include depth without stack
overflow.

## Medium

### M1: Implement soft delete global query filter with cascade

Add a built-in soft delete pattern via `modelBuilder.Entity<T>().HasSoftDelete()`. This should: add a `DeletedAt` shadow property,
configure a global query filter excluding deleted entities, override
`SaveChanges` to set `DeletedAt` instead of deleting, and handle
cascade soft delete for dependent entities. Add `IgnoreSoftDelete()`
for queries that need to see deleted entities.

### M2: Add query result caching with invalidation

Implement a query cache that stores materialized query results and
invalidates them when the underlying tables change. Add
`.TagWith("cache", duration)` for explicit caching and an
auto-invalidation system that tracks which tables each cached query
reads from. Support cache backends: in-memory (default) and
distributed (via `IDistributedCache`). Add cache hit/miss metrics.

### M3: Add configurable retry semantics for `ExecuteUpdate` and `ExecuteDelete`

`ExecuteUpdate` and `ExecuteDelete` in `EFCore.Relational/Query/`
already route through the `IExecutionStrategy` for transient fault
retry, but there are no dedicated diagnostic events for bulk
operation retries. When a transient error triggers a retry, only
the general `ExecutionStrategyRetrying` event fires without
identifying the bulk operation type. Add dedicated
`ExecuteDeleteRetrying` and `ExecuteUpdateRetrying` event IDs to
`RelationalEventId`, create a `BulkOperationRetryEventData` payload
class in `EFCore.Relational/Diagnostics/`, and add logger extension
methods to `RelationalLoggerExtensions` that emit these events.
Also add a "Resiliency and Retry" section to `README.md` documenting
the retry configuration settings, and update
`docs/getting-and-building-the-code.md` with instructions for
testing retry behaviour locally using transient-fault injection.

### M4: Add bulk operations support

Implement `BulkInsert()`, `BulkUpdate()`, and `BulkDelete()` methods
that use database-specific bulk loading mechanisms (SQL Server
`BULK INSERT`, SQLite batch insert via parameterised multi-row `INSERT`).
Bypass the change tracker for performance. Support configurable batch
sizes and progress callbacks. Maintain foreign key and index constraint
validation.

### M5: Add interceptor pipeline for raw SQL generation

Extend the interceptor framework so that users can inspect and
modify the SQL string after it is generated but before it is
executed. Add `ISqlGeneratingInterceptor` with `SqlGenerating` and
`SqlGenerated` hooks in `EFCore.Relational/`. Provide the
`SelectExpression` tree and the generated SQL text. Support
rewriting SQL (e.g., adding query hints), logging, and vetoing
execution. Wire the interceptor into the existing
`IRelationalCommandBuilder` pipeline.

### M6: Implement compiled model improvements for large models

For models with 500+ entity types, startup is slow because the
model is built at runtime. Extend the compiled-model system in
`EFCore/Infrastructure/` and `EFCore.Design/` to pre-generate
source for value converters, query filters, custom conventions, and
navigation fixup delegates. Support incremental compilation so only
changed entities are regenerated. Add AOT-friendly source generators
that replace the reflection-based metadata discovery.

### M7: Implement cross-database provider query translation fallback

When a LINQ method has no provider-specific SQL translation (e.g.,
`string.Compare` on SQLite), the query currently fails with
"could not be translated." Add a fallback evaluation mode in
`EFCore.Relational/Query/` that detects untranslatable sub-expressions,
splits them into a server-side query plus a client-side projection,
and logs a warning. Provide an opt-in `EnablePartialClientEvaluation()`
API and instrument it with diagnostics so users can identify and fix
performance bottlenecks.

### M8: Add cascade `ExecuteDelete` that respects navigation relationships

`ExecuteDelete()` operates on a single entity type and does not
cascade to dependent entities, leaving orphaned rows that violate
foreign key constraints. Add `ExecuteDeleteCascading()` in
`EFCore.Relational/Query/` that analyses the entity's navigation
metadata from `EFCore/Metadata/` to discover required dependents,
generates `DELETE` statements in dependency order (leaf entities
first), and wraps the multi-table delete in a transaction. Support
self-referencing entities by detecting cycles via the navigation
graph. Wire through the
`RelationalQueryableMethodTranslatingExpressionVisitor` for SQL
generation. Add provider-specific optimisation in
`EFCore.SqlServer/` using `DELETE ... FROM ... JOIN` syntax.

### M9: Add connection resiliency with automatic transaction replay

Extend the execution strategy in `EFCore/Storage/` so that when a
transient error occurs mid-transaction, the strategy can
automatically replay the entire transaction from a captured operation
log. Record each `ExecuteNonQuery` / `ExecuteScalar` call within the
transaction scope. On transient failure, open a new connection and
replay the log. Validate idempotency by comparing row counts. Add
`RetryOnTransientError()` configuration and diagnostic events for
each replay attempt.

### M10: Implement automatic migration diffing from live database

Add a `dotnet ef migrations diff` command in `EFCore.Design/` that
compares the current compiled model against a live database schema
(rather than the model snapshot). Reverse-engineer the database
schema, diff it against the code model, and generate a migration
containing only the delta. Support diffing across providers. Handle
drift detection for indexes, constraints, sequences, and computed
columns.

## Wide

### W1: Implement database-first code generation improvements

Overhaul the scaffolding/reverse engineering system. Add: preserved
custom code in regenerated files (partial classes), mapping comments
from database metadata to XML doc comments, stored procedure and
function scaffolding, view scaffolding with auto-detected key
properties, and incremental scaffolding that updates only changed
entities. Add T4 template support for custom code generation.
Update `Directory.Build.targets` to include a new `EFScaffold`
MSBuild target that runs the scaffold command as a post-build step,
and add a `docs/scaffolding.md` guide documenting the new code
generation workflow and template customisation options.

### W2: Add multi-tenancy support at the EF Core level

Implement native multi-tenancy patterns: schema-per-tenant
(PostgreSQL `SET search_path`), database-per-tenant (connection
string routing), and discriminator-per-tenant (global query filters).
Add `ITenantProvider` interface for tenant resolution. Support
tenant-aware migrations that apply schema changes across all tenant
databases. Add admin queries that span tenants.

### W3: Implement real-time change notifications

Add database change notification support that pushes entity changes
to subscribers in real-time. Use database-specific change tracking
mechanisms (SQL Server `SqlDependency`, SQLite polling). Surface
changes through an `IObservable<EntityChange<T>>`
API. Support filtering by entity type and property. Integrate with
SignalR for browser push. Handle reconnection and missed event
recovery.

### W4: Implement read-replica routing and CQRS context splitting

Add contextual read-replica support so read queries are automatically
routed to a replica connection. Implement `UseReadReplica(connStr)`
on `DbContextOptionsBuilder`. Add `IConnectionRouter` in
`EFCore/Infrastructure/` to select connections based on query type.
Detect read-vs-write at the `IQueryable` level. Support sticky reads
after writes within a scope. Implement health-check failover back to
the primary. Integrate with the execution strategy for retry on
replica lag. Add diagnostics for routing decisions.

### W5: Implement database sharding support

Add horizontal sharding primitives to EF Core. Implement a
`IShardingStrategy` interface with `HashSharding`, `RangeSharding`,
and `DirectorySharding` built-in strategies in `EFCore/Infrastructure/`.
Route queries and writes to the correct shard based on a shard key
property. Support cross-shard queries via scatter-gather with
client-side merge. Handle shard-aware migrations that apply schema
changes to all shards. Add shard rebalancing tooling and diagnostics.

### W6: Overhaul change tracking to support graph-based merge

Re-architect the change tracker in `EFCore/ChangeTracking/` to
support merging detached entity graphs. Implement
`DbContext.MergeGraph(entity)` that walks a detached entity graph,
compares it against tracked state, and produces minimal
`INSERT`/`UPDATE`/`DELETE` operations. Handle cycles in navigation
graphs using reference equality. Support configurable merge
strategies (server-wins, client-wins, property-level merge).
Integrate with optimistic concurrency to detect conflicts during
merge. Add diagnostic events for each resolved conflict.

### W7: Implement full-text search abstraction across providers

Add a provider-agnostic full-text search API. Implement
`EF.Functions.FullTextSearch(property, query)` that translates to
`CONTAINS()`/`FREETEXT()` on SQL Server (building on the existing
`HasFullTextIndex()` in `EFCore.SqlServer/Extensions/`) and FTS5 on
SQLite. Support ranked results via `EF.Functions.FullTextRank()`. Add
a relational-level `HasFullTextIndex()` abstraction in
`EFCore.Relational/` so providers can declare full-text index support.
Generate appropriate migration operations to create and drop full-text
indexes on each provider. Support multi-column indexes and language
configuration.

### W8: Add query plan analysis and performance advisor

Build a query analysis pipeline. Add `ToQueryPlan()` that captures
the SQL, estimated cost, and index usage from the database's
`EXPLAIN` output. Implement an `IQueryAdvisor` in
`EFCore.Relational/Query/` that inspects expression trees before
execution and warns about N+1 patterns, missing indexes, cartesian
explosions from multiple collection includes, and unbounded result
sets. Surface recommendations through `DbContext.QueryAdvice` and
the logging pipeline. Add an opt-in mode that collects actual
execution statistics per query.

### W9: Implement entity versioning and audit trail system

Add a built-in audit system. Implement `modelBuilder.Entity<T>().IsAudited()`
that creates a shadow `_Audit` table mirroring the entity schema.
On every `INSERT`, `UPDATE`, or `DELETE`, capture the old and new
values, the timestamp, the user identity (via `IAuditUserProvider`),
and the operation type. Store change sets efficiently using JSON diff
for updates. Add `context.AuditLog<T>().AsOf(date)` and
`.Changes(entityId)` query APIs. Support audit table migrations that
stay in sync. Integrate with temporal tables on SQL Server.

### W10: Implement distributed transaction coordinator across providers

Add a two-phase commit coordinator for operations that span multiple
`DbContext` instances or even multiple providers. Implement
`IDistributedTransactionManager` in `EFCore/Infrastructure/` with
prepare/commit/rollback phases. Support `TransactionScope`
integration and a standalone API. Handle heterogeneous transactions
(e.g., SQL Server + PostgreSQL). Add a recovery log that persists
in-doubt transactions. Implement timeout-based resolution and manual
intervention tooling. Surface diagnostic events for each phase.

### N11: Fix `Directory.Packages.props` not pinning transitive `System.Text.Json` version

The central package management file `Directory.Packages.props` sets
`CentralPackageTransitivePinningEnabled` to `true`, but the
`System.Text.Json` entry uses a variable reference
`$(SystemTextJsonVersion)` resolved from `eng/Versions.props`. When
building out-of-tree (e.g., specification tests run in isolation),
`eng/Versions.props` is not imported and the version resolves to
empty, causing NuGet restore to silently pick the lowest compatible
version. Fix the conditional import guard in
`Directory.Packages.props` so that a fallback version is always
defined, and add a `<When>` block to `Directory.Build.props` that
emits a build error when the resolved `System.Text.Json` version
is below the minimum supported version.

### M11: Add a CI matrix for provider-specific test sharding in `azure-pipelines.yml`

The current `azure-pipelines.yml` runs all provider tests
(SQL Server, SQLite, Cosmos) sequentially in one stage, producing
long build times and opaque failures. Refactor the pipeline into a
multi-stage matrix: add separate `SqlServer`, `Sqlite`, and
`Cosmos` stages that each filter the test projects via
`--filter Category=<provider>`. Add a final `Merge` stage that
collects results. Update `NuGet.config` to add a dedicated
`dotnet-testing` feed for test infrastructure packages. Add a
`docs/ci-pipeline.md` guide that documents the matrix layout,
explains how to add a new provider stage, and describes the
test-category tagging convention. Update the `eng/Versions.props`
`<PreReleaseVersionLabel>` to include a short commit hash so
artifact version is traceable from the pipeline.

### W11: Overhaul build infrastructure for multi-TFM and AOT readiness

Modernise the repo build infrastructure across multiple non-code
files. In `Directory.Build.props`, add a
`<DefaultTargetFrameworks>` property that centralises the TFM list
(`net10.0;net9.0;net8.0`) and conditional properties for AOT and
trimming analysis, replacing the per-project duplication. In
`Directory.Build.targets`, add a `ValidateBuildEnvironment`
target that checks the installed SDK version against
`global.json` and emits a human-readable error when the SDK is
too old. In `Directory.Packages.props`, add `<PackageVersion>`
entries for the three new AOT analyzer packages and remove
the obsolete `DotNetAnalyzers.DocumentationAnalyzers` reference
that is already superseded by the Roslyn built-in analyzers in
.NET 9+. Update `NuGet.config` to remove the deprecated
`dotnet8-transport` and `dotnet9-transport` feeds since those SDK
versions are out of support. Update `eng/Versions.props` to add
a `<MinimumSdkVersion>` property and wire it into the validation
target. Add a new `docs/build-infrastructure.md` that documents
the purpose of every `Directory.Build.*` and `eng/*.props` file,
the central package management strategy, and the expected CI
contract for contributors. Finally, update `README.md` to replace
the stale "Daily builds" section with a link to the new
build-infrastructure guide.
