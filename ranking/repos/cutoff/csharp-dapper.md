# DapperLib/Dapper

| Field | Value |
|-------|-------|
| **URL** | https://github.com/DapperLib/Dapper |
| **License** | Apache-2.0 |
| **Language** | C# |
| **Scale** | Small (focused micro-ORM) |
| **Category** | Data access / ORM |
| **Set** | Cutoff |
| **Commit** | `288730e69b05c32cac898d9b55ebea219ea8a2d1` |

## Why this repo

- **Extremely focused**: A single-purpose micro-ORM with ~15 source files.
  The entire public API surface lives in `SqlMapper.cs` and its partial-class
  companions. Navigation requires understanding partial classes, extension
  methods, and generic type resolution in a compact codebase.
- **Well-structured**: Core code under `Dapper/` with `SqlMapper.cs` (Query,
  Execute, QueryFirst, QueryMultiple), `DynamicParameters.cs`,
  `CommandDefinition.cs`, `SqlMapper.GridReader.cs` (multi-result sets),
  `SqlMapper.TypeHandler.cs` (custom type mapping), `DefaultTypeMap.cs`,
  and `CustomPropertyTypeMap.cs`.
- **Rich history**: 17K+ stars, widely adopted as the go-to lightweight
  alternative to Entity Framework. Issues span performance, type mapping
  edge cases, and ADO.NET provider compatibility.
- **Permissive**: Apache-2.0 license.

## Structure overview

```
Dapper/
├── SqlMapper.cs                  # Core — Query, Execute, QueryFirst,
│                                 #   QuerySingle, QueryFirstOrDefault
├── SqlMapper.Async.cs            # Async overloads of all query/execute methods
├── SqlMapper.GridReader.cs       # Multi-result set reading (QueryMultiple)
├── SqlMapper.TypeHandler.cs      # Custom type handler registration/resolution
├── SqlMapper.TypeDeserializerCache.cs  # IL-emitted deserializer caching
├── SqlMapper.Identity.cs         # Cache key for query identity
├── SqlMapper.Settings.cs         # Global configuration settings
├── SqlMapper.CacheInfo.cs        # Per-query compiled cache entry
├── DynamicParameters.cs          # Dynamic parameter bag for queries
├── CommandDefinition.cs          # Encapsulates command text, params, timeout
├── DefaultTypeMap.cs             # Default property-to-column mapping
├── CustomPropertyTypeMap.cs      # Custom property-to-column mapping
├── SimpleMemberMap.cs            # Single property/field member mapping
├── WrappedReader.cs              # IDataReader wrapper for grid reading
├── WrappedDataReader.cs          # Disposable data reader wrapper
├── DbString.cs                   # String parameter with length/ANSI control
├── DataTableHandler.cs           # DataTable parameter support
├── SqlDataRecordHandler.cs       # SqlDataRecord TVP support
├── TableValuedParameter.cs       # Table-valued parameter wrapper
├── FeatureSupport.cs             # Provider feature detection
└── XmlHandlers.cs                # XML type handlers
```

## Scale indicators

- ~15–20 C# source files
- ~6K lines of code
- Flat structure (single directory, partial classes)
- Zero external dependencies beyond ADO.NET

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix Query<T> not disposing IDataReader on mapping exception

When `Query<T>` maps a row and the user-supplied type's constructor
throws, the `IDataReader` is not disposed because the exception
bypasses the `finally` block in the yield-based iterator. Fix the
iterator implementation in `SqlMapper.cs` to ensure the reader is
disposed even when a mapping exception occurs mid-enumeration.

### N2: Fix DynamicParameters.Get<T> throwing deprecated exception and failing numeric casts

`DynamicParameters.Get<T>()` in `DynamicParameters.cs` throws
`ApplicationException` (deprecated since .NET 2.0) when the output
parameter value cannot be cast, and uses a direct `(T)val` cast that
throws `InvalidCastException` for compatible-but-different numeric
types (e.g., retrieving a `long` output parameter as `int`). Fix
`DynamicParameters.Get<T>()` to throw `InvalidCastException` with a
descriptive message, and use `Convert.ChangeType` when `T` implements
`IConvertible` so compatible numeric conversions succeed.

### N3: Fix Execute deadlock when CommandFlags.Pipelined is used on sync path

