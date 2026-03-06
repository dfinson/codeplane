# serde-rs/serde

| Field | Value |
|-------|-------|
| **URL** | https://github.com/serde-rs/serde |
| **License** | MIT OR Apache-2.0 |
| **Language** | Rust |
| **Scale** | Small (focused library) |
| **Category** | Serialization/deserialization framework |
| **Set** | ranker-gate |
| **Commit** | `3fd6b4840a8c7dcc34284f8d478c744c4f78ebfb` |

## Why this repo

- **Single-purpose**: The foundational serialization framework for Rust.
  Core trait system (Serialize, Deserialize, Serializer, Deserializer) is
  compact and graspable by one developer.
- **Well-structured**: Clear split between `serde/` (core traits + data model),
  `serde_derive/` (proc macros), and `serde_derive_internals/` (shared derive
  logic). Each crate has defined boundaries.
- **Rich history**: 4K+ commits, meticulous stewardship by dtolnay. Changes
  are careful and well-reviewed. Foundational to the Rust ecosystem.
- **Permissive**: Dual-licensed MIT OR Apache-2.0.

## Structure overview

```
serde/
├── src/
│   ├── lib.rs           # Re-exports, feature gates
│   ├── ser/             # Serialize trait, Serializer trait, impls
│   ├── de/              # Deserialize trait, Deserializer trait, impls
│   ├── private/         # Internal implementation details
│   └── macros.rs        # Helper macros
serde_derive/
├── src/
│   ├── lib.rs           # Proc macro entry points
│   ├── ser.rs           # Serialize derive codegen
│   ├── de.rs            # Deserialize derive codegen
│   └── internals/       # Attribute parsing, AST, validation
```

## Scale indicators

- ~40 Rust source files across crates
- ~25K lines of code
- Shallow module structure (2 levels)
- Zero runtime dependencies (core crate)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `#[serde(flatten)]` ignoring `#[serde(deny_unknown_fields)]`

When a struct uses `#[serde(flatten)]` on a field and
`#[serde(deny_unknown_fields)]` on the container, unknown fields are
silently accepted instead of causing a deserialization error. The
flattened field's `MapAccess` implementation consumes all keys, preventing
the deny-unknown-fields check from seeing them. Fix the flattened
deserialization to forward unrecognized keys to the container's unknown
field handler.

### N2: Add `#[serde(skip_serializing_if_default)]` attribute

Currently users must write `#[serde(skip_serializing_if = "is_default")]`
and define a helper function. Add a built-in `skip_serializing_if_default`
attribute that skips serialization when the field value equals its
`Default::default()`. The field type must impl both `Default` and
`PartialEq`.

### N3: Fix error span pointing to wrong field with `#[serde(rename_all)]`

When deserialization fails on a renamed field (via `rename_all = "camelCase"`),
the error message shows the original Rust field name instead of the
renamed (JSON) key name. Users see "unknown field `userData`" when the
error should say "missing field `userData`". Fix the error reporting to
use the serialized name.

### N4: Fix `#[serde(default)]` not called when field is `null` in JSON

When a JSON field is explicitly `null` and the struct field has
`#[serde(default)]`, the default function is not called — instead,
`null` is deserialized as `None` wrapping the default. The default
should be used when the field is either absent or `null`. Fix the
deserialize implementation for defaulted fields.

### N5: Add `i128`/`u128` variants to the `Content` buffer enum

The `Content` enum in `serde_core/src/private/content.rs` is used to
buffer values for internally-tagged and untagged enum deserialization.
It has variants for `I8` through `I64` and `U8` through `U64` but
lacks `I128` and `U128`. The `TagOrContentVisitor` in
`serde/src/private/de.rs` explicitly documents this gap: "Cannot
capture ... `i128` and `u128`". Add the missing variants to `Content`
and update `ContentVisitor`, `ContentDeserializer`, and
`content_unexpected` to handle them.

### N6: Fix `#[serde(bound = "")]` not clearing derive-inferred bounds

When explicitly setting `#[serde(bound = "")]` to clear all trait
bounds from the derived impl, the derive macro still adds `T: Serialize`
bounds for generic type parameters. Fix the bound clearing to fully
remove inferred bounds when an explicit empty bound is provided.

### N7: Fix `serde_derive` producing ambiguous associated type in certain generics

When deriving `Serialize` on a struct with a generic field `T::Output`
where `T` has multiple traits defining `Output`, the derived code
produces ambiguous associated type errors. Fix the derive codegen to
fully qualify associated types.

### N8: Add `#[serde(try_from = "Type")]` error type propagation

When using `#[serde(try_from = "String")]`, conversion errors lose
their original error type and are wrapped in a generic serde error
string. Propagate the `TryFrom::Error` type details through the
deserialization error so users can match on the original error.

### N9: Fix `#[serde(with = "module")]` not working with Option fields

Using `#[serde(with = "custom_module")]` on an `Option<T>` field
requires the module to handle the `Option` wrapper itself. Add
automatic `Option` unwrapping so the `with` module only needs to
handle `T`, and `None` → skip / `null` is handled by serde.

### N10: Improve untagged enum deserialization error messages

