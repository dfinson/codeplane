# CLIUtils/CLI11

| Field | Value |
|-------|-------|
| **URL** | https://github.com/CLIUtils/CLI11 |
| **License** | BSD-3-Clause |
| **Language** | C++ |
| **Scale** | Small |
| **Category** | CLI parser |
| **Set** | Cutoff |
| **Commit** | `b5fc53c89afc1f2c4fce49c5061d44dddcd41fc4` |

## Why this repo

- **Feature-rich header library**: Single-package layout under
  `include/CLI/` with clear per-concern headers — `App.hpp` (command
  hierarchy), `Option.hpp` (option/flag definitions), `Validators.hpp`
  (input validation), `Config.hpp` (TOML/INI config file parsing),
  `Formatter.hpp` (help text generation), `Error.hpp` (exception
  hierarchy), `TypeTools.hpp` (type conversion), and `StringTools.hpp`
  (string utilities). One developer can follow the entire parse flow.
- **Rich history**: 3K+ stars. Widely used C++ CLI library with
  support for subcommands, option groups, config files, and validators.
- **Permissive**: BSD-3-Clause license.

## Structure overview

```
include/CLI/
├── CLI.hpp               # Umbrella include
├── App.hpp               # App class — command/subcommand hierarchy, parsing
├── Option.hpp            # Option class — flags, options, positionals
├── Validators.hpp        # Validator framework — Range, ExistingFile, etc.
├── ExtraValidators.hpp   # Extended validators — IPV4, TypeValidator, etc.
├── Config.hpp            # Config file support — TOML/INI reading/writing
├── ConfigFwd.hpp         # Config forward declarations
├── Formatter.hpp         # Help text formatter
├── FormatterFwd.hpp      # Formatter forward declarations
├── Error.hpp             # Exception hierarchy (ParseError, RequiredError, etc.)
├── TypeTools.hpp         # Type conversion and detection
├── StringTools.hpp       # String splitting, trimming, matching
├── Split.hpp             # Argument splitting (short/long/windows-style)
├── Encoding.hpp          # UTF-8/wide string conversion
├── Argv.hpp              # argv encoding handling
├── Timer.hpp             # Simple timer utility
├── Macros.hpp            # Build configuration macros
├── Version.hpp           # Library version
└── impl/                 # Inline implementations
    ├── App_inl.hpp       # App method implementations
    ├── Option_inl.hpp    # Option method implementations
    ├── Validators_inl.hpp # Validator implementations
    ├── Config_inl.hpp    # Config reader/writer implementations
    ├── Formatter_inl.hpp # Formatter implementations
    ├── Split_inl.hpp     # Split implementations
    ├── StringTools_inl.hpp # StringTools implementations
    ├── Encoding_inl.hpp  # Encoding implementations
    └── ExtraValidators_inl.hpp # Extended validator implementations
```

## Scale indicators

- ~30 header files
- ~13K lines of code
- Flat structure (single package with `impl/` separation)
- Zero external dependencies (header-only)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add Levenshtein-based suggestion for misspelled option names

When a user passes an unknown option (e.g., `--verbos`), the error
message lists it as unknown but does not suggest the closest match
from registered options. Add string-distance matching to
`App::_parse_single()` in `App_inl.hpp` so the `ExtrasError` message
includes a "Did you mean '--verbose'?" suggestion when a close match
exists among the app's option long names.

### N2: Fix Formatter truncating help text for very long default values

When an option has a default value string longer than the column width
(e.g., a complex path or JSON string), `Formatter::make_option_opts()`
in `Formatter_inl.hpp` appends the default inline, causing misaligned
help text. Fix the formatter to wrap long default values onto a
continuation line, preserving alignment of subsequent options.

### N3: Add negation prefix support for boolean flags

Boolean flags like `--verbose` can be set but not explicitly negated
without defining a separate `--no-verbose` flag. Add a configurable
negation prefix (defaulting to `--no-`) that automatically generates
the negated form for boolean flags, so `--no-verbose` sets the flag
to `false`. Implement in `Option.hpp` flag processing and integrate
with help text display.

### N4: Fix Range validator not handling floating-point edge cases

The `Range` validator in `Validators.hpp` uses `lexical_cast` for
conversion but does not handle special floating-point values (`NaN`,
`inf`, `-inf`). When a user passes `nan` to a `Range`-validated
float option, the behavior is undefined. Add explicit rejection of
non-finite values in the `Range` validator with a clear error message.

### N5: Add environment variable source display in help text

Options can read default values from environment variables via
`envname()`, but the help formatter does not display which environment
variable an option reads from. Add the environment variable name to
the help output (e.g., `[env: MY_APP_PORT]`) in
`Formatter::make_option_opts()` alongside the default value display.

### N6: Fix Config writer not quoting strings containing delimiters

The TOML/INI config writer in `Config_inl.hpp` emits string values
without checking whether they contain the section delimiter (`[`),
comment character (`#`), or equals sign (`=`). Such values produce
malformed config files when written and re-read. Add quoting logic
that wraps values containing special characters in double quotes with
proper escaping.

