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

8 tasks (3 narrow, 3 medium, 2 wide) for the C# JSON framework.

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