When all variants of a `#[serde(untagged)]` enum fail to match, the
generated code in `serde_derive/src/de/enum_untagged.rs` emits a
generic "data did not match any variant of untagged enum" error,
discarding per-variant failure details. There is a TODO comment
(lines 34–38) noting this limitation. Collect the error from each
variant deserialization attempt and combine them into the final error
message so users can diagnose which variant was closest to matching.

## Medium

### M1: Extend `#[serde(default)]` to enum variants with named fields

Currently `#[serde(default)]` can only be applied at the struct
container level or on individual fields (see
`serde_derive/src/internals/attr.rs` lines 372–388 where it emits
"can only be used on structs"). Add support for `#[serde(default)]`
on enum variants with named fields so that missing fields within a
variant use `Default::default()` during deserialization. This requires
changes in `serde_derive/src/internals/attr.rs` (variant-level default
parsing), `serde_derive/src/de/struct_.rs` (default generation for
variant fields), and `serde_derive/src/internals/check.rs`
(validation for the new usage).

### M2: Implement `deserialize_in_place` for enum types

`deserialize_in_place` allows deserializing into an existing value
without allocating a new one. The derive macro already generates
`deserialize_in_place` for structs and tuples, but
`serde_derive/src/de.rs` lines 358–360 explicitly return `None` for
`Data::Enum`. Implement `deserialize_in_place` for enums by checking
whether the existing variant matches the incoming tag and reusing the
existing allocation when the variant matches. This touches
`serde_derive/src/de.rs` (entry point), the per-representation
modules `de/enum_externally.rs`, `de/enum_internally.rs`,
`de/enum_adjacently.rs`, and the `Deserialize` trait definition in
`serde_core/src/de/mod.rs`.

### M3: Implement `#[serde(transparent)]` for enums

The `#[serde(transparent)]` attribute works on structs with a single
field, delegating to that field's Serialize/Deserialize. Extend support
to enums where each variant has exactly one field — serialization
delegates to the variant's field, and deserialization tries each
variant's inner type in order.

### M4: Implement zero-copy deserialization for nested structures

Currently `#[serde(borrow)]` enables zero-copy `&str` deserialization
for top-level fields but fails for fields in nested structs or enum
variants. Extend the borrow mechanism to propagate lifetime constraints
through nested structures, allowing borrowed references at any depth.

### M5: Propagate map length hints through `#[serde(flatten)]` serialization

When a struct contains `#[serde(flatten)]` fields, the derive-generated
`Serialize` impl in `serde_derive/src/ser.rs` (line 388) calls
`serialize_map` with `None` as the length hint because the number of
entries contributed by flattened fields is unknown at codegen time.
Implement a length-hint strategy: track the count of non-flattened
fields at compile time and add a runtime `size_hint` query on the
flattened field's serialization. Combine these into an accurate `len`
parameter for `serialize_map`. This requires changes in
`serde_derive/src/ser.rs` (struct and variant serialization) and
`serde/src/private/ser.rs` (`FlatMapSerializer` at line 1003 and
`FlatMapSerializeStruct`).

### M6: Implement `#[serde(validate)]` for post-deserialization validation

Add `#[serde(validate = "validate_fn")]` that calls a validation
function after all fields are deserialized. If validation fails, return
a deserialization error with the validation message. Support field-level
validation (`#[serde(validate = "check_range")]` on individual fields)
that runs after the field is deserialized but before the struct is
constructed.

### M7: Support `#[serde(getter)]` on enum variants for remote types

`#[serde(getter = "...")]` enables serialization of remote types
with private fields by calling an accessor function instead of
accessing the field directly. However,
`serde_derive/src/internals/check.rs` (lines 82–87) blocks getters
in enums: "#[serde(getter = \"...\")]\nis not allowed in an enum".
Lift this restriction for enums that use `#[serde(remote = "...")]`
so that each variant's fields can use getters. This requires changes
in `serde_derive/src/internals/check.rs` (relax validation),
`serde_derive/src/ser.rs` (line 1281, getter codegen for enum
variants), and `serde_derive/src/de.rs` (remote enum handling).

### M8: Implement content-type-aware serialization

Add a mechanism for serializers to declare their content type (JSON,
XML, TOML, binary) so that `Serialize` implementations can branch on
the output format. Add `Serializer::content_type() -> ContentType`
with a default implementation returning `Unknown`. Document patterns
for format-specific serialization without runtime overhead for the
common case.

### M9: Add `#[serde(flatten)]` performance optimization

`#[serde(flatten)]` currently has O(n²) performance with multiple
flattened fields because each flattened field deserializes the entire
remaining map. Implement a buffered flattening strategy that reads the
map once into an intermediate buffer, then distributes keys to the
appropriate flattened fields.

### M10: Add `#[serde(expecting)]` support for enum variants and struct fields