`ExecuteImpl` in `SqlMapper.cs` calls `.Result` on
`ExecuteMultiImplAsync` when `CommandFlags.Pipelined` is set and the
call comes from the synchronous `Execute()` extension method. In
ASP.NET or any context with a single-threaded synchronization context,
this `.Result` call causes a deadlock because the async continuation
cannot resume. Fix `SqlMapper.cs` to detect this unsupported
combination and throw `InvalidOperationException` directing callers to
use `ExecuteAsync()` instead.

### N4: Fix DefaultTypeMap.FindConstructor using positional rather than name-based matching

`DefaultTypeMap.FindConstructor` in `DefaultTypeMap.cs` matches
constructor parameters to query columns by position (array index) rather
than name. When query columns appear in a different order than the
constructor parameters, the match fails or binds the wrong values. Fix
`DefaultTypeMap.cs` so `FindConstructor` matches each constructor
parameter to its corresponding column by name (case-insensitive),
regardless of column order.

### N5: Fix GridReader.ReadAsync<T> with buffered:false executing synchronously

`GridReader.ReadAsync<T>(buffered: false)` in
`SqlMapper.GridReader.Async.cs` returns `Task.FromResult(ReadDeferred<T>(...))`,
wrapping a synchronous iterator in an already-completed task. This
causes `reader.NextResult()` to be called synchronously via
`OnAfterGrid` rather than via the truly-async `OnAfterGridAsync` path,
blocking the calling thread on database I/O. Fix
`SqlMapper.GridReader.Async.cs` so `ReadAsync<T>(buffered: false)`
delegates to `ReadUnbufferedAsync<T>()`, which already uses
`ReadAsync` and `OnAfterGridAsync`.

### N6: Fix SqlMapper.TypeHandler not clearing cache on handler registration

Registering a new `TypeHandler<T>` via `SqlMapper.AddTypeHandler<T>()`
does not invalidate the `TypeDeserializerCache`, so previously compiled
deserializers continue using the old handler. Fix the handler
registration in `SqlMapper.cs` to purge the relevant
cache entries in `SqlMapper.TypeDeserializerCache.cs`.

### N7: Fix DbString defaulting to ANSI for Unicode connection strings

`DbString` defaults `IsAnsi = false`, but when the connection string
specifies an ANSI-only provider, the parameter is still sent as
`DbType.String` (Unicode) causing implicit conversion. Fix `DbString`
to detect the provider and set `IsAnsi` appropriately based on the
connection's provider factory.

### N8: Document SqlMapper.Identity.Equals hash/equals contract invariant for gridIndex and parametersType

`SqlMapper.Identity.GetHashCode` in `SqlMapper.Identity.cs` includes
`gridIndex` and `parametersType` in the hash computation, and
`SqlMapper.Identity.Equals` correctly compares these fields, but
this critical invariant is not documented in the code. Add an explicit
comment to the `Equals` method in `SqlMapper.Identity.cs` documenting
that both `gridIndex` and `parametersType` must be compared to maintain
the hash/equals contract, preventing future maintainers from
accidentally removing either comparison and causing incorrect cache
hits where a deserializer compiled for one grid position or parameter
set would be returned for a different one.

### N9: Fix Execute returning -1 for stored procedures with no SET NOCOUNT

`Execute()` returns `-1` for stored procedure calls when the procedure
does not set `NOCOUNT ON`, because the intermediate row counts from
DML statements inside the procedure confuse the result aggregation.
Fix `SqlMapper.cs` to aggregate all `RecordsAffected` values from the
command execution for stored-procedure commands.

### N10: Fix DbWrappedReader.Close not disposing the underlying command

`DbWrappedReader` in `WrappedReader.cs` is documented as allowing
"closing a reader to also close the command", but its `Close()` method
only calls `_reader.Close()` without disposing `_cmd`. The `Dispose(bool)`
overload correctly disposes both, but `Close()` is the path taken when
query code finishes reading rows. Fix `WrappedReader.cs` so `Close()`
also disposes `_cmd`, matching the documented purpose and the behavior
of `Dispose(bool)`.

### N11: Update Dapper.csproj target frameworks and Directory.Build.props packaging metadata

