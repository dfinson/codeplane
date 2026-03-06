# fastapi/typer

| Field | Value |
|-------|-------|
| **URL** | https://github.com/fastapi/typer |
| **License** | MIT |
| **Language** | Python |
| **Scale** | Medium |
| **Category** | CLI framework |
| **Set** | Cutoff |

## Why this repo

- **Type-hint-driven design**: Builds CLI applications from Python
  function signatures and type annotations. The `main.py` module
  introspects callbacks to auto-generate click `Option` and `Argument`
  parameters, while `core.py` provides `TyperCommand`, `TyperGroup`,
  `TyperOption`, and `TyperArgument` subclasses of click's internals.
  Rich help formatting in `rich_utils.py` and shell completion in
  `completion.py` round out the developer experience.
- **Clean layering over click**: Single `typer/` package with clear
  separation — `models.py` for data classes, `params.py` for
  `Option`/`Argument` parameter builders with extensive overloads,
  `testing.py` for `CliRunner`, and `utils.py` for function
  introspection. ~7K lines of code across 16 modules.
- **Permissive**: MIT license.

## Structure overview

```
typer/
├── __init__.py               # Public API re-exports (echo, style, Typer, etc.)
├── __main__.py               # Entry point for `python -m typer`
├── _completion_classes.py    # Shell-specific completion class overrides
├── _completion_shared.py     # Shared completion logic — script gen, install
├── _types.py                 # TyperChoice and internal type helpers
├── _typing.py                # get_origin, get_args, is_union, literal helpers
├── cli.py                    # `typer` CLI app — run/utils subcommands
├── colors.py                 # Color name constants (BLACK, RED, GREEN, etc.)
├── completion.py             # Completion callback, install, show integration
├── core.py                   # TyperCommand, TyperGroup, TyperOption, TyperArgument
├── main.py                   # Typer class, type→click conversion, command gen
├── models.py                 # OptionInfo, ArgumentInfo, Context, TyperInfo, etc.
├── params.py                 # Option() and Argument() builders with overloads
├── rich_utils.py             # Rich-powered help formatting (panels, tables)
├── testing.py                # CliRunner wrapper for Typer apps
└── utils.py                  # get_params_from_function, parameter introspection
```

## Scale indicators

- ~16 Python source files
- ~7K lines of code
- Flat structure (single package)
- Dependencies: `click`, `rich`, `shellingham`, `typing-extensions`

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add `version_callback` shortcut to `Typer.__init__`

The `Typer` class in `main.py` requires users to manually create a
`--version` callback with `@app.callback()` and an eager `Option`.
Add a `version` parameter to `Typer.__init__` that, when provided,
automatically registers a `--version` option with an eager callback
that prints the version string and exits. Wire it through the
`TyperInfo` model in `models.py`.

### N2: Fix `get_params_from_function` not handling `*args` parameters

The `get_params_from_function` utility in `utils.py` introspects
function signatures to extract parameter metadata but silently ignores
`*args` (VAR_POSITIONAL) parameters. When a callback like
`def cmd(files: list[str], *extra: str)` is registered, the `extra`
parameter is lost. Fix `get_params_from_function` to detect
VAR_POSITIONAL parameters and either convert them to a variadic
`Argument` or raise a clear error explaining the limitation.

### N3: Add `min_value` and `max_value` support to integer `Option` parameters

When a Typer callback declares `count: int = Option(...)`, there is
no way to specify valid value bounds directly in the `Option` call. The
user must write a custom callback for range validation. Add `min` and
`max` parameters to `Option` in `params.py` that generate a
`click.IntRange` type when the annotated type is `int`, and update
the `OptionInfo` model in `models.py` to carry the bounds through to
the click parameter construction in `main.py`.

### N4: Fix Rich help panel not respecting `no_args_is_help` for TyperGroup

When a `TyperGroup` is invoked with no arguments and `no_args_is_help`
is set, the Rich help formatter in `rich_utils.py` is bypassed and
click's plain-text help is shown instead. This happens because the
`TyperGroup.invoke` method in `core.py` calls the parent `invoke`
before the Rich formatter can intercept. Fix `TyperGroup.invoke` to
use the Rich formatter when displaying the "no arguments" help message.

### N5: Add `deprecated` parameter to `@app.command()` decorator

