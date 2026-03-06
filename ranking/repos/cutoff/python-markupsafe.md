# pallets/markupsafe

| Field | Value |
|-------|-------|
| **URL** | https://github.com/pallets/markupsafe |
| **License** | BSD-3-Clause |
| **Language** | Python |
| **Scale** | Small |
| **Category** | HTML escaping |
| **Set** | Cutoff |
| **Commit** | `b2e4d9c7687be25695fffbe93a37622302b24fb1` |

## Why this repo

- **Minimal and focused**: Entire library is ~380 lines in
  `__init__.py` plus a C extension (`_speedups.c`, ~200 lines) and a
  pure-Python fallback (`_native.py`, 8 lines). The `Markup` class
  subclasses `str` and overrides every mutating method to preserve
  HTML safety. The `escape()` function replaces `&<>'"` with HTML
  entities. Dead-simple call chain from public API to internal
  implementation.
- **Widely depended upon**: Jinja2, Flask, Werkzeug, and thousands
  of other projects depend on MarkupSafe. 5K+ stars.
- **Permissive**: BSD-3-Clause license.
- **Scale anchor**: Intentionally tiny to anchor the "Small" end of
  scale assessment.

## Structure overview

```
src/markupsafe/
├── __init__.py      # Public API: escape(), escape_silent(), soft_str(),
│                    #   Markup class (str subclass), EscapeFormatter,
│                    #   _MarkupEscapeHelper
├── _native.py       # Pure-Python _escape_inner() — chain of str.replace()
├── _speedups.c      # C extension _escape_inner() — per-UCS-kind optimized
├── _speedups.pyi    # Type stub for the C extension
└── py.typed         # PEP 561 marker
tests/
└── test_markupsafe.py  # Pytest suite
bench.py             # Benchmark script
```

## Scale indicators

- 3 Python source files + 1 C extension
- ~587 total lines of code
- Flat structure (single package, no subpackages)
- Zero runtime dependencies

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add backtick escaping to _escape_inner

The `_escape_inner()` function (both `_native.py` and `_speedups.c`)
escapes `& < > ' "` but not the backtick character (`` ` ``), which
can be exploited in older browsers (IE quirks mode) for attribute
injection. Add backtick replacement (`\`` → `&#96;`) to both the pure-
Python and C implementations, maintaining the existing replacement
chain order.

### N2: Fix Markup.__mod__ not escaping tuple elements recursively

The `__mod__` method wraps format arguments in `_MarkupEscapeHelper`,
which intercepts `__str__` and `__repr__` to escape values. However,
when a tuple contains nested tuples (e.g., `Markup("%s") % ((x,),)`),
the inner tuple's elements are not wrapped, so `str()` on the inner
tuple bypasses escaping. Fix `_MarkupEscapeHelper.__getitem__` to
recursively wrap nested sequences.

### N3: Add Markup.join() with automatic escaping of non-Markup items

The inherited `str.join()` method does not escape items before joining.
`Markup(", ").join(["<b>", safe])` produces `<b>, safe` without
escaping the first element. Override `join()` in the `Markup` class to
call `self.escape()` on each item that is not already a `Markup`
instance before delegating to `super().join()`.

### N4: Fix escape() not handling objects with __html__ returning non-str

The `escape()` function calls `s.__html__()` and wraps the result in
`Markup()`, but does not verify that `__html__()` returns a `str`. If
an object's `__html__()` returns `None` or an `int`, `Markup(None)`
produces `"None"` which is incorrect. Add a type check after
`__html__()` and raise `TypeError` if the return value is not a `str`.

### N5: Add Markup.striptags() method for removing HTML tags

The `Markup` class provides escaping but no way to strip HTML tags
from a markup string. Add a `striptags()` method that removes all
HTML/XML tags (using a simple regex or state machine), collapses
whitespace, and returns a plain `str` (not `Markup`), useful for
generating plain-text versions of HTML content.

### N6: Fix EscapeFormatter not handling !r and !s conversion flags

The `EscapeFormatter.format_field()` method handles `__html_format__`
and `__html__` but does not account for Python's `!r` and `!s`
conversion flags applied before `format_field()` is called. When using
`Markup("{0!r}").format("<script>")`, the repr quotes are included but
the inner content may not be properly escaped. Ensure the `!r` output
is escaped through `self.escape()`.

### N7: Add Markup.unescape() method for reversing HTML entity encoding

There is no method to convert HTML entities back to their original
characters. Add `Markup.unescape()` that converts `&amp;`, `&lt;`,
`&gt;`, `&#39;`, and `&#34;` (and numeric character references) back
to their literal characters, returning a plain `str`. Use
`html.unescape()` from the standard library internally.

### N8: Fix _speedups.c not handling compact strings (Python 3.12+)

The C extension uses `PyUnicode_READY()` which is deprecated in
Python 3.12+ and will be removed in a future version. The function
also dispatches on `PyUnicode_KIND()` which may change behavior with
compact string representations. Update the C extension to use the
modern `PyUnicode_AsUTF8AndSize()` API path for Python 3.12+ while
maintaining backward compatibility via `#if PY_VERSION_HEX` guards.

