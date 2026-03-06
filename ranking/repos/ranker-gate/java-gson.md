# google/gson

| Field | Value |
|-------|-------|
| **URL** | https://github.com/google/gson |
| **License** | Apache-2.0 |
| **Language** | Java |
| **Scale** | Small (focused library) |
| **Category** | JSON serialization library |
| **Set** | ranker-gate |
| **Commit** | `990f1377e2e21d15e280e83190132e2f6baffae2` |

## Why this repo

- **Single-purpose**: JSON serialization/deserialization for Java. Core
  functionality is compact — one developer can understand the full codebase.
- **Well-structured**: All source under `gson/src/main/java/com/google/gson/`
  with clear separation between the public API (Gson, JsonElement), internal
  streaming (JsonReader, JsonWriter), type adapters, and reflection-based
  binding.
- **Rich history**: 2K+ commits, maintained by Google for 15+ years. Stable
  but still receiving meaningful improvements and bug fixes.
- **Permissive**: Apache-2.0.

## Structure overview

```
gson/src/main/java/com/google/gson/
├── Gson.java              # Main entry point
├── GsonBuilder.java       # Builder pattern configuration
├── JsonElement.java       # JSON tree model (base)
├── JsonObject.java        # JSON object node
├── JsonArray.java         # JSON array node
├── JsonPrimitive.java     # JSON primitive node
├── TypeAdapter.java       # Type adapter base class
├── TypeAdapterFactory.java # Factory for creating type adapters
├── stream/
│   ├── JsonReader.java    # Streaming JSON parser
│   ├── JsonWriter.java    # Streaming JSON writer
│   └── JsonToken.java     # Token types
├── reflect/
│   └── TypeToken.java     # Generic type capture
├── internal/              # Internal implementation
│   ├── bind/              # Built-in type adapter bindings
│   ├── sql/               # SQL type adapters
│   └── ...
└── annotations/           # @SerializedName, @Expose, etc.
```

## Scale indicators

- ~60 Java source files
- ~15K lines of code
- Flat package structure (2 levels)
- Minimal dependencies (none at runtime)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `@SerializedName` ignored on record components

Java records with `@SerializedName` on constructor parameters do not
use the annotated name during serialization. Gson's reflection logic
finds the annotation on the canonical constructor parameter but not on
the generated field. Fix the `ReflectiveTypeAdapterFactory` to check
record component annotations for `@SerializedName`.

### N2: Add `@JsonAdapter` support for enum constants

The `@JsonAdapter` annotation works on classes and fields but not on
individual enum constants. A common need is to serialize specific enum
values differently. Add support for `@JsonAdapter` on enum constants
so each constant can specify its own serialization/deserialization
logic.

### N3: Fix `JsonReader` incorrect line number after `skipValue()`

After calling `skipValue()` to skip a large nested JSON object,
`JsonReader.getPath()` reports the wrong line number in subsequent
error messages. The line counter does not track newlines within the
skipped content. Fix `skipValue()` to correctly count newlines even
when skipping.

### N4: Fix `TypeToken` failing for intersection types in generic bounds

When a class declares a field with a type like `<T extends Serializable & Comparable<T>>`, `TypeToken.get()` throws an `IllegalArgumentException` because it does not handle `java.lang.reflect.WildcardType` instances whose upper bounds are intersection types. Fix `TypeToken` to decompose intersection bounds and use the first bound for adapter resolution.

### N5: Fix `JsonWriter` omitting trailing newline in pretty-print mode

When pretty-printing is enabled via `setIndent()`, the output of `JsonWriter` does not end with a trailing newline after the root value closes. Most JSON formatting tools produce a final newline. Fix `JsonWriter.close()` to append a newline character when indentation is active and the root value has been completed.

### N6: Fix `GsonBuilder.setDateFormat()` silently ignoring invalid patterns