The `Typer` class in `main.py` supports adding commands via
`@app.command()` but has no way to mark a command as deprecated.
Add a `deprecated: bool = False` parameter to `CommandInfo` in
`models.py` and to the `@app.command()` decorator in `main.py`.
When `deprecated=True`, prefix the help text with "[deprecated]" in
`rich_utils.py` and emit a warning via `click.echo` when the command
is invoked.

### N6: Fix shell completion not working for `Enum` type parameters

When a Typer callback uses an `Enum` type annotation (e.g.,
`color: Color` where `Color` is a `str` enum), the shell completion
system in `_completion_classes.py` does not provide completion
candidates for the enum values. Fix the completion flow to detect
`Enum` types in `TyperOption` and `TyperArgument` in `core.py` and
generate `CompletionItem` entries for each enum member.

### N7: Add `envvar` display to Rich help output

When an option is configured with `envvar="MY_VAR"`, click stores the
environment variable name but Typer's Rich help formatter in
`rich_utils.py` does not display it alongside the option in the help
panel. Add environment variable display (e.g., `[env: MY_VAR]`) to the
option rows generated by the Rich help formatter, reading the `envvar`
attribute from each `click.Option` parameter.

### N8: Fix `CliRunner` not capturing Rich-formatted output in tests

The `CliRunner` in `testing.py` wraps click's `CliRunner` but when
Rich formatting is enabled, the output includes ANSI escape sequences
that make assertion matching difficult. Add a `rich_output` parameter
to the `invoke` method that strips ANSI codes from the result's output
string, or alternatively captures the Rich console's text-only output
by injecting a non-color `Console` via environment variable.

### N9: Add `confirmation` parameter to dangerous commands

There is no built-in way to require user confirmation before executing
a command (e.g., `Are you sure? [y/N]`). Add a `confirm: bool = False`
parameter to `CommandInfo` in `models.py` and the `@app.command()`
decorator in `main.py` that inserts a click `confirm()` prompt before
calling the user's callback in `TyperCommand.invoke` in `core.py`.

### N10: Fix `TyperChoice` not supporting case-insensitive matching

The `TyperChoice` class in `_types.py` wraps `click.Choice` but does
not pass through the `case_sensitive` parameter when constructed from
an `Enum` type. When a user enters a value with different casing (e.g.,
`"Red"` vs `"red"`), the validation fails even when case-insensitive
matching would be appropriate. Fix the `TyperChoice` construction path
in `main.py` to propagate `case_sensitive=False` when the `Enum`
members all have lowercase values.

## Medium

### M1: Implement parameter groups for organizing help output

Add support for grouping related options under named sections in the
Rich help output. Introduce a `group` parameter to `Option()` in
`params.py` that assigns an option to a named group. Modify
`OptionInfo` in `models.py` to carry the group name, update the
click parameter construction in `main.py` to attach group metadata,
and update `rich_utils.py` to render options in grouped panels
instead of a single flat list.

### M2: Add support for `pydantic.BaseModel` as command parameter type

When a Typer callback has a parameter annotated with a Pydantic
`BaseModel`, Typer should auto-generate options for each model field.
Detect `BaseModel` annotations in the type-to-click conversion logic
in `main.py`, flatten model fields into individual `TyperOption`
parameters with appropriate types, and reconstruct the model instance
from the parsed values before calling the callback. Handle nested
models with dot-separated option names. Changes touch `main.py`
(model detection and flattening), `models.py` (model metadata), and
`core.py` (value reconstruction).

### M3: Implement command chaining with result passing

Add a `chain=True` parameter to `Typer` that enables command chaining
where the return value of one command is passed as input to the next.
Requires modifications to `TyperGroup` in `core.py` to enable click's
chain mode, `main.py` to handle return value propagation via the
`Context` object, and `testing.py` to support testing chained
invocations with assertions on intermediate results.

### M4: Add automatic `--dry-run` flag injection for side-effectful commands

Implement a `@app.command(dry_run=True)` option that automatically adds
a `--dry-run` flag to the command. When `--dry-run` is set, inject a
`DryRunContext` into the callback's `Context` that commands can check
before performing mutations. Add Rich formatting in `rich_utils.py`
to prefix output with "[DRY RUN]". Changes touch `models.py` (new
flag), `main.py` (parameter injection), `core.py` (context
modification), and `rich_utils.py` (output decoration).

### M5: Implement argument completion from file content