The `Dapper.csproj` still targets `net461` and `netstandard2.0`
alongside `net8.0`, but the `Directory.Build.props` sets
`CheckEolTargetFramework` to `false` to suppress end-of-life
warnings. Add `net9.0` to the `TargetFrameworks` list in
`Dapper.csproj`, update `Directory.Packages.props` to pin the
new `Microsoft.Bcl.AsyncInterfaces` version for `netstandard2.0`,
and update the `PackageReleaseNotes` URL in `Directory.Build.props`
to point to the latest release page. Also update `global.json` to
set the minimum SDK version to `9.0.100` with `rollForward` set
to `latestFeature`.

## Medium

### M1: Add QueryUnbufferedAsync streaming overloads for multi-mapping queries

The existing `QueryUnbufferedAsync<T>()` in `SqlMapper.Async.cs`
returns `IAsyncEnumerable<T>` for single-type queries, but there are
no unbuffered async overloads for multi-mapping. All multi-mapping
async methods (`QueryAsync<TFirst, TSecond, TReturn>()` and siblings)
always buffer the entire result set. Implement
`QueryUnbufferedAsync<TFirst, TSecond, TReturn>()` and
`QueryUnbufferedAsync<TFirst, TSecond, TThird, TReturn>()` overloads
in `SqlMapper.Async.cs` that return `IAsyncEnumerable<TReturn>`,
support cancellation via `CancellationToken`, respect the `splitOn`
parameter, and properly dispose the reader on completion or
cancellation. Share the split-column resolution logic with the
existing `MultiMapAsync` helper in `SqlMapper.Async.cs` and the
`GetMultiSplitPoint` helper in `SqlMapper.cs`.

### M2: Implement multi-mapping with custom split logic

Extend `Query<TFirst, TSecond, TReturn>()` to accept an optional
`Func<IDataRecord, int>` split function instead of the string-based
`splitOn` parameter. The split function determines where each type's
columns begin. Requires changes to `SqlMapper.cs` for the new overloads,
`SqlMapper.TypeDeserializerCache.cs` for dynamic split boundaries,
and `SqlMapper.Identity.cs` for cache key differentiation.

### M3: Add interceptor pipeline for command execution

Implement an `ICommandInterceptor` interface with `OnExecuting()`,
`OnExecuted()`, and `OnError()` hooks. Register interceptors globally
via `SqlMapper.Settings` or per-command via `CommandDefinition`.
Interceptors receive the `IDbCommand`, timing info, and exception
details. Changes span `SqlMapper.cs`, `SqlMapper.Async.cs`,
`SqlMapper.Settings.cs`, and `CommandDefinition.cs`.

### M4: Implement automatic TypeHandler discovery via attributes

Add a `[TypeHandler(typeof(MyHandler))]` attribute that can be placed
on a type to associate it with a custom handler. On first use,
`SqlMapper` should scan for the attribute-based handlers and register
them automatically. Requires a new attribute class, changes to
`SqlMapper.TypeHandler.cs` for discovery logic, and cache invalidation
in `SqlMapper.TypeDeserializerCache.cs`.

### M5: Add bulk insert support via SqlMapper.BulkInsert<T>

Implement `SqlMapper.BulkInsert<T>(IDbConnection, IEnumerable<T>)` that
uses `SqlBulkCopy` for SQL Server and batched `INSERT` statements for
other providers. Support column mapping from property names, custom
batch sizes, and a `CancellationToken`. Changes span `SqlMapper.cs`
for the public API, a new `BulkInsertHandler.cs` for provider-specific
logic, and `DefaultTypeMap.cs` for column mapping. Also update
`Dapper.csproj` to add a conditional `PackageReference` for
`Microsoft.Data.SqlClient` under the bulk-insert feature flag, and
add the new public API surface to `PublicAPI.Unshipped.txt`.

### M6: Implement query result caching with TTL support

Add `SqlMapper.QueryCached<T>()` that caches deserialized results by
query text and parameters with a configurable TTL. Support cache
invalidation by table name or query pattern. Requires a new
`QueryCache.cs` class, integration with `SqlMapper.cs` and
`SqlMapper.Async.cs`, and changes to `SqlMapper.Identity.cs` for
cache key computation that includes parameter values.

### M7: Add constructor-based mapping support to DefaultTypeMap

Extend `DefaultTypeMap` to support mapping columns to constructor
parameters by name, enabling immutable record/class hydration. When a
type has a parameterized constructor, match columns to constructor
parameters first, then set remaining properties. Changes span
`DefaultTypeMap.cs`, `SqlMapper.TypeDeserializerCache.cs` for the
IL-emit logic, and `SqlMapper.cs` for fallback behavior.

