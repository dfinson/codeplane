# pallets/click

| Field | Value |
|-------|-------|
| **URL** | https://github.com/pallets/click |
| **License** | BSD-3-Clause |
| **Language** | Python |
| **Scale** | Medium |
| **Category** | CLI framework |
| **Set** | Cutoff |
| **Commit** | `cdab890e57a30a9f437b88ce9652f7bfce980c1f` |

## Why this repo

- **Well-structured**: Clean single-package layout under `click/` with
  clear per-concern modules — command/group hierarchy (`core.py`),
  parameter type system (`types.py`), decorator API (`decorators.py`),
  option parsing (`parser.py`), help formatting (`formatting.py`),
  shell completion (`shell_completion.py`), and test harness
  (`testing.py`). One developer can follow the full call chain from
  decorator to parsed result.
- **Rich history**: 2K+ commits, 15K+ stars. One of the most widely
  used Python CLI libraries. PRs cover parsing edge cases, completion
  improvements, and type system extensions.
- **Permissive**: BSD-3-Clause license.

## Structure overview

```
click/
├── __init__.py            # Public API re-exports
├── core.py                # Command, Group, MultiCommand, Context, BaseCommand
├── types.py               # Type system — INT, FLOAT, STRING, Path, Choice, etc.
├── decorators.py          # @command, @option, @argument, @group, @pass_context
├── parser.py              # Option/argument tokenizer and parser
├── formatting.py          # HelpFormatter for --help output
├── utils.py               # echo, get_terminal_size, launch, open_file
├── exceptions.py          # ClickException, BadParameter, MissingParameter, Abort
├── testing.py             # CliRunner for invoking commands in tests
├── shell_completion.py    # Bash/Zsh/Fish completion generation
├── globals.py             # Thread-local context stack
├── termui.py              # Terminal UI helpers — progress bars, prompts, colors
└── _compat.py             # Python version compatibility shims
```

## Scale indicators

- ~15 Python source files
- ~10K lines of code
- Flat structure (1 level, single package)
- Minimal dependencies (only `colorama` on Windows)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add closest-match suggestion to Choice invalid value error message

When a user enters an invalid `Choice` value (e.g., `--env prodction`),
the error message lists all valid choices but does not suggest the
closest match. Add string-similarity matching (e.g., Levenshtein
distance) to `Choice.get_invalid_choice_message()` in `types.py` so the
error includes a "Did you mean 'production'?" suggestion when a close
match exists among the normalized choices.

### N2: Add ANSI escape sequence awareness to custom text wrapper

Click's custom `TextWrapper` class in `_textwrap.py` inherits
`_wrap_chunks()` from `textwrap.TextWrapper`, which uses `len()` to
measure chunk widths. When styled text containing ANSI escape sequences
(e.g., `\x1b[31m`) is passed through the help formatter, the ANSI
codes inflate the measured width and cause premature line breaks.
`_compat.py` already provides `strip_ansi()`. Add a `_wlen()` method to
`TextWrapper` in `_textwrap.py` that calls `strip_ansi()` before
computing visible string length, and override `_wrap_chunks()` to use
`self._wlen()` instead of `len()` when measuring chunks.

### N3: Fix echo() raising bare UnicodeEncodeError on narrow-encoding streams

When `echo()` writes to a stream with a narrow encoding (e.g., a file
opened with `encoding='ascii'` and `errors='strict'`), a
`UnicodeEncodeError` is raised from the bare `file.write(out)` call
with no contextual message, making it difficult for users to diagnose
the encoding problem. Fix `echo()` in `utils.py` to catch
`UnicodeEncodeError` at the `file.write()` call and re-encode the
output using `errors='replace'` as a fallback, emitting a warning or
replacing unencodable characters rather than aborting with a raw
exception. Also update `CHANGES.rst` to document the new encoding
fallback behavior for narrow-encoding streams.

### N4: Add glob expansion support to Path type

The `Path` type in `types.py` validates individual paths but does not
support shell-style glob patterns (e.g., `--input "*.csv"`). When a
user passes a glob pattern to a `Path` option, the literal string
`*.csv` is validated for existence and fails. Add a `resolve_glob`
parameter to `Path` that expands glob patterns via `glob.glob()` before
validation, returning the list of matched paths.

### N5: Add timeout parameter to prompt() in termui

The `prompt()` function in `termui.py` blocks indefinitely waiting for
user input, which is problematic for automated or CI environments where
no human is available. Add a `timeout` parameter (in seconds) to
`prompt()` that raises a `click.Abort` if the user does not respond
within the specified duration, using `select()` or `threading.Timer` for
the timeout mechanism.

