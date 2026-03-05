# JamesNK/Newtonsoft.Json

| Field | Value |
|-------|-------|
| **URL** | https://github.com/JamesNK/Newtonsoft.Json |
| **License** | MIT |
| **Language** | C# |
| **Scale** | Medium (multi-module project) |
| **Category** | JSON framework |

## Why this repo

- **Multi-module with clear boundaries**: Serialization engine, LINQ-to-JSON
  (JToken tree), reader/writer streaming API, schema validation, converters,
  BSON support — each a distinct subsystem. Working on serialization attributes
  requires navigating between attribute definitions, converter resolution,
  and the serialization pipeline.
- **Well-structured**: Source under `Src/Newtonsoft.Json/` with clear namespace
  split. `Linq/` for JToken, `Serialization/` for the serialization engine,
  `Converters/` for type converters, `Schema/` for JSON schema.
- **Rich history**: 3K+ commits, 15+ years of development. The most widely
  used .NET library — rich variety of bug fixes, feature additions, and
  performance optimizations in the PR history.
- **Permissive**: MIT license.

## Structure overview

```
Src/Newtonsoft.Json/
├── JsonConvert.cs               # Static entry point
├── JsonSerializer.cs            # Serialization engine
├── JsonReader.cs                # Base streaming reader
├── JsonWriter.cs                # Base streaming writer
├── JsonTextReader.cs            # Text JSON reader
├── JsonTextWriter.cs            # Text JSON writer
├── Linq/                        # LINQ-to-JSON
│   ├── JToken.cs                # Base token type
│   ├── JObject.cs               # JSON object
│   ├── JArray.cs                # JSON array
│   └── JValue.cs                # JSON primitive
├── Serialization/               # Serialization pipeline
│   ├── JsonSerializerInternalReader.cs
│   ├── JsonSerializerInternalWriter.cs
│   ├── JsonContract.cs          # Type contract system
│   ├── DefaultContractResolver.cs
│   └── JsonProperty.cs
├── Converters/                  # Built-in type converters
├── Bson/                        # BSON reader/writer
└── Utilities/                   # Internal utilities
```

## Scale indicators

- ~150 C# source files
- ~60K lines of code
- 2-3 levels of namespace nesting
- Clear subsystem boundaries (serialization, LINQ, streaming)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `JsonConvert.PopulateObject` not respecting `NullValueHandling.Ignore`

When using `PopulateObject` with `NullValueHandling.Ignore` in the
serializer settings, null values in the JSON still overwrite non-null
existing properties on the target object. The population path does not
check `NullValueHandling` before setting property values. Fix the
populate logic to respect the null handling setting.

### N2: Add `Required.AllowDefault` for value types

The `[JsonProperty(Required = Required.Always)]` attribute requires a
non-null value, which for value types (int, bool) means the property
must be present in the JSON even when the value is the type's default
(0, false). Add `Required.AllowDefault` that only checks for presence
in the JSON without validating against type defaults.

### N3: Fix `JObject.Parse` stack overflow on deeply nested JSON

Parsing a JSON document with ~1000 levels of nesting causes a
`StackOverflowException` because the recursive descent parser uses
the call stack for nesting depth tracking. Add a configurable maximum
depth (default 64) and throw a `JsonReaderException` when exceeded.

### N4: Fix `JsonTextReader` ignoring trailing commas in strict mode

When `JsonTextReader` is configured with strict parsing, it silently
accepts trailing commas in arrays and objects (e.g., `[1, 2, 3,]`).
The reader's `ParseValue` and `ParseObject` methods do not check for
a trailing comma before the closing bracket or brace. Fix the reader
to throw a `JsonReaderException` when a trailing comma is encountered
in strict mode.

### N5: Fix `DefaultContractResolver` cache not distinguishing generic type arguments

When resolving contracts for `List<int>` and `List<string>`,
`DefaultContractResolver` caches the contract by the open generic
type definition, causing both to share the same `JsonArrayContract`
with identical item converter settings. The cache key in
`ResolveContract` needs to use the closed generic type rather than
the generic type definition.

### N6: Fix `JValue.Equals` not comparing `DateTimeOffset` and `DateTime` correctly