### M8: Implement connection retry policy for transient failures

Add a `RetryPolicy` class and integrate it with `SqlMapper.Execute()`
and `SqlMapper.Query<T>()` to automatically retry on transient database
errors (deadlocks, timeouts, connection drops). Support configurable
retry counts, backoff strategies, and exception filters. Changes span
`SqlMapper.cs`, `SqlMapper.Async.cs`, `CommandDefinition.cs` for
policy attachment, and a new `RetryPolicy.cs`.

### M9: Add JSON column support with automatic serialization

Implement `JsonTypeHandler<T>` that automatically serializes/deserializes
objects to JSON columns. Register it globally or per-property via a
`[JsonColumn]` attribute. Support both `System.Text.Json` and
`Newtonsoft.Json` via a pluggable serializer interface. Changes span
a new `JsonTypeHandler.cs`, `SqlMapper.TypeHandler.cs` for the
registration, and `DefaultTypeMap.cs` for attribute detection.

### M10: Implement parameter sniffing mitigation via query hints

Add `CommandDefinition.WithRecompile()` and
`CommandDefinition.WithOptimizeForUnknown()` that append or inject
query hints for SQL Server. Support provider-specific hint strategies.
Changes span `CommandDefinition.cs` for the builder methods,
`SqlMapper.cs` for hint injection during command preparation, and
`FeatureSupport.cs` for provider detection.

### M11: Add GitHub Actions CI matrix and update AppVeyor configuration

The CI configuration in `appveyor.yml` runs tests only on Windows with
local database instances. The `.github/workflows/main.yml` workflow
runs on Ubuntu but lacks multi-database version testing. Add a test
matrix to `.github/workflows/main.yml` that runs against PostgreSQL 15
and 16, MySQL 8.0 and 8.4, and SQL Server 2019 and 2022 using service
containers. Update `appveyor.yml` to add a `test_script` section that
runs `dotnet test` with `--logger trx` for structured test output.
Update the `nuget.config` to add the `dotnet-tools` feed for CI tool
restoration.

## Wide

### W1: Implement full async pipeline with IAsyncEnumerable throughout

Rewrite the async execution pipeline to use `IAsyncEnumerable<T>`
natively, replacing the buffered `Task<IEnumerable<T>>` pattern.
Support `await foreach` with `ConfigureAwait`, cancellation at every
yield point, and `IAsyncDisposable` for `GridReader`. Requires
rewriting `SqlMapper.Async.cs`, updating `SqlMapper.GridReader.cs`
for async enumeration, modifying `CommandDefinition.cs` for async
flags, updating `WrappedReader.cs` for `IAsyncDisposable`, and
adding async versions of `TypeDeserializerCache` methods.

### W2: Add multi-database provider abstraction layer

Abstract provider-specific behaviors (parameter prefix, paging syntax,
identity retrieval, bulk operations, TVP support) into an
`IDatabaseProvider` interface with implementations for SQL Server,
PostgreSQL, MySQL, SQLite, and Oracle. Replace scattered provider
checks throughout `SqlMapper.cs`, `FeatureSupport.cs`,
`TableValuedParameter.cs`, `SqlDataRecordHandler.cs`, and
`DynamicParameters.cs` with the unified provider abstraction.

### W3: Implement compile-time source-generated mappers

Add a Roslyn source generator that produces AOT-friendly mappers for
`[DapperAot]`-annotated types, eliminating runtime IL emission.
The generator must handle constructor binding, property mapping,
custom type handlers, nullable reference types, and records. Requires
a new `Dapper.AOT/` project with the source generator, integration
with `SqlMapper.TypeDeserializerCache.cs` to prefer generated mappers,
and updates to `DefaultTypeMap.cs` and `SqlMapper.cs` for fallback.
Also add a new `Dapper.AOT.csproj` project file with analyzer
packaging configuration, update `Dapper.sln` to include the new
project, add shared build properties in `Directory.Build.props` for
the source generator output, and register the new package version in
`version.json`.

### W4: Add comprehensive diagnostic and tracing system