Passing an invalid `SimpleDateFormat` pattern string to `GsonBuilder.setDateFormat()` does not throw until the first serialization attempt, at which point the error message is confusing and does not reference the builder call. Validate the pattern eagerly inside `setDateFormat()` and throw an `IllegalArgumentException` with a clear message identifying the bad pattern.

### N7: Fix `JsonObject.entrySet()` mutation not reflected in serialization

Removing entries from the `Set` returned by `JsonObject.entrySet()` correctly removes them from the underlying map, but adding entries through `Set.add()` silently succeeds without updating the map on some code paths. Ensure the returned entry set is either fully backed by the map or is unmodifiable to prevent silent data loss.

### N8: Fix `Gson.fromJson(Reader)` not closing the reader on parse error

When `Gson.fromJson(Reader, Type)` encounters a `JsonSyntaxException` partway through parsing, it propagates the exception without closing the provided `Reader`. Callers using try-with-resources on the `Gson` call itself have no opportunity to close the reader. Fix the method to close the reader in all exceptional paths.

### N9: Fix `LazilyParsedNumber.equals()` not comparing values numerically

`LazilyParsedNumber.equals()` compares the underlying string representation directly, so `new LazilyParsedNumber("1.0").equals(new LazilyParsedNumber("1.00"))` returns `false`. This is inconsistent with `JsonPrimitive.equals()`, which uses `BigDecimal.compareTo()` to treat scale-different decimals as equal. When Gson parses a JSON number, it stores it internally as a `LazilyParsedNumber`, so calling `getAsNumber().equals(other.getAsNumber())` on two primitives with the same logical value but different text can return `false`. Fix `LazilyParsedNumber.equals()` to compare numerically via `BigDecimal.compareTo()` and update `hashCode()` to use a scale-independent representation.

### N10: Fix `JsonPrimitive.getAsInt()` silently truncating out-of-range values

`JsonPrimitive.getAsInt()` delegates to `getAsNumber().intValue()`, which silently narrows `long` or `BigDecimal` values that exceed `Integer.MAX_VALUE` or fall below `Integer.MIN_VALUE`. For example, `new JsonPrimitive(3_000_000_000L).getAsInt()` returns a truncated negative number with no error. This contrasts with `JsonReader.nextInt()`, which validates the cast and throws `NumberFormatException` on overflow. Fix `getAsInt()` (and similarly `getAsShort()` and `getAsByte()`) in `JsonPrimitive` to detect overflow and throw `NumberFormatException` when the value cannot be represented in the target type.

## Medium

### M1: Implement JSON streaming with back-pressure

Add a `JsonStreamer` class that reads from an `InputStream` and emits
parsed JSON tokens incrementally without buffering the entire document.
Support configurable buffer sizes. When the consumer pauses, the reader
should stop consuming from the stream (back-pressure). Support streaming
serialization as well — writing large collections element by element
without holding the full list in memory.

### M2: Add null-safety annotations throughout the API

Annotate all public API methods with `@Nullable` and `@NonNull`
(from `org.jspecify.annotations`). Add annotations to `Gson`,
`JsonElement`, `JsonObject`, `JsonArray`, `TypeAdapter`, `TypeToken`,
and all public interfaces. This requires auditing each method's actual
null behavior and fixing any methods whose behavior contradicts the
annotation.

### M3: Implement custom `TypeAdapter` composition

Add a `TypeAdapters.compose()` method that chains multiple TypeAdapters
together: the first adapter serializes/deserializes, then the result
is passed to the next adapter. This enables reusable transformations
(e.g., trim strings, clamp numbers) that compose with any base type
adapter without subclassing.

### M4: Add hierarchical `TypeAdapterFactory` registration with priority

Currently all factories registered via `GsonBuilder.registerTypeAdapterFactory()` are stored in a flat list and resolved in insertion order. Add a priority mechanism so factories can declare precedence levels. Factories at higher priority override lower ones for the same type, and the resolution logic in `Gson.getAdapter()` respects the ordering. Update `GsonBuilder` and the internal factory chain accordingly.

