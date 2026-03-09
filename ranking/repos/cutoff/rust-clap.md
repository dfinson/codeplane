# clap-rs/clap

| Field | Value |
|-------|-------|
| **URL** | https://github.com/clap-rs/clap |
| **License** | MIT OR Apache-2.0 |
| **Language** | Rust |
| **Scale** | Large |
| **Category** | CLI argument parser |
| **Set** | Cutoff |
| **Commit** | `1536fb6c6c9715ecd0d1560c4865d10be3d7b7df` |

## Why this repo

- **Well-structured**: Multi-crate workspace with clear separation:
  `clap_builder` (core argument parsing and help/error formatting),
  `clap_derive` (proc-macro for `#[derive(Parser)]`), `clap_complete`
  (shell completion generation), and `clap_lex` (low-level argv lexing).
  Each crate has focused responsibilities with minimal cross-coupling.
- **Rich history**: 14K+ stars, 5K+ commits. The dominant Rust CLI
  parsing library, used by ripgrep, bat, fd, cargo, and thousands of
  other Rust tools. PRs cover parser edge cases, derive macro
  improvements, completion engines, and error formatting.
- **Permissive**: Dual-licensed MIT OR Apache-2.0.

## Structure overview

```
clap/
├── clap_builder/
│   └── src/
│       ├── lib.rs                 # Re-exports, top-level API
│       ├── builder/
│       │   ├── command.rs         # Command struct — name, args, subcommands, settings
│       │   ├── arg.rs             # Arg struct — flags, options, positionals, value hints
│       │   ├── arg_group.rs       # ArgGroup — mutually exclusive / co-occurring args
│       │   ├── value_parser.rs    # ValueParser — typed parsing of flag values
│       │   ├── styled_str.rs      # Styled terminal strings for help/error output
│       │   └── action.rs          # ArgAction — Set, SetTrue, Count, Append, Help, Version
│       ├── parser/
│       │   ├── parser.rs          # Core argument parser — matches argv to Arg definitions
│       │   ├── arg_matcher.rs     # ArgMatcher — tracks which args have been matched
│       │   ├── matches.rs         # ArgMatches — query results after parsing
│       │   └── validator.rs       # Post-parse validation — required args, conflicts, groups
│       ├── output/
│       │   ├── help.rs            # Help message generation — long/short help formatting
│       │   ├── help_template.rs   # Customizable help templates
│       │   └── usage.rs           # Usage string generation
│       ├── error/
│       │   ├── mod.rs             # Error type and ErrorKind enum
│       │   └── format.rs          # Error message formatting and context rendering
│       └── util/
│           └── id.rs              # Id type for interning arg/group names
├── clap_derive/
│   └── src/
│       ├── lib.rs                 # Proc-macro entry points — derive(Parser, Args, Subcommand, ValueEnum)
│       ├── derives/
│       │   ├── parser.rs          # Parser derive — top-level struct → Command
│       │   ├── args.rs            # Args derive — struct fields → Arg definitions
│       │   ├── subcommand.rs      # Subcommand derive — enum variants → subcommands
│       │   └── value_enum.rs      # ValueEnum derive — enum → string value mapping
│       └── attr.rs                # Attribute parsing — #[arg(...)], #[command(...)]
├── clap_complete/
│   └── src/
│       ├── lib.rs                 # Shell completion generation entry point
│       ├── shells/
│       │   ├── bash.rs            # Bash completion script generation
│       │   ├── zsh.rs             # Zsh completion script generation
│       │   ├── fish.rs            # Fish completion script generation
│       │   ├── powershell.rs      # PowerShell completion script generation
│       │   └── elvish.rs          # Elvish completion script generation
│       ├── dynamic/
│       │   └── completer.rs       # Dynamic (runtime) completion engine
│       └── aot/                   # Ahead-of-time completion generation
│           └── generator.rs       # Generator trait and generate() function
├── clap_lex/
│   └── src/
│       └── lib.rs                 # Low-level argv lexer — OsStr splitting, -- handling, short/long flag parsing
└── examples/
    ├── derive_ref/                # Derive-style examples
    └── tutorial_builder/          # Builder-style examples
```

## Scale indicators

- ~50 Rust source files across 4 crates
- ~25K lines of code (builder crate alone is ~15K)
- Workspace layout with clear crate boundaries
- Dependencies: minimal for core (just `clap_lex`); `syn`/`quote`/`proc-macro2` for derive

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix debug assertions not detecting self-referencing ArgGroup requirements

