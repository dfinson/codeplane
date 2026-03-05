# nlohmann/json

| Field | Value |
|-------|-------|
| **URL** | https://github.com/nlohmann/json |
| **License** | MIT |
| **Language** | C++ |
| **Scale** | Medium (single-header with modular detail/) |
| **Category** | JSON library |
| **Set** | Cutoff |

## Why this repo

- **Iconic C++ library**: The most widely used JSON library for modern
  C++ with 43K+ stars, exercising advanced template metaprogramming,
  SFINAE, and ADL-based serialization. Navigation requires understanding
  deeply nested namespaces, tag-dispatch patterns, and template
  specialization throughout `detail/`.
- **Well-structured**: Despite shipping as a single amalgamated header
  (`json.hpp`), the source is organized into `detail/` subdirectories
  covering conversions, input/output adapters, iterators, binary
  formats, and metaprogramming utilities — each with clear responsibilities.
- **Rich feature surface**: Supports JSON Pointer (RFC 6901), JSON Patch
  (RFC 6902), JSON Merge Patch (RFC 7396), CBOR, MessagePack, UBJSON,
  BSON, and BJData binary formats, plus SAX-style parsing and
  user-defined type conversions via `to_json`/`from_json` ADL.
- **Permissive**: MIT license.

## Structure overview

```
include/nlohmann/
├── json.hpp                          # Amalgamated single-header (generated)
├── json_fwd.hpp                      # Forward declarations
├── ordered_map.hpp                   # Insertion-order-preserving map
├── byte_container_with_subtype.hpp   # Binary subtype container (CBOR tags)
├── adl_serializer.hpp                # Default ADL-based serializer
├── detail/
│   ├── conversions/
│   │   ├── from_json.hpp             # from_json overloads (deserialize)
│   │   └── to_json.hpp              # to_json overloads (serialize)
│   ├── input/
│   │   ├── input_adapters.hpp        # Input sources (string, stream, iterator)
│   │   ├── lexer.hpp                 # Tokenizer / scanner
│   │   ├── parser.hpp                # Recursive-descent JSON parser
│   │   ├── binary_reader.hpp         # CBOR/MessagePack/UBJSON/BSON/BJData decoder
│   │   ├── json_sax.hpp             # SAX event interface
│   │   └── position_t.hpp           # Source position tracking
│   ├── output/
│   │   ├── output_adapters.hpp       # Output sinks (string, stream, vector)
│   │   ├── serializer.hpp            # JSON text serializer (dump)
│   │   └── binary_writer.hpp        # CBOR/MessagePack/UBJSON/BSON/BJData encoder
│   ├── iterators/
│   │   ├── iter_impl.hpp             # Main bidirectional iterator
│   │   ├── iteration_proxy.hpp       # items() key-value proxy
│   │   ├── json_reverse_iterator.hpp # Reverse iterator adapter
│   │   ├── internal_iterator.hpp     # Union of object/array iterators
│   │   └── primitive_iterator.hpp    # Iterator for non-structured types
│   ├── meta/
│   │   ├── type_traits.hpp           # SFINAE helpers, is_basic_json, etc.
│   │   ├── detected.hpp              # Detection idiom (void_t, detected_t)
│   │   ├── is_sax.hpp               # SAX handler concept check
│   │   ├── call_std/
│   │   │   ├── begin.hpp            # ADL begin()
│   │   │   └── end.hpp              # ADL end()
│   │   └── identity_tag.hpp         # Tag dispatch helpers
│   ├── json_pointer.hpp              # JSON Pointer (RFC 6901)
│   ├── json_ref.hpp                  # Reference wrapper for init-list
│   ├── hash.hpp                      # std::hash specialization
│   ├── string_concat.hpp            # Compile-time string concatenation
│   ├── string_escape.hpp            # JSON string escaping
│   ├── value_t.hpp                   # Value type enum (null, object, array, ...)
│   ├── macro_scope.hpp              # Internal macro definitions
│   ├── macro_unscope.hpp            # Internal macro cleanup
│   ├── exceptions.hpp               # Exception hierarchy (parse, type, out_of_range, other)
│   └── abi_macros.hpp               # ABI versioning macros
```

## Scale indicators

- ~50 header files under `include/nlohmann/detail/`
- ~25K lines of C++ template code
- Deeply nested namespace structure (`nlohmann::detail::...`)
- Zero runtime dependencies (header-only, standard library only)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix lexer not rejecting leading zeros in JSON numbers

The lexer in `detail/input/lexer.hpp` accepts numbers like `007` or
`01.5` as valid, but RFC 8259 forbids leading zeros in numbers
(except `0` itself or `0.x`). Fix the number-scanning state machine
to reject leading zeros and emit `parse_error.101` for such inputs.