### M5: Implement lenient deserialization mode with fallback defaults

Add a `GsonBuilder.setLenientDeserialization()` mode where type mismatches during deserialization do not throw but instead use sensible defaults (zero for numbers, empty string for strings, empty collections for arrays, null for objects). Track each fallback in a `DeserializationWarnings` object accessible from a new `Gson.getLastWarnings()` method. Wire this through `ReflectiveTypeAdapterFactory` and all built-in adapters in `internal/bind/`.

### M6: Add `TypeAdapter` caching diagnostics and statistics

Add the ability to retrieve cache-hit statistics from Gson's internal `TypeAdapter` resolution. Expose a `Gson.getAdapterCacheStats()` method returning hit count, miss count, and the set of types currently cached. This requires modifying the thread-local adapter cache in `Gson`, introducing a `CacheStats` value object, and ensuring the bookkeeping is thread-safe without impacting serialization throughput.

### M7: Implement configurable field naming strategies per class

Currently `FieldNamingStrategy` applies globally to all classes. Add support for per-class naming strategies via a new `@NamingStrategy` annotation on classes, or through `GsonBuilder.registerNamingStrategy(Class<?>, FieldNamingStrategy)`. The per-class strategy should override the global one. Wire the lookup through `ReflectiveTypeAdapterFactory` and the field-binding logic in `internal/bind/`.

### M8: Support optional fields with `java.util.Optional` unwrapping

Add first-class support for `Optional<T>` fields so that `Optional.empty()` serializes as JSON `null` (or is omitted entirely when `serializeNulls` is off) and JSON `null` deserializes as `Optional.empty()` rather than a bare `null` reference. Implement a dedicated `TypeAdapterFactory` in `internal/bind/`, register it by default, and ensure it composes correctly with `@JsonAdapter`, `@SerializedName`, and custom `TypeAdapterFactory` registrations.

### M9: Implement `JsonPath` query support for `JsonElement` trees

Add a `JsonPath` class that evaluates JSONPath expressions (dot notation, bracket notation, wildcards, recursive descent, and array slices) against a `JsonElement` tree. Return results as a `JsonArray` of matched elements. Integrate with `Gson` by adding `Gson.queryTree(JsonElement, String)` and ensure the parser, evaluator, and result collector are fully covered.

### M10: Add `GsonBuilder` validation for conflicting configuration

`GsonBuilder` silently accepts contradictory settings — for example, enabling `serializeNulls()` and then registering a custom serializer that skips nulls, or setting two different date formats without the second overriding the first clearly. Add a `GsonBuilder.validate()` method that detects common conflicts and ambiguities, returning a list of warnings. Call this automatically during `create()` when a new `strictMode()` builder option is enabled.

## Wide

### W1: Add JSON Schema validation during deserialization

Implement JSON Schema (draft-2020-12) validation that can be applied
during deserialization. Add a `@JsonSchema` annotation that references
a schema resource, and a `GsonBuilder.setSchemaValidation()` option.
Schema violations should be collected as a list (not fail-fast) and
reported through a new `SchemaViolationException`. Support `$ref`,
`oneOf`, `allOf`, `anyOf`, and format validation.

### W2: Implement Gson 3.0 API with sealed interfaces and pattern matching

Design and implement a modernized Gson API that takes advantage of
Java 17+ features: sealed interfaces for `JsonElement` (enabling
pattern matching with `switch`), records for configuration, a builder
API using method chaining with generics, and `Stream<JsonElement>`
support for lazy iteration over JSON arrays. Maintain backward
compatibility through a compatibility layer.

### W3: Add asynchronous serialization and deserialization API

Design and implement `Gson.toJsonAsync()` and `Gson.fromJsonAsync()` methods that return `CompletableFuture` results. Large object graphs should be serialized on a configurable `Executor`, with the streaming layer (`JsonWriter`, `JsonReader`) adapted to work with non-blocking I/O through `AsynchronousByteChannel`. Ensure thread-safety of all adapter resolution, support cancellation, and propagate exceptions correctly through the future chain.

