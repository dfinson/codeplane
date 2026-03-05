# fmtlib/fmt

| Field | Value |
|-------|-------|
| **URL** | https://github.com/fmtlib/fmt |
| **License** | MIT |
| **Language** | C++ |
| **Scale** | Small (focused library) |
| **Category** | Text formatting library |

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

### N1: Fix `fmt::format` compile error with `std::optional<std::string>`

`fmt::format("{}", std::optional<std::string>("hello"))` fails to
compile because `fmt::formatter<std::optional<T>>` is not specialized
by default. Add a formatter for `std::optional<T>` that formats the
contained value when present and `"none"` (or a configurable string)
when empty.

### N2: Add `%b` format specifier for binary integer output

The library supports `{:x}` (hex), `{:o}` (octal), and `{:d}` (decimal)
but not `{:b}` for binary representation. Add the `b`/`B` format
specifier for integral types that outputs the value in binary. Support
the `#` flag for `0b` prefix. Match the C++23 `std::format` specification.

### N3: Fix `fmt::join` not working with move-only range elements

`fmt::join(vec_of_unique_ptr, ", ")` fails to compile because `join`
tries to copy elements for formatting. Fix `join` to forward elements
by reference without copying, allowing move-only types to be formatted
in-place.

### N4: Fix `fmt::format` stack overflow with deeply recursive format args

When formatting a value whose `fmt::formatter` specialization recursively
calls `fmt::format` on nested data, the recursive calls overflow the stack
at ~100 levels deep. Add a recursion depth limit with a clear error.

### N5: Fix `{:>20}` alignment producing wrong width for multi-byte UTF-8 chars

Right-aligning text that contains multi-byte UTF-8 characters (e.g., emoji)
produces incorrect padding because the width calculation counts bytes
instead of display columns. Fix the width calculation to use Unicode
East Asian Width properties.

### N6: Add `{:?}` debug format for standard library container types

Implement a debug format specifier for `std::vector`, `std::map`,
`std::set`, and `std::pair` that produces Python-like debug output
(e.g., `[1, 2, 3]` for vectors, `{"a": 1}` for maps).

### N7: Fix `fmt::join` with empty range producing trailing separator

When using `fmt::join` on an empty iterable, the output contains a
trailing separator instead of being empty. The join implementation
emits the separator before checking for end-of-range. Fix the loop
condition.

### N8: Fix `FMT_ENFORCE_COMPILE_STRING` not catching all runtime strings

When `FMT_ENFORCE_COMPILE_STRING` is defined, some overloads still
accept runtime strings because the enforcement check doesn't cover
`fmt::vformat` and `fmt::vprint`. Extend the check to all entry points.

### N9: Add `{:c}` format specifier for character output of integers

Add a `c` format specifier that interprets an integer as a character
and outputs the corresponding Unicode codepoint, including multi-byte
UTF-8 encoding for values > 127.

### N10: Fix chrono formatting producing wrong month name for `std::chrono::year_month`

When formatting `std::chrono::year_month` with `{:%B}`, the month name
is offset by one (January shows as February). The month value extraction
does not account for `std::chrono::month` being 1-indexed vs the
internal 0-indexed lookup table.

## Medium

### M1: Implement locale-aware number formatting

Add locale-aware formatting via the `L` specifier: `fmt::format(loc, "{:L}", 1234567)` → `"1,234,567"` (US) or `"1.234.567"` (DE). Support
thousands separators for integers, decimal separators for floats,
and currency formatting. Use `std::locale` or a custom locale
abstraction. Include common locale definitions as compile-time
constants.

### M2: Add color and style formatting for terminal output

Implement rich terminal formatting: `fmt::print(fg(color::red) | bold, "Error: {}", msg)`. Support 4-bit, 8-bit, and 24-bit (true color)
terminal colors. Add named colors, RGB/HSL color specification, and
style modifiers (bold, italic, underline, strikethrough). Auto-detect
terminal capabilities. Support style composition and nesting. Add
`fmt::styled(value, style)` for inline styling within format strings.

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

### M5: Add buffered file output with configurable buffer size

Implement `fmt::buffered_output_file` that wraps file I/O with a
configurable write buffer. Batching small `fmt::print` calls into
larger writes for file output. Support automatic flushing on newline
and manual flush control. Include an RAII guard that flushes on
destruction.

### M6: Implement named argument support with compile-time validation

Extend `fmt::format` to support named arguments: `fmt::format("{name}
is {age}", fmt::arg("name", "Alice"), fmt::arg("age", 30))`. Validate
at compile time that all referenced names have corresponding `arg()`
calls. Support mixing positional and named args.

### M7: Add `fmt::group_digits` for number formatting with separators

Implement a digit grouping wrapper: `fmt::format("{}", fmt::group_digits(1000000))` → `"1,000,000"`. Support configurable separator
character and group size. Support both integral and floating-point types.
Default to locale-independent formatting (comma separator, groups of 3).

### M8: Implement format string syntax highlighting for diagnostics

When a format string has a syntax error, show the error location within
the string with a caret pointer and a colored highlight of the
problematic specifier. Support terminal color output for `static_assert`
messages (via ANSI codes in the error text for terminals that support it).

### M9: Add `fmt::styled` for applying multiple styles to a single value

Implement `fmt::styled(value, fg(red) | bg(blue) | bold | underline)`
that applies multiple terminal styles atomically. Support style
composition via operator overloading. Auto-detect whether the output
supports ANSI color codes. Include a `text_style` builder pattern.

### M10: Implement zero-allocation formatting for embedded environments

Add a `fmt::format_to_n` variant that guarantees zero heap allocations.
Use a fixed-size stack buffer and truncate instead of growing. Support
compile-time buffer size selection based on the format string and
argument types. Emit a clear compile-time error if the buffer is
provably too small.

## Wide

### W1: Add structured logging backend

Implement `fmt::log` as a lightweight structured logging library
built on fmt. Support log levels (trace, debug, info, warn, error,
fatal), structured fields (`fmt::log::info("request completed", "status"_a=200, "duration_ms"_a=42)`), configurable sinks (console,
file, syslog), log rotation, async logging with a dedicated thread,
and compile-time log level filtering. The API should compose
naturally with existing fmt format strings.

### W2: Implement Unicode-aware text formatting

Add comprehensive Unicode support: proper text width calculation
(accounting for East Asian wide characters, combining characters,
zero-width joiners), word-wrapping at Unicode word boundaries (UAX #29),
bidirectional text handling for mixed LTR/RTL content, Unicode
normalization (NFC/NFD), and grapheme cluster-aware truncation.
Update the width calculation used by alignment specifiers (`{:<20}`)
to use Unicode text width instead of byte count.

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
