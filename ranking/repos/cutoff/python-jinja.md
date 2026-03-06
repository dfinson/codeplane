# pallets/jinja

| Field | Value |
|-------|-------|
| **URL** | https://github.com/pallets/jinja |
| **License** | BSD-3-Clause |
| **Language** | Python |
| **Scale** | Medium |
| **Category** | Template engine |
| **Set** | Cutoff |

## Why this repo

- **Classic compiler pipeline**: Clean separation of concerns across
  lexer (`lexer.py`), parser (`parser.py`), AST nodes (`nodes.py`),
  optimizer (`optimizer.py`), and code generator (`compiler.py`). The
  `Environment` class in `environment.py` orchestrates the full
  template-to-Python compilation pipeline. One developer can follow a
  template string from tokenization through to rendered output.
- **Rich extension surface**: 40+ built-in filters (`filters.py`),
  15+ built-in tests (`tests.py`), a loader abstraction with 8
  implementations (`loaders.py`), a bytecode cache system
  (`bccache.py`), sandboxed execution (`sandbox.py`), and an
  extension API (`ext.py`) for custom tags and behaviors.
- **Permissive**: BSD-3-Clause license.

## Structure overview

```
src/jinja2/
├── __init__.py            # Public API re-exports
├── _identifier.py         # Identifier character detection
├── async_utils.py         # Async variant decorators for filters/tests
├── bccache.py             # Bytecode cache — FileSystem, Memcached
├── compiler.py            # AST-to-Python code generator
├── constants.py           # Has-safe-repr constants
├── debug.py               # Traceback rewriting for template errors
├── defaults.py            # Default filters, tests, delimiter strings
├── environment.py         # Environment and Template classes
├── exceptions.py          # TemplateSyntaxError, UndefinedError, etc.
├── ext.py                 # Extension base class, InternationalizationExtension
├── filters.py             # 40+ built-in template filters (do_upper, do_sort…)
├── idtracking.py          # Variable scope and symbol tracking for compiler
├── lexer.py               # Tokenizer — produces TokenStream from source
├── loaders.py             # Template loaders — FileSystem, Dict, Package, etc.
├── meta.py                # Template introspection (find undeclared variables)
├── nativetypes.py         # NativeEnvironment — renders to Python types
├── nodes.py               # AST node classes — Expr, Stmt, Template, etc.
├── optimizer.py           # Constant-folding AST optimizer
├── parser.py              # Token stream → AST parser
├── runtime.py             # Template runtime — Context, Undefined, LoopContext
├── sandbox.py             # SandboxedEnvironment — restricted execution
├── tests.py               # Built-in template tests (odd, even, defined, etc.)
├── utils.py               # Utilities — LRUCache, pass_context, concat
└── visitor.py             # AST visitor and transformer base classes
```

## Scale indicators

- ~25 Python source files
- ~14K lines of code
- Flat structure (single package under `src/jinja2/`)
- Dependency on `markupsafe` only

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add a `do_json_query` filter for JSONPath-style value extraction

The `filters.py` module provides 40+ built-in filters but none that
can extract values from nested dict/list structures using a path string
(e.g., `data|json_query("results[0].name")`). Add a `do_json_query`
filter to `filters.py` that accepts a dot/bracket path string and
traverses the input value, returning the matched sub-value or
`Undefined` if the path does not resolve. Register it in
`DEFAULT_FILTERS` in `defaults.py`.

### N2: Fix `do_truncate` not accounting for multi-byte characters in length

The `do_truncate` filter in `filters.py` truncates strings by character
count and appends an ellipsis, but does not consider that some Unicode
characters (e.g., CJK ideographs) have double display width. When
truncating strings containing wide characters, the visual output exceeds
the intended display width. Fix `do_truncate` to optionally measure
display width using `unicodedata.east_asian_width()` when a
`width_aware` parameter is set to `True`.

### N3: Add `is mapping` template test to `tests.py`