Add a completion provider that reads completion candidates from a file
or callable. Introduce a `completion_source` parameter to `Option()`
and `Argument()` in `params.py` that accepts a file path (read lines
as candidates) or a callable that returns candidates. Wire the source
through `OptionInfo`/`ArgumentInfo` in `models.py`, convert it to a
click `shell_complete` callback during parameter construction in
`main.py`, and register it in the completion system in
`_completion_shared.py`.

### M6: Add sub-app documentation generation with structured output

Implement `typer.docs.generate(app)` that produces structured
documentation (Markdown or JSON) from a `Typer` application. Traverse
the command tree including all sub-apps, extract parameter definitions
with types and defaults, help text, examples, and generate formatted
output. Changes touch `main.py` (command tree traversal API), `core.py`
(metadata extraction from `TyperCommand`/`TyperGroup`), `models.py`
(documentation metadata), and a new `docs.py` module.

### M7: Implement custom error handler with Rich tracebacks

Add a customizable error handling system that replaces click's default
error output with Rich-formatted errors. Support error categorization
(user error vs developer error via `DeveloperExceptionConfig` in
`models.py`), custom exit codes per exception type, and error handler
registration per command or group. Changes touch `core.py` (error
interception in `TyperCommand.invoke` and `TyperGroup.invoke`),
`rich_utils.py` (Rich error rendering), `main.py` (handler
registration API), and `models.py` (error configuration).

### M8: Add command aliases with help text and completion support

Implement command aliases: `@app.command(aliases=["rm", "del"])`.
Aliases should appear in Rich help panels (e.g., `delete [rm, del]`),
work in shell completion, and be resolvable during command invocation.
Changes touch `models.py` (`CommandInfo` alias field), `main.py`
(alias registration during command creation), `core.py` (`TyperGroup`
command resolution), `rich_utils.py` (alias display), and
`_completion_shared.py` (alias enumeration).

### M9: Implement progress bar integration for long-running commands