### W4: Implement a Gson plugin system with lifecycle hooks

Add a plugin API allowing third-party libraries to hook into Gson's serialization and deserialization lifecycle. Plugins should be discoverable via `ServiceLoader`, configurable through `GsonBuilder.installPlugin()`, and receive callbacks at key points: before/after serialization of each object, before/after deserialization, on adapter resolution, and on error. The hook dispatch must be efficient and must compose correctly with existing `TypeAdapterFactory` and `ExclusionStrategy` mechanisms.

### W5: Add JSON merge-patch (RFC 7396) and JSON Patch (RFC 6902) support

Implement both JSON Merge Patch and JSON Patch as first-class operations on Gson's `JsonElement` tree. Add `JsonPatch.apply(JsonElement)`, `JsonMergePatch.apply(JsonElement)`, and `JsonDiff.diff(JsonElement, JsonElement)` that produces a JSON Patch. The diff algorithm should handle object key reordering, array element moves, and nested structures efficiently. Integrate with `Gson` via convenience methods and ensure the patch model itself is serializable by Gson.

### W6: Implement annotation-driven polymorphic deserialization

Add a `@JsonPolymorphic` annotation and `@JsonSubType` repeatable annotation that together enable polymorphic deserialization without requiring a `RuntimeTypeAdapterFactory`. The discriminator field name, position (property, wrapper-object, wrapper-array), and subtype mappings should all be declarative. Implement a `PolymorphicTypeAdapterFactory` that reads these annotations, registers itself during `Gson.getAdapter()` resolution, and handles missing or unknown discriminators gracefully with configurable fallback behavior.

### W7: Add full `java.time` type adapter suite with configurable formatting

Implement type adapters for all major `java.time` types: `Instant`, `LocalDate`, `LocalTime`, `LocalDateTime`, `ZonedDateTime`, `OffsetDateTime`, `Duration`, `Period`, `Year`, `YearMonth`, and `MonthDay`. Support ISO-8601 by default and allow per-type format overrides through `GsonBuilder.setTemporalFormat(Class, DateTimeFormatter)`. Register adapters via a `JavaTimeTypeAdapterFactory` that participates in the standard factory chain and composes with `@JsonAdapter`.

### W8: Implement structural logging and diagnostics for serialization

Add an opt-in diagnostics mode activated via `GsonBuilder.enableDiagnostics()` that captures a structured trace of every serialization and deserialization operation: which `TypeAdapter` was selected for each type, how long each adapter took, which fields were skipped by exclusion strategies, and any coercions that occurred. Emit traces as `JsonElement` trees consumable by monitoring tools. The tracing must thread through `Gson`, `ReflectiveTypeAdapterFactory`, all built-in adapters, and the streaming layer without affecting performance when disabled.

### W9: Add size-limited and depth-limited deserialization for untrusted input

Implement security-oriented deserialization limits configurable via `GsonBuilder`: maximum input size in bytes, maximum nesting depth, maximum number of object keys, maximum string length, and maximum array size. Enforce limits in `JsonReader` at the streaming level so they cannot be bypassed by custom adapters. Add a `SecurityViolationException` with details about which limit was exceeded, and ensure the limits compose correctly with lenient mode, streaming APIs, and tree-model parsing.

### W10: Implement round-trip fuzz testing infrastructure for Gson

Build an internal testing framework that generates random `JsonElement` trees (objects, arrays, primitives, nulls, nested to arbitrary depth), serializes them with `Gson.toJson()`, deserializes the output back with `Gson.fromJson()`, and asserts structural equality. Cover edge cases: Unicode surrogates, extremely large numbers, deeply nested structures, duplicate keys, and all `JsonPrimitive` numeric types. Integrate this as a reproducible test suite that can be run with a fixed seed and report minimal failing cases.