`JValue.Equals` returns `false` when comparing a `JValue` holding a
`DateTime` with one holding a `DateTimeOffset` that represents the
same instant. The `Compare` method in `JValue` does not attempt to
normalize the two date types before comparison. Fix `JValue.Compare`
to convert to a common representation when both values are date-like.

### N7: Fix `JsonSerializerInternalWriter` not escaping single quotes in property names

When `JsonTextWriter.QuoteChar` is set to single quote (`'`), the
serialization writer does not escape single quotes embedded inside
property names, producing invalid JSON like `{'it's': 'val'}`.
`WritePropertyName` in `JsonTextWriter` needs to escape the active
quote character within the name string.

### N8: Fix `BsonReader` returning wrong type for empty binary data

When reading a BSON binary field with zero length, `BsonReader`
returns `null` instead of an empty `byte[]`. The `ReadBinary` method
short-circuits on zero length before allocating the array. Fix it to
return `new byte[0]` for zero-length binary data.

### N9: Fix `JsonSerializerInternalReader` skipping `OnDeserialized` callback for populated objects

When `JsonSerializer.Populate` is used to fill an existing object,
the `OnDeserialized` callback (via `[OnDeserialized]` attribute or
`ISerializationCallback`) is not invoked after population completes.
`PopulateObject` in `JsonSerializerInternalReader` finishes after
setting properties but never raises the deserialized event. Add the
callback invocation at the end of the populate path.

### N10: Fix `JArray.Remove` not updating parent references

Removing a `JToken` from a `JArray` using `Remove(JToken)` clears
the item from the internal list but does not reset the removed
token's `Parent` property to `null`. This causes the removed token
to still report the old array as its parent, leading to incorrect
behavior when the token is added to a different container.

## Medium

### M1: Implement async serialization and deserialization

Add `JsonConvert.SerializeObjectAsync` and `DeserializeObjectAsync`
that work with `Stream` and use async I/O. The async path should
not buffer the entire JSON in memory. Support `CancellationToken`
for cancellation. The async reader/writer should yield periodically
to avoid blocking the thread pool on large documents.

### M2: Add support for `System.Text.Json` JsonDocument interop

Implement converters that bridge between Newtonsoft.Json's `JToken`
tree and System.Text.Json's `JsonDocument`/`JsonElement`. Add
`JToken.FromJsonElement(JsonElement)` and `JToken.ToJsonElement()`.
Support round-tripping without data loss. Handle the semantic
differences between the two JSON models (Newtonsoft allows duplicate
keys, System.Text.Json does not).

### M3: Implement JSON Patch (RFC 6902) support

Add a `JsonPatch` class that represents a sequence of JSON Patch
operations (add, remove, replace, move, copy, test). Implement
`JToken.ApplyPatch(JsonPatch)` and `JsonPatch.Create(JToken original, JToken modified)` that generates the minimal patch between two
documents. Support atomic patch application (all-or-nothing).

### M4: Add polymorphic deserialization using a configurable discriminator property

Currently `TypeNameHandling` embeds the full .NET type name in JSON,
which is a security risk and couples JSON to CLR types. Add a
`DiscriminatorAttribute` and a `DiscriminatorConverter<TBase>` that
maps a JSON property (e.g., `"type": "circle"`) to concrete types
without exposing CLR names. Support registration via attribute and
via serializer settings. Wire the discriminator into the
`JsonSerializerInternalReader` type resolution pipeline alongside
the existing `$type` mechanism.

### M5: Implement JSON Merge Patch (RFC 7396) support on `JToken`

Add `JToken.MergePatch(JToken patch)` that applies an RFC 7396 merge
patch: null values delete keys, objects merge recursively, and
non-object values replace. Also add
`JToken.CreateMergePatch(JToken original, JToken modified)` to
compute the minimal merge patch between two documents. Unlike the
existing `Merge` method, this must follow RFC 7396 semantics exactly,
including null-means-delete behavior.

### M6: Add `JsonPath` filter expression support in `SelectTokens`

`JToken.SelectTokens` supports basic JSON Path navigation but does
not support filter expressions like `$.store.book[?(@.price < 10)]`.
Implement a filter expression parser in the `Linq/JsonPath/` layer
that handles comparison operators (`==`, `!=`, `<`, `>`, `<=`, `>=`),
boolean logic (`&&`, `||`), and property existence (`@.isbn`). Wire
the filter into `QueryExpression` evaluation.