### N7: Add option deprecation warnings

Add an `Option::deprecated(std::string message)` method that marks an
option as deprecated. When a deprecated option is used, the parser
should emit a warning to stderr (or a configurable callback) with the
deprecation message, while still accepting the value. Display
`[DEPRECATED]` in help text. Implement in `Option.hpp` and integrate
with `App::_parse_single()`.

### N8: Fix ExistingFile validator following symlinks unconditionally

The `ExistingFile` validator in `Validators_inl.hpp` uses
`std::filesystem::exists()` which follows symlinks. When a symlink
points to a non-existent target, the validator rejects the path even
though the symlink itself exists. Add a `follow_symlinks` parameter
(default `true`) and use `std::filesystem::symlink_status()` when
disabled to validate the symlink's own existence.

### N9: Add completion value hints for custom types

Options using custom types via `transform()` or `check()` do not
provide value hints for shell completion. Add an
`Option::completion_values(std::vector<std::string>)` method that
registers a static set of completion candidates, and expose them via
the `App` introspection API for integration with external completion
generators.

### N10: Fix Timer class not supporting lap timing

The `Timer` class in `Timer.hpp` supports start/stop timing but
provides no way to record intermediate lap times without stopping.
Add a `lap()` method that records the current elapsed time, stores it
in an internal vector, and returns the duration since the last lap (or
start). Add a `laps()` accessor that returns all recorded intervals.

## Medium

### M1: Implement mutually exclusive option groups with validation

Add an `app->add_option_group("output")->mutually_exclusive()` mode
where at most one option in the group may be set. Requires validation
after parsing that checks for conflicts, a clear error message naming
the conflicting options, and help text indicating the exclusion
relationship. Touches `App.hpp` (option group management),
`Option_inl.hpp` (validation), and `Formatter_inl.hpp` (display).

### M2: Add TOML table and array-of-tables support to Config parser

The Config parser in `Config_inl.hpp` handles flat key-value pairs and
simple sections but does not support TOML inline tables
(`server = {host = "localhost", port = 8080}`) or arrays of tables
(`[[servers]]`). Add parsing for these constructs, mapping them to
nested subcommand options and vector options respectively. Touches
`Config.hpp`, `Config_inl.hpp`, and `App_inl.hpp` for value routing.

### M3: Implement typed option result objects

Currently, parsed option values are stored as strings and converted
on access. Add a `TypedOption<T>` wrapper returned by
`app->add_option<T>()` that stores the converted value directly,
supports `operator*()` and `operator->()` for access, and integrates
with validators for type-safe range checking. Requires changes to
`Option.hpp`, `TypeTools.hpp`, and `App.hpp`.

### M4: Add shell completion script generation

Implement `app->generate_completion(CompletionFormat::Bash)` that
produces a bash/zsh/fish completion script from the app's registered
subcommands, options, and their metadata (choices, file types, custom
completions). Requires traversal of the command tree, format-specific
script templates, and integration with the `App` introspection API.
Touches `App.hpp`, a new `Completion.hpp` header, and the formatter.

### M5: Implement option value callbacks with ordering guarantees

Add `Option::each(callback)` that invokes a callback each time the
option is parsed (supporting repeated options), with access to the
parse index for ordering. Requires changes to the option value
storage to track per-occurrence metadata, callback invocation in
`Option_inl.hpp` during `add_result()`, and integration with the
multi-value collection system.

### M6: Add JSON config file format support

Extend the Config system to read and write JSON configuration files
alongside TOML/INI. Support nested objects mapping to subcommands,
arrays mapping to vector options, and type-preserving round-trips
(numbers stay as numbers, not strings). Requires a JSON parser in
`Config_inl.hpp`, format detection from file extension, and
integration with the config option (`--config`).

### M7: Implement conditional option requirements

Add `Option::needs(Option*)` and `Option::excludes(Option*)` with
support for complex dependency expressions: `opt_a->needs(opt_b |
opt_c)` (A requires B or C), `opt_a->needs(opt_b & opt_c)` (A
requires both). Requires a dependency expression tree, post-parse
validation, clear error messages showing the unsatisfied dependency
chain, and help text integration. Touches `Option.hpp`, `App_inl.hpp`,
and `Formatter_inl.hpp`.

### M8: Add colored help output with theme support

Implement a `ColorFormatter` extending `Formatter` that applies ANSI
color codes to help output: bold for command names, dim for metavars,
colored section headings, and syntax-highlighted default values.
Support theme customization via a `Theme` struct and automatic
detection of terminal color capability. Touches `Formatter.hpp`,
`FormatterFwd.hpp`, and a new `Color.hpp` header.

### M9: Implement configuration file generation from current option state