Currently `#[serde(expecting = "...")]` can only be applied at the
container level (see `serde_derive/src/internals/attr.rs` line 173,
489–491). Extend the attribute to enum variants and individual struct
fields so that each variant or field visitor can produce a custom
`expecting` message on type mismatch. This requires adding `expecting`
parsing to `Variant` and `Field` attribute blocks in
`serde_derive/src/internals/attr.rs`, threading the custom message
into generated `Visitor::expecting` implementations in
`serde_derive/src/de/struct_.rs` and `serde_derive/src/de/identifier.rs`,
and updating `serde_derive/src/internals/check.rs` to validate that
`expecting` on a field is only used with types that have a custom
visitor.

## Wide

### W1: Add first-class support for `serde` with `no_std` + `no_alloc`

Currently `serde` without `std` still requires `alloc` for `String`,
`Vec`, and `BTreeMap`. Implement a `no_alloc` feature that replaces
heap-allocated types with bounded stack alternatives. Provide
`Serialize` impls for `heapless::String`, `heapless::Vec`, etc. Adjust
the data model, derive macros, and built-in impls to work without an
allocator. This affects the core trait definitions, derive codegen,
and the built-in impl set.

### W2: Implement schema generation from serde-annotated types

Add a `serde_schema` crate that generates data schemas from types
implementing `Serialize`/`Deserialize`. The schema should capture field
names (including renames), types, optionality, default values, tag
representations, and flattening structure. Output format should be
JSON Schema. The derive macro should generate a `Schema` impl alongside
`Serialize`/`Deserialize`. Support recursive types via `$ref`.

### W3: Implement async serialization and deserialization

Add `AsyncSerializer` and `AsyncDeserializer` traits for
streaming serialization/deserialization over async I/O. Support
serializing large collections without buffering the entire output,
and deserializing from async byte streams (e.g., `tokio::io::AsyncRead`).
Add derive macro support for async traits. This crosses the core
traits, derive macros, and data model.

### W4: Add serde data model introspection API

Implement a `Describe` trait (derivable alongside `Serialize`/
`Deserialize`) that returns a structural schema of the type: field
names, types, optional/required status, default values, and container
attributes. The schema should be queryable at runtime without needing
actual data. This enables JSON Schema generation, documentation
generation, and migration tooling. Crosses derive macros, core
traits, and the data model.

### W5: Implement backwards-compatible deserialization with version tags

Add `#[serde(version = 2)]` that embeds a version tag in serialized
output. On deserialization, detect the version and dispatch to the
appropriate `Deserialize` implementation. Support migration functions
between versions: `#[serde(migrate_from(v1 = "V1Type", with = "migrate_fn"))]`.
This requires derive macro changes, core trait extensions, and a
version resolution protocol.

### W6: Add first-class support for self-describing formats

Implement a `SelfDescribing` serializer trait that embeds type information
alongside values. On deserialization, `serde_any::from_reader()` can
detect the format (JSON, TOML, YAML, MessagePack) from the content
and deserialize automatically. Includes format detection heuristics,
a registry for format handlers, and an envelope format for explicit
format tagging.

### W7: Implement streaming collection serialization with backpressure

Add `SerializeStreamingSeq` and `SerializeStreamingMap` traits that
support serializing collections element-by-element with flow control.
When the output buffer is full, the serializer pauses production
(backpressure). Support for `Iterator`-based serialization without
collecting into a `Vec` first. Crosses the core serialization traits,
derive macros (for `#[serde(streaming)]`), and data model.

### W8: Add compile-time format-specific optimization

Implement a compile-time specialization mechanism where the derive
macro generates format-specific fast paths for common serialization
formats. For example, `Serialize` on a struct would generate both the
generic trait implementation and a direct-to-JSON function (bypassing
the `Serializer` trait) when the output format is known at compile
time. Requires proc macro architecture changes and conditional
compilation support.

### W9: Implement field-level lifetime tracking for zero-copy deserialization diagnostics

Currently `#[serde(borrow)]` requires manual annotation on fields
that should borrow from the deserializer input. Add automatic borrow
detection: have the derive macro analyse field types to determine
which can borrow from the deserializer (`&'de str`, `&'de [u8]`,
`Cow<'de, str>`, etc.) and which cannot. When a type could borrow
but lacks `#[serde(borrow)]`, emit a compile-time suggestion. When
`#[serde(borrow)]` is on a type that cannot borrow, emit a warning.
This requires changes in `serde_derive/src/internals/attr.rs` (borrow
analysis metadata, lines 1046–1058), `serde_derive/src/bound.rs`
(lifetime parameter analysis for borrow inference),
`serde_derive/src/de.rs` (borrowed lifetime tracking, lines 283–296),
`serde_derive/src/de/struct_.rs` (struct field borrow propagation),
`serde_derive/src/de/tuple.rs` (tuple field borrow propagation),
`serde_derive/src/internals/check.rs` (borrow validation), and
`serde_core/src/de/mod.rs` (lifetime bound documentation).

### W10: Add serde ecosystem interop layer

Implement a compatibility layer between serde and other serialization
ecosystems: protobuf (`prost`), Cap'n Proto, FlatBuffers, and
`rkyv` (zero-copy). For each format, provide bidirectional conversion
between the format's native types and serde's data model. Support
deriving both serde and the native traits from a single struct
definition. This crosses derive macros, data model mapping, and adds
per-format bridge modules.
