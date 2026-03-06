# simdjson/simdjson

| Field | Value |
|-------|-------|
| **URL** | https://github.com/simdjson/simdjson |
| **License** | Apache-2.0 |
| **Language** | C++ |
| **Scale** | Medium |
| **Category** | JSON parser |
| **Set** | Cutoff |
| **Commit** | `262ddad0370cdfa656b61c388c52bad02697f8a1` |

## Why this repo

- **Performance-oriented**: SIMD-accelerated JSON parsing with
  architecture-specific backends (haswell, arm64, icelake, westmere,
  fallback) and two API layers — DOM (full materialization) and On
  Demand (lazy streaming). A single developer can trace the full parse
  pipeline from `padded_string` through stage 1 (structural indexing)
  to stage 2 (tape/on-demand value access).
- **Rich history**: 19K+ stars, 1K+ forks. One of the most cited
  high-performance JSON libraries in C++. PRs cover SIMD
  optimizations, compliance fixes, and API ergonomics.
- **Permissive**: Apache-2.0 license.

## Structure overview

```
include/simdjson/
├── simdjson.h                  # Top-level umbrella include
├── dom/                        # DOM API — parser, document, element, array, object
│   ├── parser.h                # dom::parser — parse to full document tree
│   ├── document.h              # dom::document — owns the tape
│   ├── element.h               # dom::element — typed value accessor
│   ├── array.h                 # dom::array — iterable JSON array
│   ├── object.h                # dom::object — iterable JSON object
│   ├── serialization.h         # to_string / minify for DOM elements
│   ├── document_stream.h       # Streaming NDJSON parser
│   └── fractured_json.h        # Fractured (chunked) JSON support
├── generic/ondemand/           # On Demand API — lazy, streaming parser
│   ├── parser.h                # ondemand::parser
│   ├── document.h              # ondemand::document — root value
│   ├── value.h                 # ondemand::value — lazy typed access
│   ├── array.h / object.h      # Lazy iteration
│   ├── field.h                 # Key-value pair in objects
│   ├── json_type.h             # Runtime type detection
│   ├── document_stream.h       # Streaming NDJSON for on-demand
│   └── deserialize.h           # Structured deserialization
├── error.h                     # Error codes and simdjson_result<T>
├── padded_string.h             # SIMD-safe input buffer
├── implementation.h            # Runtime CPU dispatch
├── minify.h                    # JSON minification
├── jsonpathutil.h              # JSON Pointer utilities
├── internal/
│   ├── tape_ref.h              # Tape navigation helpers
│   ├── tape_type.h             # Tape token types
│   └── dom_parser_implementation.h  # Per-arch parser backend interface
├── haswell/ arm64/ icelake/ … # Per-architecture SIMD implementations
│   ├── simd.h                  # SIMD register wrappers
│   ├── bitmanipulation.h       # Bit-level helpers
│   ├── numberparsing_defs.h    # Number parsing SIMD specializations
│   └── stringparsing_defs.h    # String parsing SIMD specializations
src/
├── simdjson.cpp                # Unified compilation unit
├── implementation.cpp          # CPU detection and dispatch
├── haswell.cpp / arm64.cpp / … # Per-arch compilation units
├── from_chars.cpp              # Number parsing (strtod alternative)
└── to_chars.cpp                # Number formatting
```

## Scale indicators

- ~170 header files across `include/simdjson/`
- ~44K lines of code (headers + src)
- Multi-level hierarchy: DOM API, On Demand API, per-arch backends
- Zero external runtime dependencies

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add human-readable description to error_code string conversion