### M7: Add `IAsyncEnumerable<T>` serialization and deserialization support

`JsonSerializer` does not handle `IAsyncEnumerable<T>` — it throws
at runtime because no `JsonContract` matches the interface. Add an
`AsyncEnumerableContract` and corresponding read/write logic in
`JsonSerializerInternalReader` and `JsonSerializerInternalWriter`
that streams elements from an async sequence to a JSON array and
deserializes a JSON array by yielding elements asynchronously.

### M8: Implement configurable `NamingStrategy` composition

The current `NamingStrategy` on `DefaultContractResolver` applies a
single naming transformation. Add `CompositeNamingStrategy` that
chains multiple strategies (e.g., snake_case first, then prefix with
underscore for private fields). Support per-type overrides via
`[JsonObject(NamingStrategy = ...)]` that compose with the resolver-
level strategy. Update `JsonProperty.PropertyName` resolution in
`DefaultContractResolver.SetPropertySettingsFromAttributes` to walk
the chain.

### M9: Add `JToken.ToObject<T>` support for dictionary types with complex keys

`JToken.ToObject<T>()` fails for `Dictionary<TKey, TValue>` when
`TKey` is not `string`, because the LINQ-to-JSON conversion path
assumes dictionary keys are always strings. Add conversion logic
in `JToken.ToObject` that detects non-string key dictionaries,
deserializes each `JProperty` name through the key type's converter,
and constructs the dictionary. Support `enum`, `int`, `Guid`, and
custom `TypeConverter`-backed key types.

### M10: Add support for deserializing C# record types with primary constructors

`JsonSerializerInternalReader` cannot correctly deserialize C# record
types that use primary constructors because the constructor parameter
names do not match the property names after `NamingStrategy` is
applied. Add logic in `DefaultContractResolver.CreateConstructorParameters`
to match constructor parameters to JSON properties using the resolved
property name rather than the CLR parameter name. Support `init`-only
setters and `with`-expression cloning for record population.

## Wide

### W1: Add JSON Schema generation from .NET types

Implement `JsonSchema.Generate<T>()` that produces a JSON Schema
(draft-2020-12) from a .NET type using its Newtonsoft.Json serialization
configuration. Respect `[JsonProperty]` attributes, `[JsonConverter]`
annotations, `Required` settings, `NullValueHandling`, and custom
contract resolvers. Support recursive types, generic types, inheritance
(`$ref`, `allOf`), and enum schemas. Add schema validation for
`JToken` instances.

### W2: Implement source generator for compile-time serialization

Add a C# source generator that emits serialization/deserialization
code at compile time, eliminating runtime reflection cost. The
generator should analyze `[JsonObject]` and `[JsonProperty]`
annotations, handle contract customization, support converters, and
produce code equivalent to the reflection-based serializer. Add a
`[JsonSourceGeneration]` attribute to opt-in per type. Maintain
behavioral parity with the runtime serializer for all supported
features.

### W3: Implement a JSON diff engine producing RFC 6902 patches

Add `JsonDiff.Compare(JToken left, JToken right)` that produces a
minimal RFC 6902 JSON Patch representing the structural difference
between two documents. The diff engine should handle object property
additions, removals, and modifications; array element insertions,
deletions, and moves (using LCS for optimal edit distance); and
nested changes with correct JSON Pointer paths. Integrate with the
existing `JToken` tree and provide `JToken.Diff(JToken other)` as a
convenience method. Support configurable diff strategies (structural
vs. semantic equality).

### W4: Add comprehensive date and time handling overhaul

Centralize all date/time conversion logic currently scattered across
`DateTimeUtils`, `IsoDateTimeConverter`, `JavaScriptDateTimeConverter`,
and inline parsing in `JsonReader`. Introduce a `DateTimeHandling`
settings object on `JsonSerializerSettings` that controls format
selection (ISO 8601, Unix epoch seconds, Unix epoch milliseconds,
Microsoft `/Date()/`), time zone normalization (UTC, local, offset-
preserving), and calendar system. Update `JsonTextReader.ParseDate`,
`JsonTextWriter.WriteValue(DateTime)`, and all date converters in
`Converters/` to delegate to the centralized handler. Ensure
round-trip fidelity for `DateTime`, `DateTimeOffset`, and `TimeSpan`.

