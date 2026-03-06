# fmtlib/fmt

| Field | Value |
|-------|-------|
| **URL** | https://github.com/fmtlib/fmt |
| **License** | MIT |
| **Language** | C++ |
| **Scale** | Small (focused library) |
| **Category** | Text formatting library |
| **Set** | ranker-gate |
| **Commit** | `696dd855fc82b582ad6da2e732a3c57aa3e56dff` |

## Why this repo

- **Single-purpose**: Fast, safe text formatting library for C++. The basis
  for C++20's `std::format`. Core formatting engine, compile-time format
  string checking, and output adapters. One developer can hold the full
  codebase in their head.
- **Well-structured**: Header-only core under `include/fmt/` with clear
  per-concern headers. Implementation in `src/` is minimal. Separation
  between core formatting, output targets (printf, ostream, std), and
  compile-time checking is clean.
- **Rich history**: 3K+ commits, 20K+ stars. Adopted into the C++ standard.
  PRs cover performance optimization, format spec compliance, and compiler
  compatibility.
- **Permissive**: MIT license.

## Structure overview

```
include/fmt/
├── core.h               # Core formatting engine, format_to, format
├── format.h             # Extended formatting facilities
├── format-inl.h         # Implementation details (out-of-line)
├── compile.h            # Compile-time format string parsing
├── ranges.h             # Range/container formatting
├── chrono.h             # Date/time formatting
├── color.h              # Terminal color formatting
├── os.h                 # OS-specific output (file, pipe)
├── ostream.h            # std::ostream integration
├── printf.h             # printf-compatible interface
├── std.h                # std:: type formatters
├── xchar.h              # Wide character support
└── args.h               # Dynamic argument handling

src/
├── format.cc            # Out-of-line implementation
└── os.cc                # OS-specific implementation
```

## Scale indicators

- ~20 header files + ~5 source files
- ~25K lines of code (headers are substantial)
- Flat structure (1 level)
- Zero dependencies (header-only core)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `ostream::print` in `os.h` not flushing on newline characters

The `ostream` class in `os.h` buffers output for performance but does
not flush when the formatted output contains newline characters, unlike
standard C `stdio` behavior with line-buffered streams. When writing
log lines via `fmt::output_file`, the output may not be visible until
the buffer fills or the file is explicitly closed. Add an optional
line-buffering mode that flushes after each newline.

### N2: Fix `printf.h` not rejecting invalid conversion specifiers

The `printf_formatter` in `printf.h` silently ignores unrecognized
conversion specifiers like `%q` or `%K` instead of reporting an error.
Standard printf implementations reject non-standard specifiers;
`printf_arg_formatter` should validate the conversion character against
the known set and produce a clear `format_error` for invalid ones.

### N3: Fix `fmt::join` not working with move-only range elements

`fmt::join(vec_of_unique_ptr, ", ")` fails to compile because `join`
tries to copy elements for formatting. Fix `join` to forward elements
by reference without copying, allowing move-only types to be formatted
in-place.

### N4: Fix `fmt::format` stack overflow with deeply recursive format args

When formatting a value whose `fmt::formatter` specialization recursively
calls `fmt::format` on nested data, the recursive calls overflow the stack
at ~100 levels deep. Add a recursion depth limit with a clear error.

### N5: Fix `compile.h` compiled format not supporting dynamic width arguments

`FMT_COMPILE("{:{}}")` with a dynamic width argument fails to compile
because the compile-time format string parser in `compile.h` does not
handle runtime width references. Only static widths are supported in
compiled format strings. Extend the compiled format handler to resolve
dynamic width and precision arguments at runtime.

### N6: Fix `ranges.h` tuple formatting with `const`-qualified pair element types

`fmt::format("{}", std::pair<const std::string, int>{"key", 42})`
fails to compile because the tuple formatter in `ranges.h` applies
`remove_cvref_t` inconsistently, leaving `const` qualifiers on
pair element types that prevent formatter lookup. Normalize element
types before looking up formatters in the tuple formatting path.

### N7: Fix `args.h` `dynamic_format_arg_store` dangling reference for string_view

When adding `string_view` arguments to `dynamic_format_arg_store` in
`args.h`, the stored argument references external string data without
extending its lifetime. If the original string is destroyed before
formatting, the stored argument becomes a dangling reference. Add
internal string storage to `dynamic_format_arg_store` for `string_view`
arguments to ensure the data outlives the store.

### N8: Fix `FMT_ENFORCE_COMPILE_STRING` not catching all runtime strings