### N2: Fix from_json not handling std::optional<T> for null values

The `from_json` overloads in `detail/conversions/from_json.hpp` do
not provide a specialization for `std::optional<T>`. Attempting to
deserialize a JSON null into `std::optional<T>` causes a compilation
error. Add a `from_json` overload that maps JSON null to
`std::nullopt` and non-null values to `T`.

### N3: Fix serializer emitting invalid UTF-8 replacement on lone surrogates

The serializer in `detail/output/serializer.hpp` replaces invalid
UTF-8 sequences with `\ufffd` when `error_handler_t::replace` is
active, but for lone high surrogates in `\uD800`–`\uDBFF` it emits
raw bytes instead of the replacement character. Fix the
`dump_escaped` method to detect unpaired surrogates and apply the
replacement consistently.

### N4: Fix json_pointer::back() not checking for empty pointer

`json_pointer::back()` in `detail/json_pointer.hpp` calls
`reference_tokens.back()` without checking if the pointer is empty
(root pointer). This causes undefined behavior. Fix `back()` to throw
`out_of_range.405` when the pointer has no reference tokens.

### N5: Fix ordered_map::erase invalidating iteration order

`ordered_map::erase()` in `ordered_map.hpp` uses swap-and-pop removal
to maintain O(1) erasure, but this changes the iteration order of
remaining elements. Fix the erase method to preserve insertion order
by using a shift-down approach or maintaining a stable index.

### N6: Fix binary_reader not validating CBOR tag values for typed arrays

The CBOR reader in `detail/input/binary_reader.hpp` accepts any tag
value without validation when reading typed arrays (tags 64–87).
Invalid tag values outside the typed-array range are silently treated
as untagged data. Fix the reader to reject unexpected tag values with
`parse_error.112`.

### N7: Fix iter_impl::operator[] returning dangling reference for object values

`iter_impl::operator[]` in `detail/iterators/iter_impl.hpp` returns a
reference to the mapped value when iterating over objects, but for
primitive types it constructs a temporary and returns a reference to
it. Fix the primitive path to either disallow indexing or return by
value.

### N8: Fix value_t comparison not treating integer and unsigned as compatible

The `operator<` for `value_t` in `detail/value_t.hpp` treats
`number_integer` and `number_unsigned` as distinct types for ordering
purposes. This causes `json(1) < json(2u)` to compare by type
discriminator rather than numeric value. Fix the comparison operators
in `basic_json` to unify integer and unsigned ordering.

### N9: Fix parser ignoring max_depth limit for empty nested containers

The recursive-descent parser in `detail/input/parser.hpp` enforces a
`max_depth` limit on nesting, but empty containers like `[[]]` or
`{"a":{}}` do not increment the depth counter because the open-bracket
is consumed before the depth check. Fix the depth tracking to
increment before recursing into any container.

### N10: Fix to_json not handling std::variant with monostate

The `to_json` overloads in `detail/conversions/to_json.hpp` support
`std::variant` via `std::visit`, but `std::monostate` has no
serialization, causing a compile error when the variant's active type
is `monostate`. Add a `to_json` overload for `std::monostate` that
serializes it as JSON null.

## Medium

### M1: Add SAX-based streaming JSON parser with depth-limited events

Extend the SAX interface in `detail/input/json_sax.hpp` with a new
`filtered_sax` adapter that only emits events for nodes at or below
a specified JSON Pointer prefix. The adapter wraps any user-provided
SAX handler and suppresses events outside the filter path. Changes
span `json_sax.hpp` for the adapter class, `parser.hpp` for wiring
filtered SAX into `sax_parse()`, and `basic_json` for a convenience
`parse(input, json_pointer, sax)` overload.

### M2: Implement JSON Merge Patch diff generation

Add a `json::diff_merge_patch(source, target)` static method that
computes the RFC 7396 merge-patch document transforming `source` into
`target`. The implementation must handle nested objects, array
replacement, and null-means-delete semantics. Changes span
`basic_json` for the public API, `detail/json_pointer.hpp` for path
traversal utilities, and new logic alongside the existing `patch()`
and `diff()` methods.

### M3: Add CBOR diagnostic notation output

Implement `json::to_cbor_diagnostic()` that serializes a JSON value
into CBOR diagnostic notation (RFC 8949 §8) for debugging binary
payloads. The output must handle tags, indefinite-length containers,
byte strings with hex encoding, and special float values. Changes span
`detail/output/binary_writer.hpp` for the diagnostic output mode,
`detail/output/output_adapters.hpp` for a diagnostic string adapter,
and `basic_json` for the public entry point.

### M4: Implement custom allocator support for basic_json