### N6: Fix HelpFormatter.write_dl() ignoring current indentation in text width

`HelpFormatter.write_dl()` in `formatting.py` computes the help-text
column width as `max(self.width - first_col - 2, 10)`, but does not
subtract `self.current_indent`. Since `write_dl` is always called
inside a `section()` context that increments `self.current_indent`,
the resulting `text_width` is `self.current_indent` characters wider
than the remaining terminal space. On narrow terminals this allows
wrapped help text to extend past the intended right margin. Fix by
computing `text_width = max(self.width - self.current_indent - first_col - 2, 10)`
so the help text is constrained to the actual available width.

### N7: Add value normalization hook to _NumberRangeBase

The `_NumberRangeBase` class in `types.py` validates that a value falls
within `[min, max]` but does not offer a way to snap values to a step
grid (e.g., multiples of 5). Add a `step` parameter to `IntRange` and
`FloatRange` that rounds the converted value to the nearest valid step
before range validation, and update `_describe_range()` to include the
step in help text.

### N8: Fix shell completion not escaping special characters in values

When completing values that contain spaces or shell metacharacters (e.g.,
file paths with spaces), the completion output does not escape them,
causing the shell to misinterpret the completions. Fix the completion
formatting in the shell-completion module to properly quote or escape
values for each supported shell (bash, zsh, fish).

### N9: Add encoding errors parameter to CliRunner for non-UTF-8 testing

`CliRunner` in `testing.py` has a `charset` parameter for configuring
stream encoding, but the isolated stdout stream created in `invoke()` is
constructed via `_NamedTextIOWrapper` without an `errors` argument,
leaving it at the default `errors="strict"`. When `charset` is set to a
narrow encoding such as `ascii`, writing any non-ASCII character raises
`UnicodeEncodeError` rather than applying a replacement strategy. Add a
`charset_errors` parameter (default `"strict"`) to `CliRunner.__init__`
that is forwarded to the `_NamedTextIOWrapper` instances wrapping stdin
and stdout inside `invoke()`, so developers can write tests for
encoding-limited consoles without unexpected exceptions.

### N10: Add structured error context to MissingParameter exception

The `MissingParameter` exception in `exceptions.py` carries the
parameter name and type but does not include the full command path or
the set of parameters that _were_ provided, making it difficult for
programmatic consumers to generate actionable error reports. Add
`command_path` and `provided_params` attributes to `MissingParameter`
and populate them in `Parameter.process_value()` in `core.py`.

## Medium

### M1: Implement parameter groups for mutually exclusive options

Add support for declaring groups of options that are mutually exclusive:
if one is set, the others must not be. Requires a `mutually_exclusive()`
constraint that validates after all parameters are resolved, integrates
with the help formatter to display the exclusion relationship, and
produces a clear error message naming the conflicting options. Touches
the parameter processing in the core module, help generation in the
formatter, and validation in the decorators.

### M2: Add typed parameter overloads for @option and @argument

Introduce type-safe overloads for the `@option` and `@argument`
decorators that use `ParamSpec` and `Concatenate` to correctly type
the decorated function's signature. Currently mypy/pyright cannot infer
the types of parameters added by click decorators. Requires changes
to the decorators module, the type system exports, and the public API
type stubs.

### M3: Implement lazy subcommand loading for large CLI applications

Add a `LazyGroup` variant that accepts subcommands as import strings
(e.g., `"myapp.commands.deploy:cli"`) and only imports them when
invoked. Requires changes to the `Group.get_command()` and
`Group.list_commands()` methods, integration with shell completion so
lazy commands appear in completion results, and error handling for
import failures with clear diagnostics.

### M4: Add structured output support for help text

Implement a `--help-format` option that outputs help text as JSON or
YAML instead of plain text. The structured output should include the
command name, parameters with types and defaults, subcommand list, and
description. Requires a new formatter backend alongside `HelpFormatter`,
integration with `Command.format_help()`, and registration of the
format option as a special eager parameter.

### M5: Add context-level flag to display auto-derived environment variable names in help

Click's `Context` supports `auto_envvar_prefix` which automatically
maps `--option` flags to `PREFIX_OPTION` environment variables,
propagating hierarchically through nested command groups. However,
these auto-derived env var names are never shown in option help text
unless `show_envvar=True` is set individually on every option.
Add a `show_default_envvars` boolean parameter (default `False`) to
`Context.__init__` that, when enabled, causes every option that would
read from a `{auto_envvar_prefix}_{name.upper()}` env var to include
the env var name in its help output — without requiring per-option
`show_envvar=True`. Requires changes to `Context.__init__` and its
`make_info_name` path, `Option.get_help_extra()` to check the new
context flag, `Command.make_context()` to forward the parameter, and
the `@click.command` / `@click.group` decorators to expose it.