Add a `@app.command(progress=True)` option that provides a Rich
progress bar context to the command callback. The progress bar should
integrate with Typer's Rich console and support task tracking, ETA
display, and nested progress for sub-tasks. Changes touch `models.py`
(progress configuration), `main.py` (progress context injection),
`core.py` (progress lifecycle in command invocation), and
`rich_utils.py` (progress bar rendering with Typer's console).

### M10: Add configuration file support for default option values

Implement a `config_file` parameter for `Typer` that loads default
values from a TOML or JSON configuration file. Config values should
have lower priority than command-line arguments but higher than code
defaults. Support per-command sections in the config file. Changes
touch `main.py` (config loading and default resolution), `core.py`
(config integration in `TyperCommand.invoke`), `models.py` (config
metadata), and `utils.py` (config file parsing).

## Wide

### W1: Implement a plugin system for dynamically discovered commands

Add a plugin architecture that discovers and loads Typer sub-commands
from Python entry points, namespace packages, or a plugin directory.
Support plugin metadata (version, author, description), dependency
ordering between plugins, conflict detection when multiple plugins
provide the same command name, and a `typer plugins list` built-in
command. Changes span `main.py` (plugin loading and command
registration), `core.py` (plugin-aware `TyperGroup`), `models.py`
(plugin metadata models), `cli.py` (plugin management commands),
`rich_utils.py` (plugin info display), and `_completion_shared.py`
(completion for plugin commands).

### W2: Add interactive wizard mode for complex commands

Implement `@app.command(wizard=True)` that presents an interactive
step-by-step interface when required parameters are missing. Use Rich
to display type-specific prompts: text input for strings, selection
menus for `Enum` and `Choice` types, file browser for `Path` types,
confirmation for bools, and number spinners for integers. Support
back-navigation, per-step validation, and a Rich-formatted summary
panel before execution. Changes span `core.py` (wizard invocation
mode), `main.py` (parameter-to-wizard-step mapping), `rich_utils.py`
(wizard UI rendering), `models.py` (wizard configuration), `params.py`
(wizard step metadata), `testing.py` (wizard simulation in tests), and
`_types.py` (type-to-widget mapping).

### W3: Implement async command support throughout the framework

Add first-class support for `async def` command callbacks. The `Typer`
class should detect async functions in the callback introspection in
`main.py` and run them in an event loop. `TyperCommand.invoke` in
`core.py` should support awaiting async callbacks. Environment variables
and parameter resolution should work identically for sync and async
commands. Rich progress bars should work within async contexts. The
`CliRunner` in `testing.py` should handle async commands transparently.
Changes span `main.py` (async detection and loop management), `core.py`
(async invocation), `testing.py` (async test support), `rich_utils.py`
(async-safe Rich output), `utils.py` (async function introspection),
and `models.py` (async callback types).

### W4: Implement a comprehensive testing framework for Typer apps

Extend `CliRunner` in `testing.py` into a full testing framework:
snapshot testing for Rich-formatted help output, structured result
objects with parsed stdout/stderr and exit codes, fixture support for
file system and environment variables, mock input streams for
interactive prompts, assertion helpers for command output patterns, and
a pytest plugin with Typer-specific fixtures. Changes span `testing.py`
(enhanced runner and assertions), `core.py` (test mode hooks),
`rich_utils.py` (deterministic output for snapshots), `main.py`
(test configuration), and a new `pytest_plugin.py` module for pytest
integration.

### W5: Add automatic man page and shell completion script generation

Implement documentation generators that produce roff-formatted man
pages and complete shell completion scripts (bash, zsh, fish,
PowerShell) from a Typer app's command tree. Man pages should include
full command hierarchies with cross-references, parameter tables with
types and defaults, environment variable documentation, and examples.
Shell scripts should support dynamic completion via the completion
system. Changes span `main.py` (command tree metadata extraction),
`core.py` (parameter metadata access), `_completion_shared.py`
(enhanced script templates), `_completion_classes.py` (per-shell
generators), `rich_utils.py` (man page formatting), `models.py`
(documentation metadata), and a new `docs.py` module.

### W6: Implement middleware/hook system for command lifecycle

Add pre/post hooks for the command lifecycle: `before_parse`,
`after_parse`, `before_invoke`, `after_invoke`, `on_error`, and
`on_close`. Hooks should support ordering via priority, async hooks,
and access to the `Context`. Support both decorator-based
(`@app.before_invoke`) and programmatic registration. Changes span
`core.py` (hook invocation in `TyperCommand` and `TyperGroup`),
`main.py` (hook registration API on `Typer`), `models.py` (hook
metadata and ordering), `testing.py` (hook testing support),
`rich_utils.py` (hook timing display in verbose mode), and `utils.py`
(hook dependency resolution).

### W7: Implement cross-platform interactive TUI for command selection

Add a `typer.tui(app)` mode that presents a Rich-powered terminal UI
for navigating and executing commands. Display the command tree as a
navigable menu, show parameter forms with type-specific input widgets,
provide real-time validation feedback, display command output in a
scrollable panel, and support command history. Changes span
`rich_utils.py` (TUI layout and widgets), `main.py` (TUI entry
point), `core.py` (interactive command execution), `models.py`
(TUI configuration), `params.py` (widget type mapping), `testing.py`
(TUI simulation), and `_types.py` (widget type registry).

### W8: Add comprehensive type system extensions

Extend Typer's type system to support complex Python types: `dict`
parameters (parsed as `key=value` pairs), `set` parameters
(deduplicated lists), `tuple` parameters (fixed-length typed
sequences), `Union` types (try each type in order), `Optional[X]`
with sentinel-based None detection, and `Literal` types (auto-generated
`Choice`). Each type needs conversion logic in `main.py`, click type
generation in `core.py`, Rich help rendering in `rich_utils.py`,
completion support in `_completion_shared.py`, typing utilities in
`_typing.py`, and model updates in `models.py`.

### W9: Implement a CLI application scaffolding generator

Add `typer scaffold` to the CLI that generates a complete Typer
application project from a template: project structure with
`pyproject.toml`, command modules, test files, CI configuration,
completion scripts, and man pages. Support customization via prompts
(project name, license, features). Changes span `cli.py` (scaffold
commands), `main.py` (template rendering), `models.py` (project
configuration), `core.py` (project validation), and new template
files for project scaffolding including command modules, test files,
and configuration.

### W10: Add internationalization support for help text and error messages

Implement i18n across Typer's user-facing output. All error messages,
Rich help panel labels, built-in strings, and completion descriptions
should be translatable via gettext or a custom catalog. Support
user-provided translations for parameter help text and command
descriptions. Include locale detection from environment variables.
Changes span `rich_utils.py` (translatable panel labels and strings),
`core.py` (translatable error messages), `main.py` (locale
configuration and translation loading), `models.py` (translation
metadata), `_completion_shared.py` (translatable completion output),
`params.py` (translatable default help text), and a new `i18n.py`
module for translation catalog management.