Extend `basic_json` to propagate a user-provided allocator to all
internal containers (object map, array vector, string). The allocator
must be threaded through `ordered_map.hpp`, the internal
`json_value` union construction in `basic_json`, and all
`detail/conversions/from_json.hpp` overloads that construct temporary
containers. Add an `allocator_type` typedef and
`get_allocator()` method.

### M5: Add JSON Schema draft-07 validation

Implement `json::validate(instance, schema)` that validates a JSON
document against a JSON Schema (draft-07) definition. Support `type`,
`properties`, `required`, `items`, `enum`, `pattern`, `minimum`,
`maximum`, `allOf`, `anyOf`, `oneOf`, `not`, and `$ref` resolution.
Changes span a new `detail/schema/` directory with a `validator.hpp`,
modifications to `basic_json` for the public API, and
`detail/json_pointer.hpp` for `$ref` resolution.

### M6: Implement efficient binary search for sorted JSON arrays

Add `json::binary_search()`, `json::lower_bound()`, and
`json::upper_bound()` member functions for sorted JSON arrays that
perform O(log n) lookups using a user-provided comparator. The
functions must integrate with `iter_impl.hpp` for random-access
iterator support and validate that the array is actually sorted.
Changes span `basic_json`, `detail/iterators/iter_impl.hpp` for
random-access operations, and `detail/meta/type_traits.hpp` for
comparator concept checking.

### M7: Add BJData optimized container round-trip support

The BJData binary format supports optimized containers with `$` (type)
and `#` (count) markers for homogeneous arrays. Currently
`binary_reader.hpp` parses them but `binary_writer.hpp` always writes
the generic format. Implement optimized writing: detect homogeneous
arrays, emit type/count markers, and add a `use_bjdata_optimized`
option. Changes span `detail/output/binary_writer.hpp`,
`detail/input/binary_reader.hpp` for round-trip verification, and
`basic_json` for the output option.

### M8: Implement JSON Path (RFC 9535) query support

Add `json::json_path(expression)` that evaluates a JSONPath expression
against a JSON document and returns a `json` array of matching values.
Support root (`$`), child (`.key`), recursive descent (`..`),
wildcards (`*`), array slices (`[0:3]`), and filter expressions
(`[?(@.price < 10)]`). Changes span a new `detail/json_path.hpp`
for the expression parser and evaluator, `basic_json` for the public
method, and `detail/meta/type_traits.hpp` for expression type
detection.

### M9: Add copy-on-write semantics for large JSON values

Implement COW (copy-on-write) for JSON objects and arrays by wrapping
the internal storage in a reference-counted pointer. Copies share
data until mutation. Changes span `basic_json` for the internal
`json_value` union (replacing raw storage with COW pointers),
`detail/iterators/iter_impl.hpp` for detach-on-write in mutable
iterator access, and `detail/conversions/from_json.hpp` for
ensuring proper detach during deserialization into existing values.

### M10: Implement pretty-print customization with indentation callbacks

Add a `json::dump(callback)` overload that accepts an
`indent_callback_t` function invoked at each nesting level, allowing
custom formatting (e.g., aligned colons, compact small arrays, max
line width). Changes span `detail/output/serializer.hpp` for the
callback-driven formatting path, `detail/output/output_adapters.hpp`
for buffered output with rewind support, and `basic_json` for the
new `dump()` overload and callback type definition.

## Wide

### W1: Implement full JSON Schema draft-2020 validation with output formats

Build a complete JSON Schema validator supporting draft-2020-12
including `$dynamicRef`, `prefixItems`, `unevaluatedProperties`,
`unevaluatedItems`, vocabulary system, and format validation. Produce
structured output per the specification (flag, basic, detailed,
verbose formats). Requires a new `detail/schema/` directory with
vocabulary-aware resolver, validator engine, and output formatter,
modifications to `detail/json_pointer.hpp` for dynamic scope
resolution, `detail/exceptions.hpp` for validation error types,
and `basic_json` for the public validation API.

### W2: Add streaming parser with zero-copy string views

Replace the current DOM parser pipeline with an optional zero-copy
mode where string values are represented as `std::string_view` into
the original input buffer. Requires modifying `detail/input/lexer.hpp`
for view-based token emission, `detail/input/parser.hpp` for
lifetime-aware DOM construction, `basic_json` for a
`basic_json_view` variant with non-owning string storage,
`detail/conversions/from_json.hpp` for view-aware deserialization,
`detail/output/serializer.hpp` for serializing view-backed values,
and `detail/iterators/iter_impl.hpp` for iterator support over the
new type.

### W3: Implement parallel JSON processing with thread-safe accessors