The built-in tests in `tests.py` include `is iterable`, `is sequence`,
and `is string`, but there is no `is mapping` test to check whether a
value implements the `Mapping` ABC. Add a `test_mapping` function to
`tests.py` that returns `True` for dict-like objects, and register it
in `DEFAULT_TESTS` in `defaults.py`.

### N4: Fix `FileSystemLoader` not normalizing path separators on Windows

The `FileSystemLoader` in `loaders.py` uses `os.path.join` to construct
template file paths, but template names use forward slashes by
convention. When a template name like `"partials/header.html"` is
loaded on Windows, the path separator mismatch can cause cache misses
in the bytecode cache (`bccache.py`) because the cache key includes the
un-normalized path. Fix the loader to normalize separators before
computing the cache key.

### N5: Add `lineno` context to `UndefinedError` messages

When an `Undefined` object is accessed in `runtime.py`, the resulting
`UndefinedError` includes the variable name and hint but not the
template source line number. The line information is available in the
compiled code's code object but is not propagated to the exception. Fix
the `Undefined._fail_with_undefined_error` method to include the
current template name and line number in the error message when the
information is available from the call stack.

### N6: Add `do_unique` filter to remove duplicate values from sequences

The `filters.py` module has `do_sort`, `do_reverse`, `do_batch`, and
`do_slice` for sequence manipulation, but no filter for removing
duplicates while preserving order. Add a `do_unique` filter that accepts
an optional `attribute` parameter (like `do_sort`) for deduplicating
objects by a specific attribute, and uses an `OrderedDict` or `dict`
key-tracking internally to preserve insertion order. Register it in
`DEFAULT_FILTERS`.

### N7: Fix `Optimizer` not folding `Concat` nodes with all-constant operands

The `Optimizer` in `optimizer.py` constant-folds `Expr` nodes by
calling `as_const()`, but `Concat` nodes (which represent
`{{ "a" ~ "b" }}`) are not folded even when all operands are constant
strings. This is because `Concat` is not a subclass of `Expr` that
`as_const()` handles for concatenation. Fix the optimizer to detect
`Concat` nodes with all-`Const` children and replace them with a single
`Const` node containing the concatenated string.

### N8: Add `ignore_missing` parameter to `ChoiceLoader`

The `ChoiceLoader` in `loaders.py` iterates through its list of loaders
and raises `TemplateNotFound` if none can locate the template. However,
there is no way to make it silently skip loaders that raise import
errors or configuration errors during initialization. Add an
`ignore_missing` parameter to `ChoiceLoader.__init__` that catches
`TemplateNotFound` (and optionally `OSError`) from individual loaders
and continues to the next one, only raising if all loaders fail.

### N9: Fix `TokenStream.expect` not including available token types in error

When `TokenStream.expect()` in `lexer.py` encounters an unexpected
token, it raises a `TemplateSyntaxError` with the expected and actual
token types but does not suggest what tokens would be valid at that
position. Enhance the error message to include a list of token types
that commonly follow the current parsing context, improving template
author diagnostics.

### N10: Add cache statistics method to `LRUCache` in utils

The `LRUCache` class in `utils.py` implements a basic LRU eviction
cache but provides no way to inspect cache performance (hit/miss ratio,
eviction count, current size vs capacity). Add a `stats()` method that
returns a `CacheStats` named tuple with `hits`, `misses`, `evictions`,
and `current_size` fields, incrementing counters in `__getitem__` and
`__setitem__`.

## Medium

### M1: Implement template inheritance cycle detection with clear diagnostics

When two templates extend each other (e.g., A extends B, B extends A),
Jinja2 enters infinite recursion during compilation. The `Environment`
class in `environment.py` calls `_load` and `_parse` without tracking
the inheritance chain. Add cycle detection to template loading that
tracks the chain of `{% extends %}` directives, detects cycles before
recursion, and raises a `TemplateSyntaxError` with the full cycle path
(e.g., "Circular inheritance: a.html → b.html → a.html"). Changes touch
`environment.py` (load tracking), `parser.py` (extends parsing), and
`exceptions.py` (new error context).