Add `app->write_config(std::ostream&, ConfigFormat)` that serializes
the current parsed state (including defaults, environment values, and
CLI values) to a config file in TOML, INI, or JSON format. Include
option descriptions as comments, group by subcommand, and mark
values that differ from defaults. Touches `Config.hpp`,
`Config_inl.hpp`, and `App.hpp`.

### M10: Add command alias support

Implement `app->add_subcommand("remove")->aliases({"rm", "del"})` so
subcommands can be invoked by alternative names. Aliases should work
in parsing, help text (listed alongside the primary name), and shell
completion. Requires changes to `App::_find_subcommand()` for alias
resolution, `App_inl.hpp` for registration, and the formatter for
display.

## Wide

### W1: Implement a plugin system for dynamically discovered subcommands

Add a plugin architecture that discovers and loads subcommands from
shared libraries or entry points at runtime. Support plugin metadata
(version, author), dependency ordering, conflict detection for
duplicate command names, and a `plugin list` built-in subcommand.
Changes span `App.hpp` (dynamic registration), a new `Plugin.hpp`
module (discovery and loading), the formatter (plugin attribution),
config integration (plugin paths), and error handling.

### W2: Add interactive wizard mode for complex CLI applications

Implement `app->interactive()` that presents a step-by-step wizard
when invoked without required arguments. Each option is prompted with
type-specific UI: text input for strings, selection for `Choice`
validators, file browser for `ExistingFile`, and numeric input with
range boundaries. Support back-navigation, validation at each step,
and a summary before execution. Changes span `App.hpp`, `Option.hpp`,
validators, the formatter, a new `Wizard.hpp` module, and the
error-handling path.

### W3: Implement automatic man page and documentation generation

Add `app->generate_man()` for roff-formatted man pages and
`app->generate_markdown()` for documentation. Support full subcommand
hierarchies with cross-references, option tables with types/defaults/
validators, environment variable documentation, and exit code
documentation. Changes span a new `DocGen.hpp` module, the formatter
(structured extraction), `App.hpp` metadata access, validator display
names, and a man page formatting engine.

### W4: Implement comprehensive input validation framework

Add a constraint system for cross-option validation: `requires`
(option A needs B), `conflicts` (mutually exclusive), `at_least_one`
(one of set required), and `implies` (A=x requires B=y). Constraints
validate post-parse and produce grouped error messages listing all
violations. Include help text integration showing constraint
relationships. Changes span `App.hpp`, `Option.hpp`, validators, the
formatter, error handling, and a new `Constraints.hpp` module.

### W5: Add internationalization support for help and error messages

Implement i18n across all user-facing output: error messages, help
text labels ("Usage:", "Options:", "Subcommands:"), validator error
strings, and built-in text. Support gettext-style catalogs, locale
detection, and user-provided translations for option descriptions.
Changes span `Error.hpp`, `Formatter_inl.hpp`, `Validators_inl.hpp`,
`App_inl.hpp`, `StringTools.hpp`, and a new `Locale.hpp` module.

### W6: Implement a middleware/hook system for parse lifecycle

Add hooks for the parse lifecycle: `before_parse`, `after_parse`,
`before_subcommand`, `after_subcommand`, `on_error`, and `on_exit`.
Hooks support ordering via priority, access to the parse context, and
both synchronous and callback-based registration. Changes span
`App.hpp` (hook registration), `App_inl.hpp` (invocation points),
`Option_inl.hpp` (per-option hooks), error handling, and the
callback system.

### W7: Add async command execution with task orchestration

Implement `app->async_run()` that executes subcommand callbacks
concurrently when they have no data dependencies. Add dependency
declarations between subcommands, a task graph executor, progress
reporting, and cancellation support. Changes span `App.hpp` (async
dispatch), a new `TaskGraph.hpp` module, the callback invocation
system, error handling for concurrent failures, and output
synchronization.

### W8: Implement configuration migration and versioning

Add a config versioning system that tracks schema versions in config
files and applies migration functions when loading older versions.
Support declarative migration rules (rename option, change type,
split/merge options), migration chain validation, backup before
migration, and dry-run mode. Changes span `Config.hpp`, a new
`Migration.hpp` module, `App.hpp` integration, the config reader/
writer, and error handling.

### W9: Add comprehensive testing framework integration

Implement a test harness that extends the parsing system for testing:
captured output comparison, argument simulation without `argv`,
fixture-based option sets, assertion helpers for parse results, and
snapshot testing for help text. Add a Google Test / Catch2 integration
header with custom matchers. Changes span a new `Testing.hpp` module,
`App.hpp` integration, output capture, the formatter, and build system
support.

### W10: Implement remote configuration and feature flags

Add support for fetching option defaults from a remote configuration
server (HTTP/gRPC). Include a caching layer, timeout/retry handling,
partial overrides (remote provides defaults, CLI overrides), change
notification callbacks, and integration with the existing config file
system for offline fallback. Changes span a new `RemoteConfig.hpp`
module, `Config.hpp` integration, `App.hpp` option resolution,
networking (HTTP client), caching, error handling, and serialization.