The `error_message()` function in `error.h` returns terse codes like
`TAPE_ERROR` or `MEMALLOC`. Add a `error_description()` companion
that returns a longer human-readable explanation (e.g., "The JSON
document has an improper structure: missing or superfluous commas,
braces, or brackets") for each `error_code` enum value, usable in
diagnostic output without requiring users to consult documentation.

### N2: Fix padded_string move constructor not zeroing source capacity

The `padded_string` move constructor in `padded_string.h` transfers
ownership of the internal buffer but does not reset the source
object's `viable_size` to zero. If the moved-from object is later
queried for `size()`, it returns a stale value pointing to
deallocated memory. Zero both `viable_size` and `data_ptr` in the
moved-from object after transfer.

### N3: Add JSON Pointer escape validation to at_pointer()

The `at_pointer()` method on `dom::element` and `ondemand::value`
accepts RFC 6901 JSON Pointer strings but does not validate that `~`
characters are followed by `0` or `1` as required by the spec. A
malformed pointer like `"/foo~2/bar"` silently fails or produces
unexpected results. Add validation in `jsonpathutil.h` that returns
`INVALID_JSON_POINTER` for improperly escaped `~` sequences.

### N4: Add byte-offset reporting to parse errors

When parsing fails, the `error_code` returned by `dom::parser::parse()`
or `ondemand::parser::iterate()` does not include the byte offset where
the error was detected. Add an `error_location()` method to parser
classes that returns the byte offset within the input buffer where the
last error occurred, enabling callers to produce "error at byte 1234"
diagnostics.

### N5: Fix minify() not handling surrogate pairs in string values

The `minify()` function copies string content verbatim without
validating UTF-16 surrogate pair escape sequences (`\uD800\uDC00`).
When a JSON string contains an unpaired surrogate (`\uD800` alone),
`minify()` emits it unchanged, producing invalid JSON. Validate
surrogate pair completeness during minification in `minify.h` and
return `UTF8_ERROR` for unpaired surrogates.

### N6: Add capacity() accessor to ondemand::parser

The `ondemand::parser` allocates internal buffers sized to the input
document but does not expose the current buffer capacity. Add a
`capacity()` method that returns the maximum document size (in bytes)
the parser can handle without reallocation, matching the existing
`dom::parser::capacity()` API for consistency.

### N7: Fix document_stream skipping documents on trailing whitespace

The `dom::document_stream` (NDJSON streaming) parser counts documents
by scanning for non-whitespace starts. When a document is followed by
excessive trailing whitespace (e.g., `\n\n\n`), the stream may
incorrectly count or skip the next document. Fix the document boundary
detection in `document_stream-inl.h` to properly consume inter-document
whitespace without double-counting.

### N8: Add compile-time JSON path validation

The `compile_time_json.h` header supports constexpr JSON parsing but
does not validate JSON Pointer paths at compile time. Add a
`consteval` overload of `at_pointer()` that validates the pointer
syntax during compilation and produces a `static_assert` failure for
malformed pointers, improving developer feedback for hard-coded paths.

### N9: Fix from_chars not reporting overflow for uint64 values

The `from_chars.cpp` number parser handles signed 64-bit integers but
does not correctly detect overflow for unsigned 64-bit values exceeding
`UINT64_MAX`. Values like `18446744073709551616` (2^64) are silently
truncated instead of returning `NUMBER_OUT_OF_RANGE`. Add overflow
detection for the unsigned parsing path.

### N10: Add element count accessor to dom::array without full iteration

The `dom::array` class supports iteration but provides no `size()`
method to retrieve the element count without iterating. The underlying
tape already encodes the array's span. Add a `size()` method to
`dom::array` that computes the element count from the tape structure
in O(n) time, and document that it requires traversal.

## Medium

### M1: Implement JSON Patch (RFC 6902) support for dom::document

Add a `json_patch()` method that applies an array of patch operations
(`add`, `remove`, `replace`, `move`, `copy`, `test`) to a
`dom::document`. Requires mutable access to the document tape,
JSON Pointer resolution for target paths, value comparison for `test`
operations, and proper error reporting for failed patches. Touches
`dom/document.h`, `dom/element.h`, and `jsonpathutil.h`.

### M2: Add JSON Schema validation for on-demand documents

Implement a `schema_validator` class that validates an
`ondemand::document` against a JSON Schema (draft 2020-12 subset:
`type`, `required`, `properties`, `items`, `minLength`, `maxLength`,
`minimum`, `maximum`, `pattern`, `enum`). The validator should stream
through the document without full materialization, collect all
violations into a result set, and report JSON Pointer paths for each
error.

### M3: Implement pretty-print serialization with configurable indentation

The existing `to_string()` in `dom/serialization.h` produces compact
JSON. Add a `to_pretty_string()` that formats with configurable
indentation (spaces or tabs), line breaks after each key-value pair and
array element, and optional trailing commas. Requires a new formatting
state machine alongside the existing serialization walker and
integration with both `dom::element` and the minify pipeline.

### M4: Add SAX-style event callback interface for streaming parsing

Implement a `sax_parser` that emits callbacks (`on_object_start`,
`on_key`, `on_value`, `on_array_start`, `on_object_end`,
`on_array_end`) as it processes JSON, without materializing a DOM or
on-demand document. The parser should reuse the existing stage-1
structural indexing and provide a builder that routes events through a
user-supplied handler interface. Touches stage-1 output, a new event
dispatcher, and the implementation dispatch layer.

### M5: Implement JSON Merge Patch (RFC 7396) for dom::document

Add a `json_merge_patch()` function that takes a `dom::document` and a
patch document, producing a new document with the merge applied.
Requires recursive object merging (patch keys overwrite target keys,
`null` values delete keys), array replacement semantics, and
construction of a new tape representing the merged result. Touches
`dom/document.h`, `dom/object.h`, and the tape builder.

### M6: Add configurable depth limit to parsers

Both `dom::parser` and `ondemand::parser` use a hard-coded maximum
nesting depth (`DEFAULT_MAX_DEPTH`). Add a configurable `max_depth`
parameter to parser constructors that is validated during stage-2
parsing, returns `DEPTH_ERROR` when exceeded, and is queryable via
`parser.max_depth()`. Requires changes to parser initialization,
the per-architecture stage-2 implementations, and the on-demand
iterator's depth tracking.

### M7: Implement JSON diff between two dom::documents

Add a `json_diff()` function that compares two `dom::document` trees
and returns a list of differences as JSON Patch operations (RFC 6902).
Requires recursive comparison of elements by type, key-ordered object
comparison, array element alignment (LCS-based), and construction of
the patch array. Touches `dom/element.h`, `dom/object.h`,
`dom/array.h`, and a new diff module.

### M8: Add document statistics collection during parsing

Implement a `document_stats` structure that collects metrics during
parsing: total key count, max nesting depth, string byte total,
number count, boolean/null counts, and array/object counts. Wire it
into the stage-2 tape builder so stats are accumulated without a
separate pass. Touches the tape builder in each architecture backend
and the parser result type.

### M9: Implement selective field extraction with compiled field sets

Add a `field_set` class that pre-compiles a list of field names into
a lookup structure. During on-demand parsing, only matching fields
are materialized; others are skipped efficiently. Requires integration
with `ondemand::object` iteration, hash-based field matching, and a
builder API (`field_set::compile({"name", "age", "email"})`). Touches
the on-demand object iterator and the parser.

### M10: Add NDJSON writer for dom::element sequences

The library parses NDJSON via `document_stream` but provides no writer.
Add a `ndjson_writer` class that serializes a sequence of
`dom::element` values as newline-delimited JSON, with configurable
separator (newline vs. record separator), output buffering, and
streaming write to an `std::ostream` or callback. Touches
`dom/serialization.h` and a new writer module.

## Wide

### W1: Implement a mutable DOM with in-place modification

The current `dom::document` is immutable after parsing — the tape
cannot be modified. Implement a `mutable_document` class that allows
inserting, deleting, and updating keys and values in-place. Requires a
new backing store (not tape-based), conversion between immutable and
mutable representations, JSON Pointer-based mutation API, and
serialization back to JSON. Changes span a new mutable DOM module,
the element/object/array interfaces, serialization, and JSON Pointer
utilities.

### W2: Add full JSON Schema draft 2020-12 validation with reporting

Implement comprehensive JSON Schema validation covering `$ref`,
`$defs`, `allOf`, `anyOf`, `oneOf`, `not`, `if/then/else`,
`additionalProperties`, `patternProperties`, `dependentRequired`,
`uniqueItems`, `const`, `format`, and annotation collection. Include
a schema compiler that pre-processes schemas into an optimized
validation plan, a validation context that collects all errors with
JSON Pointer paths, and integration with both DOM and on-demand parsers.
Touches a new schema module, the parser interfaces, error reporting,
and JSON Pointer resolution.

### W3: Implement binary JSON (BSON/MessagePack/CBOR) codec adapters

Add encoder/decoder adapters that convert between simdjson's DOM
representation and binary JSON formats (BSON, MessagePack, CBOR).
Each adapter should handle type mapping (e.g., BSON ObjectId has no
JSON equivalent — encode as string), round-trip fidelity testing, and
streaming encode/decode for large documents. Changes span new codec
modules, the DOM element type system, serialization, error handling,
and the padded_string input layer.

### W4: Add multi-threaded parallel parsing for large documents

Implement a parallel parsing mode that splits a large JSON document
across multiple threads. Stage 1 (structural indexing) runs on
parallel chunks, results are merged, and stage 2 processes the unified
structural index. Requires chunk boundary detection (avoiding splits
inside strings), thread pool integration, lock-free result merging,
and fallback to single-threaded mode for small documents. Changes span
the stage-1 implementation across all architectures, the parser
orchestration layer, and the document construction path.

### W5: Implement JSONPath query engine

Add a `jsonpath` module that compiles JSONPath expressions (e.g.,
`$.store.book[?(@.price < 10)].title`) into an executable query plan.
Support bracket notation, recursive descent (`..`), array slicing,
filter expressions with comparison operators, and wildcard selectors.
The query should work on both DOM and on-demand documents, returning
result sets. Changes span a new JSONPath parser, a query executor, DOM
and on-demand integration, result set construction, and error handling.

### W6: Add streaming JSON builder for output generation

Implement a `json_builder` API for constructing JSON output
incrementally: `builder.start_object()`, `builder.key("name")`,
`builder.value("Alice")`, `builder.end_object()`. Support pretty-
printing, automatic comma insertion, depth tracking with validation,
streaming to `std::ostream` or callback, and integration with DOM
elements for hybrid construction. Changes span a new builder module,
serialization integration, error handling, and the padded_string
output layer.

### W7: Implement JSON-to-CSV and JSON-to-XML converters

Add converter modules that transform JSON documents to CSV (for
arrays of flat objects) and XML. CSV conversion should handle nested
objects via column flattening, configurable delimiters, and header
generation. XML conversion should handle attribute mapping, namespace
support, and text content. Changes span new converter modules, DOM
traversal, type mapping, output formatting, and error handling for
unconvertible structures.

### W8: Add comprehensive fuzzing harness with coverage-guided targets

Expand the existing fuzz targets in `fuzz/` to cover: on-demand
parsing with field access patterns, JSON Pointer traversal, NDJSON
streaming, number parsing edge cases, deeply nested structures, and
unicode string processing. Add a fuzzing driver that integrates with
libFuzzer and AFL, corpus management, and crash reproduction scripts.
Changes span the fuzz directory, test infrastructure, build system
(CMakeLists.txt), and CI configuration.

### W9: Implement language binding generator for Python and Rust

Add a binding generation layer that exposes simdjson's DOM and
on-demand APIs to Python (via pybind11) and Rust (via cxx).
Include type-safe wrappers for parser, document, element, and
iterator types; memory management that respects the padded_string
lifetime; error translation to language-native exceptions/Results;
and build system integration. Changes span new binding modules, CMake
configuration, Python/Rust project scaffolding, and documentation.

### W10: Add benchmark suite with regression detection

Implement a comprehensive benchmark suite that measures: parse
throughput (MB/s) across document sizes, on-demand vs DOM comparison,
per-architecture performance, number parsing accuracy, string parsing
with unicode, and NDJSON streaming. Add a CI-integrated regression
detector that compares against baseline measurements and fails the
build on significant regressions. Changes span the benchmark directory,
CI configuration, result storage, statistical comparison, and
reporting.