### M2: Add a `profiling` mode that tracks per-block render times

The `Environment` class has no built-in support for profiling template
rendering performance. Add a `profiling=True` option to `Environment`
that instruments `BlockReference` calls in `runtime.py` to measure and
record the wall-clock time spent rendering each `{% block %}`. Store
results on the `Context` object and expose them via a
`Context.get_profile()` method. Changes touch `environment.py` (flag
propagation), `runtime.py` (block instrumentation and context storage),
and `compiler.py` (injecting timing calls into generated code).

### M3: Implement template dependency graph extraction

The `meta.py` module provides `find_referenced_templates` for a single
template, but there is no facility to build a complete dependency graph
across all templates in a loader. Add a `DependencyGraph` class to
`meta.py` that walks all templates available in a loader, resolves
`{% extends %}`, `{% include %}`, and `{% import %}` directives, and
produces an adjacency list of template dependencies. Include
`topological_sort()` and `find_cycles()` methods. Changes touch
`meta.py` (graph construction), `loaders.py` (template enumeration via
`list_templates`), and `nodes.py` (dependency extraction from AST).

### M4: Add support for custom delimiter configuration validation

The `Environment` constructor accepts custom block, variable, and
comment delimiters via `*_start_string` and `*_end_string` parameters,
but does not validate that they are non-overlapping or that they will
not create ambiguous tokenization. Add validation to `Environment.__init__`
that checks for delimiter conflicts (e.g., `block_start_string` being
a prefix of `variable_start_string`), raises descriptive errors, and
tests the delimiter set against the `Lexer` to ensure it can tokenize a
simple template. Changes touch `environment.py` (validation logic),
`lexer.py` (delimiter conflict detection), and `exceptions.py` (new
`ConfigurationError`).

### M5: Implement sandboxed attribute access audit logging

The `SandboxedEnvironment` in `sandbox.py` restricts attribute access
via `is_safe_attribute` and `is_safe_callable` but provides no
mechanism to log or audit denied access attempts. Add an audit callback
system to `SandboxedEnvironment` that invokes a configurable callback
with the target object, attribute name, and denial reason on each
blocked access. The callback should receive structured data including
the template name and line number. Changes touch `sandbox.py` (audit
hook infrastructure), `runtime.py` (context propagation), and
`environment.py` (callback configuration).

### M6: Add AST-level template linting for common template mistakes

There is no linting or static analysis pass for Jinja2 templates. Add
a `lint()` method to `Environment` that parses a template into an AST
and runs a series of checks: undefined variables (cross-referencing
`meta.find_undeclared_variables`), unreachable code after
`{% return %}` or `{% break %}`, duplicate block names, and deprecated
filter usage. Return a list of `LintWarning` objects with severity,
message, and source location. Changes touch `environment.py` (lint
entrypoint), `nodes.py` (AST traversal), `meta.py` (variable
analysis), and a new `lint.py` module for rule definitions.

### M7: Implement incremental template compilation with change detection

The `FileSystemBytecodeCache` in `bccache.py` caches compiled bytecode
but recompiles entire templates when any part changes. Add a change
detection mechanism that hashes individual blocks and only recompiles
blocks that have changed, merging the result with the cached version.
Requires tracking block boundaries during compilation. Changes touch
`bccache.py` (cache key granularity), `compiler.py` (block-level
hashing), and `environment.py` (incremental compilation orchestration).

### M8: Add template source map generation for debugging

When Jinja2 compiles a template to Python code, the relationship
between template source lines and generated Python lines is lost
outside of error handling. Add a source map output option to
`CodeGenerator` in `compiler.py` that produces a JSON mapping from
generated Python line numbers to template source line/column positions.
Expose the source map via `Template.get_source_map()`. Changes touch
`compiler.py` (line tracking during code generation), `environment.py`
(source map storage), and `nodes.py` (source position propagation).