Add thread-safe read access to `basic_json` via a `json::concurrent`
wrapper that uses shared mutexes for concurrent reads and exclusive
writes. Implement parallel `for_each()`, `transform()`, and
`find_all()` that partition large arrays/objects across threads.
Changes span `basic_json` for the concurrent wrapper API,
`detail/iterators/iter_impl.hpp` for partitioned iteration,
`detail/meta/type_traits.hpp` for executor concept detection,
`ordered_map.hpp` for thread-safe bucket access, and
`detail/output/serializer.hpp` for parallel sub-tree serialization.

### W4: Rewrite binary format layer as a pluggable codec framework

Refactor the monolithic `binary_reader.hpp` and `binary_writer.hpp`
into a codec plugin architecture where each binary format (CBOR,
MessagePack, UBJSON, BSON, BJData) is a self-contained codec class
implementing a common `binary_codec` interface. Add codec registration,
auto-detection from byte signatures, and per-codec options. Changes
span splitting `detail/input/binary_reader.hpp` into per-format
readers, splitting `detail/output/binary_writer.hpp` into per-format
writers, a new `detail/binary/codec_registry.hpp`, updates to
`basic_json` for codec-based API, and `detail/input/input_adapters.hpp`
for peek-ahead support needed by auto-detection.

### W5: Implement JSON-LD context processing and expansion

Add JSON-LD 1.1 context processing: `json::expand(context)`,
`json::compact(context)`, and `json::flatten()` per the W3C spec.
Support `@context` resolution (including remote via a pluggable
fetcher), term definitions, type coercion, `@id`/`@type` keywords,
and language maps. Requires a new `detail/jsonld/` directory with
context processor, expansion/compaction algorithms, `basic_json`
for the public API, `detail/json_pointer.hpp` for IRI-based
navigation, and `detail/conversions/to_json.hpp` for RDF literal
serialization.

### W6: Add compile-time JSON parsing with constexpr basic_json

Make `basic_json` usable in `constexpr` contexts for C++20 and later.
The lexer, parser, and value construction must all be `constexpr`-
capable, enabling compile-time JSON validation and embedding.
Changes span `detail/input/lexer.hpp` for constexpr tokenization,
`detail/input/parser.hpp` for constexpr recursive descent,
`basic_json` for constexpr constructors and accessors,
`detail/value_t.hpp` for constexpr comparison, `ordered_map.hpp` for
constexpr map operations, and `detail/exceptions.hpp` for constexpr-
friendly error reporting.

### W7: Implement JSON diff and patch with move detection and conflict resolution

Extend the existing `json::diff()` (RFC 6902) with move-operation
detection, copy-operation detection, and minimal patch generation.
Add three-way merge via `json::merge3(base, ours, theirs)` with
conflict markers for incompatible changes. Changes span `basic_json`
for the extended diff/merge API, `detail/json_pointer.hpp` for
longest-common-subsequence path analysis, a new
`detail/patch/diff_engine.hpp` for the optimized diff algorithm,
`detail/patch/merge3.hpp` for three-way merge logic, and
`detail/exceptions.hpp` for conflict-error types.

### W8: Implement memory-mapped large file JSON processing

Add support for parsing JSON files via memory-mapped I/O without
loading the entire file into memory. Implement a `mmap_input_adapter`
that integrates with the existing input adapter framework, a
lazy-loading DOM that materializes sub-trees on demand, and
range-based access for streaming over gigabyte-scale files. Changes
span `detail/input/input_adapters.hpp` for the mmap adapter,
`detail/input/parser.hpp` for incremental DOM construction,
`basic_json` for a `lazy_json` type with deferred materialization,
`detail/iterators/iter_impl.hpp` for lazy-aware iteration, and
`detail/output/serializer.hpp` for streaming serialization of
lazy nodes.

### W9: Add full Unicode normalization and collation for string comparison

Implement Unicode NFC/NFD/NFKC/NFKD normalization and
locale-sensitive collation for JSON string comparisons. Add
`json::normalize()` for in-place string normalization and a
`json::compare(a, b, collation)` function. Changes span
a new `detail/unicode/` directory with normalization tables and
algorithm, modifications to `detail/output/serializer.hpp` for
normalized output, `detail/input/lexer.hpp` for normalization during
parsing, `basic_json` for the comparison and normalization API,
and `detail/string_escape.hpp` for normalization-aware escaping.

### W10: Implement a JIT-compiled query engine for repeated JSON queries

Build a JIT-compiled query evaluator that compiles JSONPath or
JMESPath expressions into optimized native code for repeated
evaluation against different JSON documents. Support expression
caching, SIMD-accelerated string matching, and parallel evaluation.
Requires a new `detail/query/` directory with expression compiler
and evaluator, integration with `basic_json` for the query API,
`detail/meta/type_traits.hpp` for platform detection,
`detail/iterators/iter_impl.hpp` for compiled traversal operations,
and `detail/input/lexer.hpp` for reusing the tokenizer in expression
parsing.