When an `ArgGroup` lists one of its own member args in its `requires`
list (e.g., group `"io"` contains `["input", "output"]` and requires
`["input"]`), the constraint is tautological but goes undetected.
The assertion pass in `builder/debug_asserts.rs` validates that
required IDs exist in the command but does not check whether a group
requires one of its own members. Fix `assert_app` to detect and
report self-referencing group requirements.

### N2: Fix usage string not showing mutual exclusion between arg groups

When two `ArgGroup`s are marked mutually exclusive, the usage string
generated by `output/usage.rs` lists both groups' args without
indicating they are alternatives. Fix the usage renderer to wrap
mutually exclusive groups in `( A | B )` syntax.

### N3: Fix help output misaligning long flag descriptions when terminal is narrow

When the terminal width is small (e.g., 40 columns), long flag names
cause the description column to wrap incorrectly, producing jagged
output. Fix the column-width calculation in `output/help.rs` to
properly account for flag name length and fall back to stacked layout
when space is insufficient.

### N4: Fix elvish completion generator not escaping special characters in descriptions

When a command or arg description contains characters special in
Elvish syntax (backticks, single quotes, or dollar signs), the
generated completion script in `clap_complete/src/aot/shells/elvish.rs`
produces syntax errors because descriptions are inserted into Elvish
string literals without escaping. Fix the elvish generator to escape
special characters in descriptions before emitting them. Also update
the shell-specific documentation in `README.md` to note the supported
character set for descriptions in generated Elvish completions.

### N5: Fix error format not showing valid values for custom `PossibleValuesParser` failures

When a `PossibleValuesParser` rejects an input, the error message
in `error/format.rs` shows the invalid value and the flag name but
does not list the valid possible values unless they were set via
`Arg::value_parser(["a", "b", "c"])`. When a `PossibleValuesParser`
is constructed manually and passed as a `ValueParser`, the possible
values are not extracted for the error context. Fix error construction
to include possible values from the `ValueParser` metadata.

### N6: Fix clap_lex not treating negative numbers as values when `allow_negative_numbers` is not set

When an argument like `--offset` expects a numeric value and the user
passes `--offset -5`, the lexer in `clap_lex/src/lib.rs` classifies
`-5` as a short flag cluster via `to_short()` because
`is_negative_number()` is only checked in specific parser paths.
The parser in `parser/parser.rs` then reports an unknown flag `-5`
instead of treating it as the value for `--offset`. Fix the parser's
value-reading logic to check `is_negative_number()` before
interpreting a token as flags.

### N7: Fix fish completion generator's `escape_name` not sanitizing binary names containing dots or special characters

The `escape_name` function in `clap_complete/src/aot/shells/fish.rs`
only replaces hyphens with underscores (`name.replace('-', "_")`).
When a binary name contains dots, colons, or other non-alphanumeric
characters (e.g., `my.app` or `cargo:test`), the generated fish
function names like `__fish_my.app_needs_command` contain characters
that are invalid in fish identifiers, producing syntax errors in
the completion script. Fix `escape_name` to also replace dots,
colons, and other non-alphanumeric, non-underscore characters with
underscores.

### N8: Fix Command::display_name not propagating to subcommand error messages

When `Command::display_name("my-tool")` is set on the root command,
subcommand errors still show the binary name from argv[0] instead of
the display name. Fix error context assembly in `error/mod.rs` to use
`display_name` if set, walking up the command tree.

### N9: Fix validator not reporting all group conflicts when multiple groups are violated simultaneously

When a user provides flags that violate two or more `ArgGroup`
constraints at the same time (e.g., both a mutually-exclusive pair and
a missing required-together pair), the validator in
`parser/validator.rs` short-circuits after the first violation and
only reports one error. Fix the group validation pass to collect all
group constraint violations before returning, and format them as a
combined error listing each violated group and its members.

### N10: Fix `zsh` completion generator not handling subcommands with hyphens in names

When a subcommand name contains hyphens (e.g., `my-subcommand`), the
zsh completion script in `clap_complete/src/aot/shells/zsh.rs` produces
a function name with hyphens, which is invalid zsh syntax. The
generator should replace hyphens with underscores in generated function
names while preserving the original hyphenated name in the completion
text and descriptions.

## Medium