### M9: Implement filter chaining optimization in the compiler

When multiple filters are chained (e.g., `{{ x|lower|trim|truncate(50) }}`),
the compiler generates nested function calls for each filter. Add an
optimization pass to `compiler.py` that detects chains of pure
(side-effect-free) filters and combines them into a single composed
function call, reducing call overhead. Requires annotating filters with
a `pure` flag in `defaults.py` and adding a new `FilterChain`
optimization to `optimizer.py`. Changes touch `compiler.py`,
`optimizer.py`, `defaults.py`, and `nodes.py`.

### M10: Add structured error recovery mode to the parser

The parser in `parser.py` raises `TemplateSyntaxError` on the first
error and aborts. Add an error recovery mode (`Environment(error_recovery=True)`)
that collects multiple syntax errors per template instead of stopping
at the first one. The parser should skip to the next statement delimiter
on error and continue parsing. Return all errors as a list attached to
the `TemplateSyntaxError`. Changes touch `parser.py` (recovery logic),
`environment.py` (mode flag), `lexer.py` (synchronization points), and
`exceptions.py` (multi-error container).

## Wide

### W1: Implement an async-native template rendering pipeline

Jinja2 has partial async support via `async_utils.py` and async
generator wrappers, but the core rendering pipeline (environment,
context, loaders, filters) is synchronous with async wrappers bolted
on. Implement a fully async rendering path: async `Environment.get_template`,
async `BaseLoader.get_source`, async filter support with
`@async_pass_context`, async `{% include %}` resolution, and async
bytecode cache reads/writes. Changes span `environment.py` (async
template loading), `loaders.py` (async source fetching), `runtime.py`
(async context and block rendering), `filters.py` (async filter
variants), `compiler.py` (async code generation), `bccache.py` (async
cache I/O), and `async_utils.py` (new async primitives).

### W2: Add a template security analyzer for injection detection

Implement a static analysis tool that scans Jinja2 templates for
potential security issues: unescaped variable output in HTML contexts,
`|safe` usage on user-controlled variables, `{% set %}` assignments
from untrusted sources, dangerous filter chains that bypass auto-
escaping, and sandbox escape patterns. Build a taint-tracking system
on the AST that propagates "tainted" markers through variable
assignments and filter applications. Changes span `nodes.py` (taint
annotations), `compiler.py` (taint propagation), `sandbox.py`
(security rules), `filters.py` (taint-through declarations), `meta.py`
(analysis infrastructure), and a new `security.py` module for the
analyzer and report generation.

### W3: Implement a template-to-JavaScript cross-compilation backend

Add a compilation target that generates JavaScript code from Jinja2
AST, allowing templates to be rendered client-side. Support the core
template features: variable output, filters, tests, conditionals,
loops, block inheritance, includes, and macros. Map built-in filters
and tests to JavaScript equivalents. Changes span `compiler.py` (new
`JSCodeGenerator` class), `nodes.py` (JS-specific visitor methods),
`filters.py` (JS filter mapping declarations), `tests.py` (JS test
mappings), `runtime.py` (JS runtime library generation), and a new
`js_backend.py` module for the JavaScript code emitter and runtime
bundle.

### W4: Implement a multi-environment template composition system

Add support for composing templates from multiple `Environment`
instances, each with different loaders, filters, and security
settings. A `CompositeEnvironment` should allow `{% include %}` and
`{% import %}` directives to specify a namespace prefix that routes to
a specific sub-environment (e.g., `{% include "admin::dashboard.html" %}`).
Support cross-environment block inheritance and shared context
variables. Changes span `environment.py` (composite environment class),
`loaders.py` (namespace-aware loader delegation), `parser.py` (prefix
syntax parsing), `runtime.py` (cross-environment context bridging),
`compiler.py` (namespace-qualified template references), and
`nodes.py` (namespace annotations on include/import nodes).

### W5: Add internationalization support beyond the i18n extension

