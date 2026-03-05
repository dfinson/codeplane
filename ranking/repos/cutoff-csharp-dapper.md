# DapperLib/Dapper

| Field | Value |
|-------|-------|
| **URL** | https://github.com/DapperLib/Dapper |
| **License** | Apache-2.0 |
| **Language** | C# |
| **Scale** | Small (focused micro-ORM) |
| **Category** | Data access / ORM |
| **Set** | Cutoff |

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

### N2: Fix DynamicParameters ignoring DbType for output parameters

When adding an output parameter via `DynamicParameters.Add()` with an
explicit `DbType`, the type is silently ignored during command
preparation if the parameter value is `null`. Fix the parameter setup
loop in `DynamicParameters.cs` to apply the specified `DbType`
regardless of whether a value is provided.

### N3: Fix CommandDefinition.Flags not propagating to async methods

`CommandDefinition` accepts a `Flags` property (e.g., `Buffered`,
`Pipelined`) but the async execution path in `SqlMapper.Async.cs`
ignores it, always defaulting to buffered execution. Fix the async
methods to respect `CommandDefinition.Flags` consistently with the
synchronous paths.

### N4: Fix DefaultTypeMap not matching underscore-separated column names

`DefaultTypeMap` maps columns to properties by exact name match only.
Columns like `first_name` fail to map to a property `FirstName`. Fix
`DefaultTypeMap.cs` to support an underscore-removal convention so
`first_name` maps to `FirstName` without requiring a custom type map.

### N5: Fix GridReader.Read<T> not advancing to next result set on empty results

When `GridReader.Read<T>()` encounters a result set with zero rows, it
does not call `NextResult()` on the underlying reader, causing the next
`Read<T>()` call to fail or return stale data. Fix the result-set
advancement logic in `SqlMapper.GridReader.cs`.

### N6: Fix SqlMapper.TypeHandler not clearing cache on handler registration

Registering a new `TypeHandler<T>` via `SqlMapper.AddTypeHandler<T>()`
does not invalidate the `TypeDeserializerCache`, so previously compiled
deserializers continue using the old handler. Fix the handler
registration in `SqlMapper.TypeHandler.cs` to purge the relevant
cache entries in `SqlMapper.TypeDeserializerCache.cs`.

### N7: Fix DbString defaulting to ANSI for Unicode connection strings

`DbString` defaults `IsAnsi = false`, but when the connection string
specifies an ANSI-only provider, the parameter is still sent as
`DbType.String` (Unicode) causing implicit conversion. Fix `DbString`
to detect the provider and set `IsAnsi` appropriately based on the
connection's provider factory.

### N8: Fix SqlMapper.Identity hash collision for different generic types

`SqlMapper.Identity` computes its hash code without including the
generic type argument of `Query<T>`. Two queries with the same SQL
but different `T` can collide, causing incorrect cached deserializers.
Fix the hash computation in `SqlMapper.Identity.cs` to incorporate
the result type.

### N9: Fix Execute returning -1 for stored procedures with no SET NOCOUNT

`Execute()` returns `-1` for stored procedure calls when the procedure
does not set `NOCOUNT ON`, because the intermediate row counts from
DML statements inside the procedure confuse the result aggregation.
Fix `SqlMapper.cs` to aggregate all `RecordsAffected` values from the
command execution for stored-procedure commands.

### N10: Fix QueryFirstOrDefault<T> materializing entire result set

`QueryFirstOrDefault<T>()` internally calls `Query<T>()` and takes
the first element, materializing all rows before discarding them. Fix
the method in `SqlMapper.cs` to read only the first row from the
`IDataReader` and immediately dispose the reader.

## Medium

### M1: Add QueryUnbufferedAsync<T> streaming support

Implement `QueryUnbufferedAsync<T>()` that returns
`IAsyncEnumerable<T>` for streaming large result sets without buffering.
The method must support cancellation via `CancellationToken`, respect
`CommandDefinition.Flags`, and properly dispose the reader and
connection on enumeration completion or cancellation. Changes span
`SqlMapper.Async.cs` and `CommandDefinition.cs`.

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
logic, and `DefaultTypeMap.cs` for column mapping.

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