### M1: Implement value hint-driven shell completions for common types

Add rich runtime completions driven by `ValueHint`: `FilePath` triggers
filesystem completion, `Url` suggests common URL schemes (`http://`,
`https://`, `ftp://`), `EmailAddress` emits a descriptive placeholder
candidate, `Hostname` reads `/etc/hosts` for candidate hostnames.
Requires changes to all five shell generators in `clap_complete/src/aot/shells/`
(bash, zsh, fish, elvish, powershell), the `ValueHint` enum in `clap_builder`,
the dynamic completion engine in `clap_complete/src/engine/`, and
integration tests per shell.

### M2: Add ArgGroup conflict diagnostics with visualization

When a user violates an arg group constraint (required-together,
mutually-exclusive), the error message is generic. Implement rich
diagnostics that name the conflicting group, list all member args, show
which args were provided vs. missing, and render an ASCII-art diagram
of the group relationships. Changes span `error/format.rs`,
`builder/arg_group.rs`, `parser/validator.rs`, and the help template
system.

### M3: Implement derive macro support for flattening external crate args

Add `#[command(flatten_external = "some_crate::Options")]` that can
flatten arg structs defined in external crates without requiring them to
derive `Args`. Generate a runtime adapter that reads the external
struct's fields via a registry trait. Changes span `clap_derive/src/derives/args.rs`,
a new trait in `clap_builder`, attribute parsing in `attr.rs`, code
generation templates, and cross-crate integration tests.

### M4: Add contextual help snippets in error messages

When a parse error references a specific argument or subcommand, embed
the relevant help text inline in the error message instead of only
suggesting `--help`. For example, a missing required arg error should
include that arg's short description, valid values, and default. Requires
changes to `error/format.rs` to fetch arg metadata, integration with
`output/help.rs` for single-argument formatting, respect for terminal
width via `builder/styled_str.rs`, and a configuration knob on
`Command` to disable inline help for brevity.

### M5: Implement JSON schema generation for the command tree

Add `Command::to_json_schema() -> serde_json::Value` that outputs the
full command tree as a JSON document: command names, descriptions,
args with types and constraints (required, default, possible values),
subcommands, and arg groups. Support filtering hidden commands and
deprecated args. Requires introspection of `builder/command.rs` and
`builder/arg.rs` fields, a schema builder that walks the command tree,
serialization of `ValueParser` type information, `output/` module
integration for consistent descriptions, and optional `serde`
feature-gating in the `clap_builder` crate.

### M6: Add custom error formatting with user-defined templates

Implement `Command::error_template(tmpl)` that lets users define error
message layout using a template string (similar to help templates).
Support placeholders for error kind, invalid value, valid values, usage,
and suggestion. Requires a template parser in `error/`, integration with
`Error::format()`, fallback to default formatting, and validation that
template placeholders are valid.

### M7: Implement completion script auto-installation

Add a `clap_complete::install()` function that detects the user's shell,
writes the completion script to the appropriate config directory
(`~/.bash_completion.d/`, `~/.config/fish/completions/`, etc.), and
prints instructions for sourcing. Support `--dry-run` to preview.
Requires shell detection logic, platform-specific path resolution,
file writing with permission handling, existing-file backup, and
integration with `clap_complete`'s generator system. Also update
`CONTRIBUTING.md` with platform-specific testing guidelines for
the install paths and add a CI job in `.github/workflows/ci.yml`
that validates install-path resolution on Linux, macOS, and Windows.

### M8: Add help output customization with section reordering and filtering

Implement `Command::help_config(HelpConfig)` that controls help output
layout: reorder sections (usage, description, args, subcommands,
footer), hide specific sections, set custom section headers, and
control whether hidden args are shown with `--help-all`. Requires a
`HelpConfig` struct in `builder/`, integration with the template
engine in `output/help_template.rs`, changes to `output/help.rs` for
section rendering control, and derive support via
`#[command(help_config = ...)]` in `clap_derive/src/derives/parser.rs`.

### M9: Implement value validation with custom error messages

Add `Arg::value_parser(RangedU64ValueParser::new().range(1..=100))` style
validators that produce structured errors on failure. Support composing
validators (e.g., range + regex), custom error messages per validator,
and integration with derive via `#[arg(value_parser = ...)]`. Changes
span `builder/value_parser.rs`, error construction, derive codegen in
`clap_derive`, and the help system to show valid ranges.