### M6: Add retry and timeout decorators for commands that call external services

Implement `@click.retry(max_attempts=3, backoff=1.0)` and
`@click.timeout(seconds=30)` decorators that wrap command callbacks.
Retry should catch configurable exception types and support exponential
backoff. Timeout should use signal-based or thread-based interruption.
Both should integrate with click's exception handling to produce
user-friendly error messages.

### M7: Implement parameter value callbacks with dependency injection

Add a `@option` parameter `inject=True` that resolves the option's
value by calling a provider function with the current `Context` and
other already-resolved parameter values. Requires changes to parameter
resolution order in the core module, cycle detection for dependent
parameters, and integration with the type system for validation of
injected values.

### M8: Add rich help formatting with terminal colors and styles

Implement a `RichHelpFormatter` that replaces `HelpFormatter` and uses
ANSI styles to colorize help output: bold command names, dim metavars,
colored section headings, and syntax-highlighted default values. Support
style customization via a theme dictionary. Requires a new formatter
class, integration with `Context.formatter_class`, and graceful
degradation when color is disabled.

### M9: Implement command aliases with completion support

Add support for command aliases in `Group`: `@cli.command(aliases=['rm', 'del'])`. Aliases should work in invocation, help text
(listed alongside the primary name), and shell completion. Requires
changes to `Group.get_command()` for alias resolution,
`Group.format_commands()` for display, and the completion module for
alias enumeration.

### M10: Add configuration file support as a parameter source

Implement `@click.config_option('--config', format='toml')` that reads
parameter values from a configuration file. Support TOML, JSON, and INI
formats. Config values should have lower precedence than command-line
arguments but higher than defaults. Requires a config file reader, a
custom parameter source integrated into `Context.params` resolution, and
error reporting for malformed config files.

## Wide

### W1: Implement a plugin system for dynamically discovered commands

Add a plugin architecture that discovers and loads commands from entry
points (`click.plugins` group), namespace packages, or a plugin
directory. Support plugin metadata (version, author, description),
dependency ordering between plugins, conflict detection when multiple
plugins provide the same command name, and a `plugin list` built-in
command that displays discovered plugins. Changes span the group/multi-
command hierarchy, context initialization, entry-point scanning, and
shell completion for plugin-provided commands.

### W2: Add async command support throughout the framework

Implement first-class support for `async def` command callbacks.
`@click.command` should detect async functions and run them in an event
loop. `Context.invoke()` should support awaiting async callbacks.
`CliRunner` should handle async commands transparently. Progress bars
and prompts should work within async contexts. Changes span the
command invocation path in `core.py`, the decorator layer, the testing
module, and the terminal UI helpers.

### W3: Implement a declarative command configuration system

Add a YAML/TOML-based command definition format that generates click
command trees without Python code. Support parameter definitions with
types, defaults, and validation; subcommand hierarchies; help text;
and callback references to Python functions. Include a validator that
checks configuration files for errors. Changes span the core module
(a new command builder), the type system (type resolution from strings),
the decorator layer (configuration loading), and a new configuration
parser module.

### W4: Implement comprehensive parameter validation framework

Add a validation system that supports cross-parameter constraints:
`requires` (option A needs option B), `conflicts` (mutually exclusive),
`at_least_one` (one of a set must be provided), and `depends_on` (value
of one option constrains another). Validations should run after all
parameters are resolved, produce grouped error messages listing all
violations, and integrate with help text to display constraints. Changes
span the core module, parameter processing, help formatting, error
handling, and the testing module.

### W5: Add per-Context gettext support for runtime-switchable translations

Click already uses `from gettext import gettext as _` at module level in
every source file, so built-in strings are extractable and the standard
gettext locale lookup works. What is missing is the ability to supply a
different translation function per `Context` — e.g., to serve different
locales in the same process or to inject test doubles. Add a `gettext`
callable parameter (default: the standard `gettext.gettext`) to
`Context.__init__`; store it as `self.gettext` and `self.ngettext`. Replace
all bare module-level `_()` and `ngettext()` calls in `core.py`,
`exceptions.py`, `formatting.py`, `parser.py`, `termui.py`,
`_termui_impl.py`, and `shell_completion.py` with `ctx.gettext()` /
`ctx.ngettext()`, threading the context through where it is already
available. Update `Context.__repr__`, error formatting, and help
formatting paths so they all resolve through the context's translation
callable. Add a `locale` utility helper that constructs a gettext
translator from a `.po` catalog for a given locale string and returns a
`Context`-compatible gettext function.

