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

8 tasks (3 narrow, 3 medium, 2 wide) for the C++ text formatting library.

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