### M10: Add shell-agnostic completion testing framework

Implement a `clap_complete::testing::CompletionTester` that simulates
completion requests for any shell backend and returns structured
results. Support testing positional completions, flag-value completions,
subcommand completions, and description formatting. Requires a new
testing module in `clap_complete`, internal refactoring to separate
output from logic, mock filesystem for path completions, and example
tests.

## Wide

### W1: Implement a command versioning and deprecation lifecycle

Add version-aware command and arg metadata: `Arg::since("2.0")`,
`Arg::deprecated_since("3.0", "use --format instead")`,
`Command::removed_in("4.0")`. At runtime, compare against the app
version to warn or error. Generate migration guides listing changes
between versions. Changes span `builder/command.rs`, `builder/arg.rs`,
the parser for warning emission, error formatting, help rendering,
derive macro attribute handling, completion generators to mark
deprecated items, and a new migration-guide output module.

### W2: Add async command execution with tokio integration

Implement `Command::execute_async()` and derive support for
`#[command(async)]` that runs `RunE` as an async function on a tokio
runtime. Support graceful shutdown via `CancellationToken`, async
`PreRunE`/`PostRunE` hooks, and timeout enforcement per command.
Changes span the command execution pipeline, a new async module in
`clap_builder`, derive codegen for async trait methods, context
propagation, error handling for `JoinError`, and the testing framework
for async command tests.

### W3: Implement a plugin system for dynamically discovered subcommands

Add plugin discovery from: (a) binaries on `$PATH` matching a naming
convention (e.g., `myapp-plugin-*`), (b) a plugin directory, and (c)
dynamically loaded `.so`/`.dylib` libraries. Support plugin metadata
(version, description, expected clap version), conflict detection, and
a built-in `plugin list` subcommand. Changes span command resolution
in `builder/command.rs`, a new plugin module, completion support for
plugin commands, help formatting for plugin sections, derive integration
for plugin traits, and security considerations for dynamic loading.

### W4: Implement a TUI-based interactive mode for missing arguments

When required arguments are missing, instead of printing an error, enter
an interactive TUI mode that prompts for each missing argument with
type-appropriate widgets: text input for strings, selection list for
`ValueEnum`, file picker for `ValueHint::FilePath`, toggle for bools,
and spinner for numbers. Show validation inline and a final summary
before execution. Changes span the command execution pipeline, a new
`interactive` module, `ValueHint` and `ValueEnum` introspection,
terminal raw-mode handling, derive integration for prompt customization,
and a testing harness for TUI simulation.

### W5: Add comprehensive man page and markdown documentation generation

Rewrite the documentation generator to produce high-quality man pages
(with proper `.TH`, `.SH` sections, cross-references), markdown with
front-matter for static site generators, and a JSON schema describing
the full CLI interface. Support custom templates, multi-level command
trees with a table-of-contents index page, example rendering, exit-code
documentation, and environment variable sections. Changes span a new
`clap_doc` crate, template engine integration, command tree traversal,
arg introspection, the derive macro for doc attributes, and output
format abstraction.

### W6: Implement distributed completion with server-client architecture

Add a completion server mode where a long-running daemon caches the
command tree and provides completions over a Unix socket or TCP, enabling
instant completions for complex CLIs with expensive initialization.
Support lazy subcommand loading, result caching with invalidation,
multiple concurrent shells, and graceful degradation to static
completions. Changes span a new `clap_complete_server` crate, the
shell generators for client-mode scripts, a daemon process module,
IPC protocol definition, the dynamic completer, and integration tests
with simulated shells.

### W7: Add OpenTelemetry instrumentation across the CLI lifecycle

Instrument the full parse-validate-execute lifecycle with OpenTelemetry:
trace spans for arg parsing, validation, hook execution, and command
invocation. Emit metrics for parse duration, error frequency, flag usage
statistics, and subcommand popularity. Support configurable exporters
(stdout, OTLP, Jaeger). Changes span the parser, validator, command
execution pipeline, a new telemetry module in `clap_builder`, derive
integration for span attributes, `clap_complete` instrumentation, and
conditional compilation to keep telemetry optional.

### W8: Implement first-class configuration file integration