When `FMT_ENFORCE_COMPILE_STRING` is defined, some overloads still
accept runtime strings because the enforcement check doesn't cover
`fmt::vformat` and `fmt::vprint`. Extend the check to all entry points.

### N9: Fix error message for out-of-range positional argument index

When `fmt::format("{5}", 42)` raises an error for an out-of-range
argument index, the error message says "argument not found" without
indicating how many arguments were actually provided. Improve the
error reporting in `base.h` `format_string` validation to include
the valid argument range (e.g., "argument index 5 out of range,
only 1 argument provided").

### N10: Fix `chrono.h` `{:%H:%M:%S}` producing incorrect output for negative durations

When formatting a negative `std::chrono::duration` with `{:%H:%M:%S}`,
the negative sign is applied only to the hours component, producing
`"-1:00:05"` instead of correctly representing -5 seconds as
`"-0:00:05"`. The sign handling in `chrono.h` `tm_writer` does not
correctly distribute the negative across all time components.

## Medium

### M1: Implement configurable truncation indicator for `format_to_n`

When `format_to_n` truncates output, there is no way to signal
truncation to the reader. Add a `format_to_n_with_suffix` variant in
`base.h` that accepts an optional truncation suffix (e.g., `"..."`)
which replaces the last characters when output exceeds the buffer.
Requires modifying the output counting logic in `format.h` and the
`format_to_n_result` struct in `base.h`.

### M2: Add runtime format string caching for repeated formatting

When the same format string is used repeatedly (e.g., in a logging
loop), the format string is re-parsed on every call. Implement
`fmt::runtime_format_cache` that parses a runtime format string once
into a reusable compiled representation. Changes span `base.h` (parsed
format representation), `format.h` (cached format dispatch), and
`format-inl.h` (parse result storage).

### M3: Implement compile-time format string checking improvements

Improve compile-time format string validation to catch more errors:
type mismatch between format specifier and argument type, width/precision
specifiers on types that don't support them, and invalid format spec
combinations. Produce clear `static_assert` messages that identify the
problematic argument position and explain the issue.

### M4: Implement custom format specification parsing for user types

Add a mechanism for user-defined `fmt::formatter` specializations to
define custom format spec syntax beyond the standard mini-language. The
parser should support a `parse_custom_specs()` helper that parses
additional specifiers after the standard width/precision/fill handling.

### M5: Implement `fmt::format` support for `std::source_location` formatting

Add a `formatter<std::source_location>` specialization in `std.h` that
formats a source location as `"file.cpp:42:function_name"`. Support
custom format specs to control which components are included: `{:f}`
for file only, `{:l}` for file:line, `{:c}` for file:line:column,
and default for file:line:function. Add supporting presentation type
handling in `format.h`.

### M6: Implement `fmt::format` support for `std::stacktrace` formatting

Add `formatter<std::stacktrace>` and `formatter<std::stacktrace_entry>`
specializations in `std.h` that format stack traces with configurable
verbosity. Support `{:s}` for short (function names only), `{:f}` for
full (file:line + function), and `{:n}` for numbered frames. Add
depth-limiting and frame filtering support. Changes span `std.h` and
`format.h`.

### M7: Add XML/HTML entity escaping mode for format output

Implement `fmt::xml_escape(value)` wrapper that escapes `<`, `>`, `&`,
`"`, `'` characters in the formatted output for safe embedding in XML
or HTML. Add corresponding `fmt::html_escape(value)` alias. Changes
span `format.h` (escape wrapper and character escaping logic), `base.h`
(escaped value type registration), and `color.h` (interaction with
styled output).

### M8: Implement format string syntax highlighting for diagnostics

When a format string has a syntax error, show the error location within
the string with a caret pointer and a colored highlight of the
problematic specifier. Support terminal color output for `static_assert`
messages (via ANSI codes in the error text for terminals that support it).

### M9: Implement `fmt::to_string` fast path for common integral types

When calling `fmt::to_string(integer)`, the current code path allocates
a `memory_buffer` and goes through the full format machinery including
spec parsing. Add an optimized fast path in `format.h` for small
integers that uses a stack-allocated buffer with direct digit
generation, bypassing the format spec parsing in `base.h`. Support
`int`, `long`, `unsigned`, and `long long` with the fast path.

### M10: Implement zero-allocation formatting for embedded environments

Add a `fmt::format_to_n` variant that guarantees zero heap allocations.
Use a fixed-size stack buffer and truncate instead of growing. Support
compile-time buffer size selection based on the format string and
argument types. Emit a clear compile-time error if the buffer is
provably too small.

## Wide

### W1: Implement format string extension system for domain-specific sublanguages

Add a plugin architecture where domain-specific formatters register
custom format syntax via type prefixes (e.g., `{:sql:...}`,
`{:json:...}`) that dispatch to registered formatter extensions.
Changes span the format string parser in `base.h`, formatter dispatch
in `format.h`, compile-time validation in `compile.h`, the runtime
format system in `format-inl.h`, and add a registration module in a
new `ext.h` header.

### W2: Add memory-mapped file output with concurrent formatting

Implement `fmt::mmap_output(path, size_hint)` that writes formatted
output directly to a memory-mapped file, eliminating double-buffering.
Support dynamically growing the mapping, concurrent writes from
multiple threads using partitioned regions, and crash-safe atomic
commit via rename. Changes span `os.h` (memory mapping and file
management), `format.h` (mmap-aware output iterator), the buffer
system in `base.h`, and platform-specific implementation in `os.cc`.

### W3: Implement a `fmt::print` interceptor system for testing

Add a testing framework for fmt: intercept all `fmt::print` calls in
a scope, capture the formatted output, and provide assertion helpers
(`EXPECT_PRINTED("text")`, `EXPECT_PRINTED_MATCHES(regex)`). Support
both stdout and stderr capture. Requires a global output registry,
thread-safe capture buffers, and scope-based RAII guards. Changes span
fmt::print, fmt::vprint, the output.cc implementation, and add a
testing header.

### W4: Add structured logging built on fmt

Implement `fmt::log` as a lightweight structured logging library:
log levels (trace/debug/info/warn/error/fatal), structured key-value
fields, multiple sinks (console, file, syslog), log rotation, async
logging thread, and compile-time level filtering. The API should
compose naturally with fmt format strings. Changes span format
helpers, output infrastructure, add a logging module with sink
abstraction and async writer.

### W5: Implement comprehensive Unicode text processing

Add Unicode-aware text operations built on fmt's character handling:
grapheme cluster segmentation (UAX #29), word boundary detection,
line breaking algorithm (UAX #14), bidirectional text support (UAX #9),
and Unicode normalization (NFC/NFD). Update all width calculations and
alignment operations to use grapheme-aware widths. Changes span
core.h, format.h, the width calculation system, and add a Unicode
support module.

### W6: Add fmt as a C++20 `std::format` compatibility layer

Implement a mode where fmt acts as a polyfill for `std::format` on
compilers that don't support it yet, and passes through to `std::format`
when available. Detect compiler support at build time. Provide the
same API as `std::format`/`std::format_to`/`std::vformat`. Handle
divergences between fmt and std::format semantics. Changes span the
format core, compile-time parsing, argument handling, and build system.

### W7: Implement format string localization system

Add `fmt::locale_format(locale, fmt_str, args...)` that selects
format strings from a locale-specific message catalog. Support ICU
MessageFormat-compatible patterns for pluralization and gender. Load
translations from PO/MO files or JSON. Support fallback chains
(fr-CA → fr → en). Changes span format parsing, argument binding,
add a locale module, message catalog loader, and plural rule engine.

### W8: Add serialization framework built on fmt formatters

Implement `fmt::serialize(value)` and `fmt::deserialize<T>(str)` that
use fmt's formatter infrastructure for bidirectional text
serialization. Formatter specializations that implement `parse()` can
opt-in to deserialization via `parse_value()`. Support structured
formats (JSON, TOML) by composing formatters. Changes span the
formatter trait, parsing infrastructure, add serialization headers
and format-specific composers.

### W9: Implement format string static analysis tool

Add a standalone tool (`fmt-check`) that analyzes C++ source files for
format string issues: type mismatches, unused arguments, format
specifiers incompatible with argument types, performance anti-patterns
(unnecessary allocations), and deprecated specifier usage. Output
diagnostics in compiler-compatible format. Requires format string
extraction from source code, type inference from call sites, and
diagnostic formatting.

### W10: Add compile-time format string composition

Implement `FMT_COMPILE` extensions for composing format strings from
fragments at compile time: `constexpr auto fmt = FMT_COMPILE("{}") +
FMT_COMPILE(" items")`. Support conditional fragments and repeated
patterns. The composed format string should be compiled into a single
optimized formatting function. Changes span the compile-time parser,
code generator, type system, and add composition primitives.