Integrate `System.Diagnostics.Activity` and `DiagnosticSource`
throughout Dapper: emit events for command execution, parameter
binding, result deserialization, cache hits/misses, and type handler
resolution. Add `SqlMapper.Diagnostics` for programmatic subscription.
Changes span every `SqlMapper*.cs` partial, `CommandDefinition.cs`,
`DynamicParameters.cs`, and `SqlMapper.TypeDeserializerCache.cs`.

### W5: Implement relationship mapping with navigation properties

Add `Query<TParent>.Include<TChild>(expression)` syntax for loading
related entities via foreign-key relationships. Support one-to-many,
many-to-one, and many-to-many with configurable join strategies
(single query with split, or N+1 lazy loading). Requires a new
relationship metadata model, changes to `SqlMapper.cs` for the
fluent API, `DefaultTypeMap.cs` for navigation property detection,
`SqlMapper.GridReader.cs` for multi-result assembly, and new IL-emit
logic in `SqlMapper.TypeDeserializerCache.cs`.

### W6: Add schema migration and type validation system

Implement `SqlMapper.ValidateSchema<T>(IDbConnection)` that compares
the runtime type map against the actual database schema, reporting
missing columns, type mismatches, and unmapped properties. Add
`SqlMapper.GenerateMigration<T>()` for ALTER TABLE script generation.
Changes span `DefaultTypeMap.cs`, `CustomPropertyTypeMap.cs`,
`SqlMapper.TypeHandler.cs` for type compatibility checks, and new
schema inspection classes that query `INFORMATION_SCHEMA` or
provider-specific metadata.

### W7: Implement expression-based query builder integration

Add a lightweight LINQ-like query builder:
`SqlMapper.From<T>().Where(x => x.Age > 18).Select(x => new { x.Name })
.ToQuery()` that generates parameterized SQL. Support projections,
filtering, ordering, paging, and joins. Integrate the generated
`CommandDefinition` with existing `Query<T>()` methods. Changes span
new builder classes, expression visitor for SQL generation,
`CommandDefinition.cs`, `DynamicParameters.cs` for parameter extraction,
and `DefaultTypeMap.cs` for table/column naming conventions.

### W8: Add connection pooling and management layer

Implement a `DapperConnectionPool` that wraps raw `IDbConnection`
instances with connection lifetime management, health checking, warm-up,
and per-tenant isolation. Add connection string routing for
read/write splitting. Integrate pool metrics with the diagnostic
system. Changes span a new `ConnectionPool/` directory, modifications
to all `SqlMapper.cs` entry points to optionally accept pooled
connections, `SqlMapper.Settings.cs` for pool configuration, and
`SqlMapper.Async.cs` for async connection acquisition.

### W9: Implement distributed caching layer with cache invalidation

Add `SqlMapper.QueryDistributedCached<T>()` that integrates with
`IDistributedCache` (Redis, Memcached) for cross-process query result
caching. Support serialization via `MessagePack` or `System.Text.Json`,
cache tags for group invalidation, cache-aside pattern with stampede
protection, and async refresh. Changes span new cache infrastructure
classes, integration with `SqlMapper.cs` and `SqlMapper.Async.cs`,
`SqlMapper.Identity.cs` for distributed cache keys, and
`CommandDefinition.cs` for cache policy attachment.

### W10: Add full stored-procedure metadata introspection and code generation

Implement `SqlMapper.DiscoverProcedures(IDbConnection)` that reads
stored procedure metadata (parameters, result sets, return types) from
the database and generates strongly-typed wrapper methods at runtime
via IL emission or as source files. Support SQL Server, PostgreSQL,
and Oracle. Changes span a new `Procedures/` directory, metadata
readers per provider, IL-emit or T4 code generation, integration with
`SqlMapper.cs` for registration, `DynamicParameters.cs` for parameter
validation, and `FeatureSupport.cs` for provider capabilities.

### W11: Overhaul documentation site and build automation

The `docs/` directory contains a `docs.csproj`, `index.md`, and
sponsor-related markdown, but the documentation site lacks API
reference generation, versioned docs, and search. Add a DocFX
configuration (`docs/docfx.json`) that generates API docs from the
`Dapper.csproj` XML documentation comments, and update `docs/index.md`
with a getting-started guide and migration notes. Update
`Dapper.sln` to include the docs project in a `Solution Items`
folder, and add a `docs` build step to `.github/workflows/main.yml`
that runs `docfx build` and deploys to GitHub Pages.