Build a config-file system that automatically binds args to keys in
TOML/YAML/JSON config files. Support per-subcommand config sections,
config-file discovery (XDG, `--config` flag, env var), precedence
(CLI > env > config > default), config file generation from the command
tree, and validation of config-file contents against the arg schema.
Changes span `builder/arg.rs` for config key metadata, a new config
module, the parser for config-source fallback, `output/help.rs` to show
config keys, derive attributes for config mapping, and a config-file
generator utility.

### W9: Implement multi-language help and error message localization

Add i18n support across all user-facing strings: help labels ("Usage:",
"Arguments:", "Options:"), error messages, suggestion text, completion
instructions, and value-enum descriptions. Use Rust's `fluent` crate
for message formatting with locale negotiation from `$LANG`/`$LC_ALL`.
Support user-provided translations for command descriptions via derive
attributes. Changes span `output/help.rs`, `output/usage.rs`,
`error/format.rs`, `builder/styled_str.rs`, the derive macro for i18n
attributes, completion generators, and a message catalog build system.

### W10: Implement a compatibility layer for migrating from structopt and clap v2

Build a compatibility shim that allows codebases using structopt or
clap v2 APIs to compile against clap v4 with minimal changes. Map
structopt's `#[structopt(...)]` attributes to clap derive equivalents,
translate clap v2 builder patterns (`Arg::with_name().required(true)`)
to v4 API (`Arg::new().required(true)`), emit deprecation warnings for
translated APIs, and provide a migration tool that rewrites source
code. Changes span a new `clap_compat` crate, proc-macro re-exports,
API adapters in `clap_builder`, a source-rewriting tool, and
comprehensive test suites covering both legacy APIs.

### N11: Fix CHANGELOG.md 5.0.0 section not using Keep a Changelog link-reference format

The `CHANGELOG.md` uses link-reference format (`## [4.5.60]`, `## [Unreleased]`)
for all version entries, with corresponding `[version]: https://...` URLs in the
reference section at the bottom. However, the `5.0.0` entry uses the bare header
format `## 5.0.0 - TBD` instead of `## [5.0.0] - TBD`, making it inconsistent
with every other version and preventing it from rendering as a clickable link in
GitHub's markdown viewer. Fix `CHANGELOG.md` to:
1. Change `## 5.0.0 - TBD` to `## [5.0.0] - TBD`
2. Add a `[5.0.0]: https://github.com/clap-rs/clap/compare/v4.5.60...HEAD`
   reference link in the reference section at the bottom of the file,
   alongside the existing `[Unreleased]` and `[4.5.60]` entries.

### M11: Migrate advisory vulnerability checking from deprecated action to cargo-deny

The `.github/workflows/audit.yml` has two security jobs: `security_audit`
uses the archived `actions-rs/audit-check@v1` action, and `cargo_deny`
runs `cargo deny check bans licenses sources` but omits `advisories`.
The `deny.toml` [advisories] section exists but has its `db-urls` field
commented out, leaving it unconfigured. Migrate all advisory checking to
cargo-deny: (1) configure `deny.toml` [advisories] by uncommenting and
setting `db-urls = ["https://github.com/rustsec/advisory-db"]` and adding
`vulnerability = "deny"`, (2) update the `cargo_deny` matrix in `audit.yml`
to check `bans licenses sources advisories` and remove the deprecated
`security_audit` job, (3) change `multiple-versions` in `deny.toml` [bans]
from `"warn"` to `"deny"` to enforce stricter dependency hygiene, and
(4) update `CONTRIBUTING.md` to document running `cargo deny check` locally.

### W11: Prepare workspace for v5 release across all non-code configuration files

Prepare the clap workspace for the v5 major release by updating all
non-code configuration and documentation files. Update the workspace
`Cargo.toml` to bump `rust-version` (MSRV) from `1.74` to `1.80`
and update the `workspace.package` edition to `2024`. Update
`CHANGELOG.md` to finalize the `5.0.0` section with all breaking
changes and migration notes. Revise `CONTRIBUTING.md` to document
the v5 branching strategy and deprecation removal process. Update
`deny.toml` to add new workspace members (`clap_complete_nushell`,
`clap_mangen`) to the license audit scope. Update `release.toml`
with the v5 version bump configuration for all crates. Update
`CITATION.cff` with the new version and release date. Revise
`.github/workflows/ci.yml` to add a `msrv` job that tests against
the new MSRV and update the `rust-next` workflow to test against
the latest nightly. Update `README.md` badges to reflect the new
MSRV and link to the v5 migration guide.