The current `InternationalizationExtension` in `ext.py` provides basic
`{% trans %}` support but lacks plural forms with complex CLDR rules,
ICU message format support, date/number/currency formatting with
locale awareness, bidirectional text handling, and translation
extraction to PO/XLIFF formats. Implement a comprehensive i18n system
with locale-aware filters for dates, numbers, and currencies; ICU
`MessageFormat` parsing in template expressions; automatic extraction
of translatable strings with context and plural metadata; and RTL/LTR
text direction markers. Changes span `ext.py` (enhanced i18n
extension), `filters.py` (locale-aware formatting filters),
`lexer.py` (ICU expression syntax), `parser.py` (ICU message
parsing), `compiler.py` (locale-aware code generation), `runtime.py`
(locale context propagation), and a new `i18n.py` utilities module.

### W6: Implement a template debugging protocol server

Add a debug adapter that allows IDEs to set breakpoints in Jinja2
templates and step through rendering. Implement the Debug Adapter
Protocol (DAP) with support for template breakpoints (by template
name and line number), variable inspection in the current context and
local scope, step-through of loop iterations and block calls, call
stack display showing the template inheritance and include chain, and
conditional breakpoints using Jinja2 expressions. Changes span
`compiler.py` (debug hook injection), `runtime.py` (debugger context
integration), `environment.py` (debug mode activation), `debug.py`
(DAP server implementation), `nodes.py` (breakpoint annotations), and
a new `debugserver.py` module for the DAP protocol handler and
communication.

### W7: Implement a template migration tool for version upgrades

Add a tool that automatically migrates Jinja2 templates between major
versions. Parse templates with the current parser, apply a series of
AST transformations for deprecated syntax (old-style `{% for %}`
scoping, removed filters, changed default behaviors), and regenerate
template source from the transformed AST. Include a diff preview mode,
dry-run validation, and a report of breaking changes. Changes span
`parser.py` (legacy syntax support), `nodes.py` (AST-to-source
serialization), `filters.py` (deprecation metadata), `environment.py`
(migration mode), `lexer.py` (legacy token handling), a new
`migrate.py` module for transformation rules and orchestration, and
`visitor.py` (migration-specific transformers).

### W8: Add real-time template rendering with incremental updates

Implement a reactive rendering system where templates automatically
re-render when their input context changes. Templates should track
which context variables each block depends on, and when a variable
changes, only re-render the affected blocks. Support WebSocket-based
push of incremental HTML updates. Changes span `runtime.py`
(dependency tracking in context access), `compiler.py` (dependency
annotation in generated code), `environment.py` (reactive rendering
mode), `nodes.py` (block dependency declarations), a new
`reactive.py` module for change detection and incremental rendering,
and a new `ws_transport.py` module for WebSocket communication.

### W9: Implement a visual template editor backend API

Add a structured API that enables visual template editing: parse
templates into a manipulable document model (blocks, variables, loops,
conditionals as structured objects), support insertion and deletion of
template constructs at cursor positions, provide auto-completion data
for available variables, filters, and tests based on context, and
round-trip edited documents back to template source. Changes span
`parser.py` (incremental parsing support), `nodes.py` (document model
with editing operations), `compiler.py` (partial re-compilation),
`meta.py` (completion context analysis), `environment.py` (editor
mode), `lexer.py` (incremental tokenization), and a new `editor.py`
module for the document model and editing API.

### W10: Implement a template performance profiler with flame graph output

Add a comprehensive performance profiling system that instruments
template rendering at multiple levels: per-template render time,
per-block render time, per-filter execution time, per-expression
evaluation time, and loader/cache lookup time. Aggregate results
across multiple renders and output flame graph data (folded stack
format), Chrome trace event format, and a summary report. Changes span
`runtime.py` (render instrumentation), `compiler.py` (profiling hook
injection), `environment.py` (profiler lifecycle management),
`filters.py` (filter timing wrappers), `loaders.py` (loader timing),
`bccache.py` (cache timing), and a new `profiler.py` module for data
collection, aggregation, and output formatting.