### W5: Implement buffered large-document streaming with `JsonReader.ReadAhead`

Add a `ReadAheadJsonReader` wrapper that reads large JSON documents
in configurable chunks (default 64 KB) without loading the entire
document into memory. Integrate with `JsonSerializer.Deserialize` so
that collection deserialization can process elements incrementally.
Add `JsonSerializer.DeserializeStream<T>(Stream, Action<T> onItem)`
for processing large arrays element by element. Wire into
`JsonSerializerInternalReader.CreateList` to support incremental
collection building. Support cancellation and progress reporting.

### W6: Add a diagnostic tracing layer for the serialization pipeline

Add `JsonSerializerTrace` that hooks into `JsonSerializerInternalReader`
and `JsonSerializerInternalWriter` to emit structured diagnostic
events: contract resolution (type → contract mapping), converter
selection (which `JsonConverter.CanConvert` matched), property
population (name, value, skip reason), reference tracking (`$ref`
resolution), and error handling (which error strategy was applied).
Expose the trace via `JsonSerializerSettings.TraceWriter` using a
new `ITraceWriter` interface with `Trace(TraceLevel, string, Exception)`
method. Add built-in `MemoryTraceWriter` and `DiagnosticSourceTraceWriter`
implementations. Ensure zero overhead when tracing is disabled.

### W7: Implement AOT and trimmer compatibility for the serialization engine

The reflection-heavy `DefaultContractResolver` and converter
discovery pipeline cause trimming warnings and failures in
NativeAOT scenarios. Add `[DynamicallyAccessedMembers]` annotations
throughout `Serialization/`, introduce `JsonTypeInfo<T>` metadata
containers that can be pre-generated, and create a
`TrimSafeContractResolver` that uses only statically-known type
information. Update `JsonSerializerInternalReader` and
`JsonSerializerInternalWriter` to optionally use the pre-built
contracts. Add linker XML for types that must survive trimming.
Validate with `ILLink` analyzer and `PublishTrimmed`.

### W8: Add a contract resolver composition and override system

Add `CompositeContractResolver` that chains multiple
`IContractResolver` instances with priority-based override semantics.
Each resolver in the chain can modify, wrap, or replace the contract
produced by lower-priority resolvers. Add `ContractResolverBuilder`
for fluent configuration: `.AddResolver<T>()`, `.ForType<T>(c => ...)`
per-type overrides, `.WithFallback(resolver)`. Update
`JsonSerializer.ContractResolver` to accept `IContractResolver` or
`CompositeContractResolver`. Ensure the caching layer in
`DefaultContractResolver` is aware of the composition chain so that
per-resolver caches do not conflict.

### W9: Implement a JSON Schema validation engine on `JToken`

Add `JsonSchemaValidator` that validates `JToken` instances against
a JSON Schema (draft-2020-12). Support `type`, `properties`,
`required`, `additionalProperties`, `items`, `prefixItems`, `anyOf`,
`oneOf`, `allOf`, `not`, `if/then/else`, `$ref` with `$defs`,
`pattern`, `format`, `minimum/maximum`, `minLength/maxLength`,
`minItems/maxItems`, `uniqueItems`, and `enum`/`const`. Return
structured `ValidationResult` with JSON Pointer paths to failing
nodes and human-readable error messages. Support custom format
validators via `FormatHandlerRegistry`. Wire into `JToken.IsValid`
and `JToken.Validate`.

### W10: Add multi-target `Utf8JsonWriter`-based output for `JsonSerializer`

Currently `JsonSerializer` writes through `JsonTextWriter` which
internally builds a `string` via `StringWriter`. Add an alternative
output path using `System.Buffers.IBufferWriter<byte>` that writes
UTF-8 JSON bytes directly without intermediate string allocation.
Introduce `Utf8JsonSerializerWriter` implementing `JsonWriter` that
wraps `Utf8JsonWriter` from System.Text.Json. Update
`JsonSerializerInternalWriter.Serialize` to dispatch to the
appropriate writer based on output target. Support `Stream`,
`PipeWriter`, and `Memory<byte>` targets. Ensure feature parity
with `JsonTextWriter` including formatting, string escaping, and
comment writing.