### W6: Implement a middleware/hook system for command lifecycle

Add pre/post hooks for the command lifecycle: `before_parse`,
`after_parse`, `before_invoke`, `after_invoke`, `on_error`, and
`on_close`. Hooks should support ordering via priority, async hooks,
and access to the context. Support both decorator-based and
programmatic hook registration. Changes span context management, the
command invocation pipeline, the group dispatching logic, error
handling, and the decorator API.

### W7: Implement interactive command wizards

Add `@click.wizard` that presents an interactive step-by-step interface
for complex commands. When invoked without required arguments, the
wizard prompts for each parameter with type-specific UI: text input
for strings, selection menus for `Choice` types, file browser for
`Path` types, and confirmation for flags. Support back-navigation,
validation at each step, and a summary screen before execution. Changes
span the core command invocation, parameter types, the terminal UI
module, the prompt system, and the testing module for wizard simulation.

### W8: Add comprehensive shell completion rewrite

Rewrite the shell completion system to support dynamic completions
based on context: complete option values by calling type-specific
completers (file path completion for `Path`, value enumeration for
`Choice`), complete based on previously supplied options, and support
completion for chained commands. Add completion testing utilities to
`CliRunner`. Changes span the completion module, all parameter types,
the parser, the core command classes, and the testing module.

### W9: Implement a command-line application testing framework

Extend `CliRunner` into a full testing framework: snapshot testing for
help text output, structured result objects with parsed stdout/stderr,
filesystem fixtures for `Path` parameters, environment variable
fixtures, mock input streams for interactive prompts, and assertion
helpers for exit codes and output patterns. Add a pytest plugin that
provides click-specific fixtures. Changes span the testing module,
a new pytest plugin module, the CLI runner isolation logic, and
integration with the exception hierarchy.

### W10: Add automatic man page and documentation generation

Implement `click.docs.generate_man(cli)` that produces roff-formatted
man pages from a click command tree, and `click.docs.generate_rst(cli)`
for Sphinx documentation. Support full command hierarchies with
cross-references between subcommands, parameter tables with types and
defaults, environment variable documentation, and exit code
documentation. Changes span a new documentation generator module,
the help formatter for structured extraction, the core module for
metadata access, and the type system for type name rendering.

### N11: Update `CHANGES.rst` with categorized changelog entries and anchor labels

The `CHANGES.rst` file's unreleased section currently uses a flat list
with no category groupings, and no version section has RST anchor labels,
so Sphinx cross-references to specific versions do not resolve on
ReadTheDocs. Restructure the unreleased section to group entries under
subsection headings — Bug Fixes, Features, Deprecations, and Breaking
Changes — placing the existing entry under Bug Fixes and adding
placeholder subheadings for future use. Add RST anchor labels (e.g.,
``.. _changelog-8-3-1:``) before each released version heading so that
documentation pages can cross-reference specific changelog sections.

### M11: Add `[project.optional-dependencies]` extras to pyproject.toml

The `pyproject.toml` uses PEP 735 `[dependency-groups]` (a uv-specific
format) for development, testing, and documentation dependencies, but
does not declare `[project.optional-dependencies]` extras. Without
extras, users installing with standard pip cannot install subsets of
dependencies via `pip install click[dev]` or `pip install click[docs]`.
Add `[project.optional-dependencies]` groups `dev`, `test`, and `docs`
that mirror the corresponding `[dependency-groups]` entries. Update
`.readthedocs.yaml` to install via the `docs` extra in its pip install
step (in addition to or instead of the uv group) so the build works
in ReadTheDocs' standard pip environment. Update the contributing docs
or inline comments in `pyproject.toml` to explain the distinction
between the two dependency mechanisms.

### W11: Overhaul project metadata, README examples, and configuration files

Perform a comprehensive non-code refresh across project configuration and
documentation files. Update `pyproject.toml` classifiers to include
Python version and implementation classifiers (e.g., `"Programming
Language :: Python :: 3.10"` through `"Programming Language :: Python
:: 3.13"`, `"Programming Language :: Python :: Implementation ::
CPython"`). Revise the `README.md` "A Simple Example" section to show
modern usage patterns including type annotations on command callbacks and
a shell completion setup snippet. Update `CHANGES.rst` to use a
consistent entry format with contributor attribution (`:pr:` and
`:issue:` references) throughout the unreleased section, and add RST
anchor labels to each version heading for Sphinx cross-reference
support. Update `.editorconfig` to cover `.toml` files with a dedicated
`[*.toml]` section specifying appropriate indent settings. Update
`.pre-commit-config.yaml` to add a `check-yaml` hook from the
pre-commit-hooks repo alongside the existing hooks.