### N9: Add __html_format__ support to Markup for custom format specs

The `Markup.__html_format__()` method raises `ValueError` for any
non-empty format spec. Add support for common format specifiers that
make sense for HTML strings: truncation (`:.20` to limit length with
an ellipsis entity `&hellip;`), and alignment (`:>30` using `&nbsp;`
padding instead of spaces). Implement in the `__html_format__` method.

### N10: Fix soft_str() not preserving Markup subclass identity

The `soft_str()` function checks `isinstance(s, str)` and returns `s`
unchanged, but this also matches `Markup` subclasses. While this
preserves `Markup` itself, a user subclass of `Markup` (e.g.,
`LazyMarkup`) is returned as-is, which is the correct behavior.
However, `soft_str()` is not documented as `Markup`-aware. Add explicit
documentation and a fast-path type check: if `type(s) is Markup`,
return immediately without the `isinstance` check.

## Medium

### M1: Add HTML attribute escaping mode

The current `escape()` treats all contexts identically, but HTML
attribute values have stricter escaping requirements than text content
(e.g., `=` should be escaped in unquoted attributes). Add an
`escape_attr()` function and a `Markup.escape_attr()` classmethod that
escapes additional characters (`=`, `` ` ``, whitespace) for safe
attribute value insertion. Requires changes to `__init__.py`,
`_native.py`, and `_speedups.c`.

### M2: Implement Markup-aware template string support

Add a `MarkupTemplate` class (subclassing `string.Template`) that
escapes substitution values automatically, similar to how
`EscapeFormatter` works for `str.format()`. `MarkupTemplate("Hello,
$name!").safe_substitute(name="<script>")` should produce
`Markup('Hello, &lt;script&gt;!')`. Requires a new class in
`__init__.py` with overridden substitution methods and integration
with the `escape()` function.

### M3: Add streaming escape support for large strings

The current `escape()` creates a complete escaped copy in memory.
For very large strings (e.g., multi-MB HTML content), this doubles
memory usage. Add an `escape_stream()` generator that yields escaped
chunks without holding the full result in memory, and a corresponding
C extension function that processes the input in fixed-size blocks.
Touches `__init__.py`, `_native.py`, and `_speedups.c`.

### M4: Implement context-aware auto-escaping

Add a `MarkupContext` enum (`HTML`, `ATTRIBUTE`, `JS`, `CSS`, `URL`)
and a `contextual_escape(s, context)` function that applies the
appropriate escaping rules for each context. JavaScript context should
escape characters that break `<script>` blocks, URL context should
percent-encode, and CSS context should escape characters that could
close `<style>` blocks. Requires a new escaping matrix in
`__init__.py` and corresponding C-level fast paths.

### M5: Add Markup.truncate() with HTML-aware length calculation

Implement `Markup.truncate(length, end='...')` that truncates to the
specified visible-character length without breaking HTML entities or
tags. `Markup("Hello &amp; World").truncate(11)` should produce
`Markup("Hello &amp; Wo...")`, not split the `&amp;` entity. Requires
entity-aware length calculation, an entity boundary scanner, and
proper `Markup` return-type preservation.

### M6: Implement a safe string interpolation DSL

Add `Markup.format_map_strict(mapping)` that raises `KeyError` for
any key in the template not present in the mapping (unlike
`format_map()` which would raise at runtime). Additionally, validate
at call time that all mapping values implement `__html__` or are
primitive types, rejecting arbitrary objects that might produce unsafe
`__str__` output. Requires extending `EscapeFormatter` with a
validation pass and new error types.

### M7: Add performance benchmarking CI integration

Extend `bench.py` into a comprehensive benchmark suite covering:
small strings (< 100 chars), large strings (> 1MB), strings with
high entity density (every character needs escaping), `Markup.format()`
with many substitutions, and `Markup.join()` with large lists. Add
`asv` (airspeed velocity) configuration for tracking performance across
commits. Touches `bench.py`, a new `benchmarks/` directory, CI
configuration, and `pyproject.toml`.

### M8: Implement Markup.sanitize() for tag whitelisting

Add `Markup.sanitize(allowed_tags={'b', 'i', 'em', 'strong'},
allowed_attrs={'class', 'id'})` that strips all HTML tags not in the
whitelist and removes disallowed attributes from permitted tags. Return
a new `Markup` instance with only safe tags preserved. Requires an
HTML parser (using `html.parser`), attribute filtering, and
reconstruction of the sanitized output.

### M9: Add __format__ support with HTML-aware alignment

Override `Markup.__format__()` to handle format specs with
HTML-entity-aware width calculation. `f"{Markup('&amp;'):>10}"`
should right-align based on the visible width (1 character for `&`)
not the raw length (5 characters for `&amp;`). Requires entity-aware
width computation and padding with `Markup`-typed spaces.

### M10: Implement lazy markup evaluation

Add a `LazyMarkup` class that stores a callable and its arguments,
only rendering to a string when `__str__()` or `__html__()` is
called. `LazyMarkup(my_expensive_render, arg1, arg2)` should defer
execution until the value is actually needed. Support `+` and `%`
operators that return new `LazyMarkup` instances composing operations.
Requires a new class in `__init__.py` implementing the `_HasHTML`
protocol.

## Wide

### W1: Implement a full HTML sanitizer with CSS and URL validation

Build a comprehensive HTML sanitizer that goes beyond tag whitelisting:
validate CSS properties in `style` attributes against a safe property
list, validate `href` and `src` URLs against an allowlist of schemes
(`http`, `https`, `mailto`) rejecting `javascript:` and `data:`,
handle nested contexts (Markdown inside HTML), and preserve whitespace
semantics. Changes span a new `sanitizer.py` module, the `Markup`
class, `_native.py` for pure-Python sanitization rules, and the test
suite.

### W2: Add multi-format escaping engine with pluggable contexts

Implement a pluggable escaping architecture that supports HTML, XML,
LaTeX, Markdown, and custom formats. Each format registers its own
escape rules and entity mappings. `Markup` becomes generic over the
escaping format: `Markup[HTML]`, `Markup[LaTeX]`. Requires a format
registry, per-format escape implementations (Python + C), `Markup`
class generics, and format-specific test suites. Changes span
`__init__.py`, `_native.py`, `_speedups.c`, new format modules, and
`pyproject.toml` for optional dependencies.

### W3: Implement a Markup-aware diff and patch system

Add `markup_diff(old, new)` that produces a semantic diff between two
Markup strings, highlighting additions and deletions in HTML (e.g.,
`<ins>` and `<del>` tags). Include `markup_patch(original, diff)` to
apply diffs. Handle entity-level diffing (changes to `&amp;` vs `&`)
and tag-level diffing (structural changes). Changes span a new
`diff.py` module, the `Markup` class for diff output, HTML parsing
utilities, and the test suite.

### W4: Add async/streaming markup rendering pipeline

Implement an async streaming pipeline: `AsyncMarkup` objects that
can be awaited, an `async for` interface for rendering large templates
chunk-by-chunk, back-pressure support, and integration with ASGI
frameworks. Include `markup_stream()` that yields `Markup` chunks
from an async iterable of values. Changes span a new `async_markup.py`
module, C extension updates for GIL-free chunk processing, the
`Markup` class protocol, and test fixtures for async scenarios.

### W5: Implement compile-time escape optimization via AST transform

Add a build-time optimization that scans Python source for constant
`escape()` calls (e.g., `escape("Hello & World")`) and replaces them
with pre-computed `Markup` literals at import time. Use an AST
transformer registered as a codec or import hook. Include a CLI tool
for pre-processing source files and a pytest plugin for validating
transformations. Changes span a new `optimize.py` module, AST
transformation logic, import machinery, build configuration, and
documentation.

### W6: Add comprehensive property-based testing with Hypothesis

Implement a Hypothesis strategy for generating arbitrary Markup
strings, nested escape/unescape sequences, format strings with mixed
safe/unsafe values, and edge-case Unicode points. Add property tests
verifying: round-trip safety (escape then unescape recovers original),
Markup arithmetic (addition preserves safety), format idempotence,
and C/Python implementation equivalence. Changes span the test suite,
a new `strategies.py` module, `conftest.py` for fixtures, and CI
configuration for slow property tests.

### W7: Implement a Markup-compatible content security policy builder

Add a `CSPBuilder` class that generates `Content-Security-Policy`
header values from Markup content analysis. Scan Markup strings for
inline scripts, inline styles, and external resource references,
then generate the tightest CSP that permits the detected content.
Include nonce generation, hash-based allowlisting for inline scripts,
and `report-uri` support. Changes span a new `csp.py` module, the
`Markup` class for content analysis hooks, HTML parsing, and the test
suite.

### W8: Add multi-language string externalization with Markup preservation

Implement i18n support where translatable strings preserve Markup
safety: `_("Hello, <em>%(name)s</em>!", name=user_input)` should
escape `name` but preserve the `<em>` tags from the translation.
Include gettext-compatible catalog extraction, plural forms, and
context markers. Changes span a new `i18n.py` module, a catalog
format, extraction tools, `EscapeFormatter` integration, and
documentation.

### W9: Implement a Markup-aware parser combinator library

Build a parser combinator library that operates on `Markup` strings,
extracting structured data while preserving safety annotations. Include
combinators for matching entity-encoded patterns, splitting on HTML
tag boundaries, and extracting text content from tagged regions.
Changes span a new `parser.py` module, combinator primitives, the
`Markup` class for parser integration, result types, and the test
suite.

### W10: Add WASM/JavaScript compilation target for browser-side escaping

Compile the C extension to WebAssembly via Emscripten, producing a
JavaScript module that provides `escape()` and `Markup` compatible
with the Python API. Include a JavaScript wrapper that matches the
Python `Markup` class interface, npm packaging, benchmark comparisons
with native JS escaping, and a test suite running in Node.js. Changes
span a new `wasm/` directory, Emscripten build configuration,
JavaScript wrapper code, npm package metadata, and CI for WASM builds.
