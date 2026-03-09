# fastapi/typer

| Field | Value |
|-------|-------|
| **URL** | https://github.com/fastapi/typer |
| **License** | MIT |
| **Language** | Python |
| **Scale** | Medium |
| **Category** | CLI framework |
| **Set** | Cutoff |
| **Commit** | `9286ffeb0de672e9de8aa916caf10035cd16728f` |

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
function signatures to extract parameter metadata but does not detect
`*args` (VAR_POSITIONAL) parameters as unsupported. When a callback
like `def cmd(files: list[str], *extra: str)` is registered,
`get_params_from_function` processes `extra` as a regular parameter,
but invocation fails at runtime with a `TypeError` because click
passes all resolved values as keyword arguments while the function
expects `extra` as variadic positional. Fix `get_params_from_function`
in `utils.py` to detect `inspect.Parameter.VAR_POSITIONAL` parameters
and raise a clear `ValueError` explaining that `*args` is not
supported, directing users to use `list` or `tuple` annotations
instead.

### N3: Add `Decimal` type support to Typer's type system

When a Typer callback declares a parameter with `decimal.Decimal`
annotation (e.g., `amount: Decimal = Option(...)`), Typer raises
`RuntimeError: Type not yet supported: <class 'decimal.Decimal'>` when
building the click command. Add `Decimal` support to `get_click_type`
in `main.py` by mapping the `Decimal` annotation to `click.STRING`
for input capture, and add a `Decimal` entry to
`determine_type_convertor` in `main.py` so that the parsed string is
converted to a `Decimal` instance before the callback is invoked.
Update `typer/__init__.py` to re-export `Decimal` for convenience.

### N4: Fix Rich help panel not respecting `no_args_is_help` for TyperGroup

When a `TyperGroup` is invoked with no arguments and `no_args_is_help`
is `True`, click raises `NoArgsIsHelpError`. In Typer's `_main`
function in `core.py`, this exception is caught as a
`click.ClickException` and dispatched to `rich_format_error` in
`rich_utils.py`. However, `rich_format_error` returns early (does
nothing) for `NoArgsIsHelpError` without ever calling `e.show()`,
which means the Rich-formatted help is never displayed. Fix
`rich_format_error` in `rich_utils.py` to call `e.show()` when the
exception is `NoArgsIsHelpError`, so that `TyperGroup.format_help`
(which delegates to `rich_format_help`) is invoked and the Rich help
panel is rendered correctly.

### N5: Add `deprecated` parameter to `Option()` and `Argument()` builders

Click 8.x supports a `deprecated: bool | str = False` parameter on
`click.Option` and `click.Argument` that emits a deprecation warning
when the parameter is used. Typer exposes `deprecated` only at the
command level (`CommandInfo` in `models.py`), but not at the
individual parameter level. Add a `deprecated: bool | str = False`
field to `ParameterInfo` in `models.py` so that `OptionInfo` and
`ArgumentInfo` inherit it. Expose the parameter in the `Option()` and
`Argument()` builders in `params.py`. Wire it through
`get_click_param` in `main.py` so it is passed to the `TyperOption`
and `TyperArgument` constructors in `core.py`, which then forward it
to click's underlying option and argument classes.

### N6: Fix `BashComplete.format_completion` to include completion item type

Typer's `BashComplete` class in `_completion_classes.py` overrides
`format_completion` to return only `item.value`, stripping the item
type. Click's own `BashComplete.format_completion` returns
`f"{item.type},{item.value}"` so that the shell completion script can
distinguish plain-text completions from file and directory completions.
Because Typer strips the type, Bash shell completion for `Path`-typed
parameters and custom completion providers that return typed
`CompletionItem` objects (e.g., `type="file"`) never trigger
filesystem completion. Fix `BashComplete.format_completion` in
`_completion_classes.py` to include the item type prefix, and update
the `COMPLETION_SCRIPT_BASH` template in `_completion_shared.py` to
correctly parse and dispatch on the type field.

### N7: Fix metavar display for `count=True` options in Rich help

When an option uses `count=True` (e.g., `verbose: int = Option(0, '--verbose', '-v', count=True)`),
Typer's Rich help formatter in `_print_options_panel` in `rich_utils.py`
displays `INTEGER` in the metavar column even though a count option
does not accept a value on the command line. Boolean flag options are
already skipped (`if metavar_str != "BOOLEAN"`), but count options
follow a different path. Fix `_print_options_panel` in `rich_utils.py`
to detect when a `click.Option` has `count=True` and suppress the
metavar display for those options, consistent with how flag options
are handled.

### N8: Fix `CliRunner` not providing clean text output for Rich-formatted help

The `CliRunner` in `testing.py` wraps click's `CliRunner` but when
Rich formatting is enabled, the help output contains Rich panel
decorations (box-drawing characters, extensive whitespace padding,
and panel borders) that make string-based assertions fragile. Add a
`strip_ansi: bool = False` parameter to the `invoke` method in
`testing.py` that, when `True`, post-processes the result's output
string to remove ANSI escape sequences and optionally strips Rich
panel decorations by setting `TERM=dumb` and `NO_COLOR=1` in the
environment before invoking the command, forcing Rich to produce
plain text output.

### N9: Add `confirmation` parameter to dangerous commands

There is no built-in way to require user confirmation before executing
a command (e.g., `Are you sure? [y/N]`). Add a `confirm: bool = False`
parameter to `CommandInfo` in `models.py` and the `@app.command()`
decorator in `main.py` that inserts a click `confirm()` prompt before
calling the user's callback in `TyperCommand.invoke` in `core.py`.

### N10: Add `case_insensitive_enums` flag to `Typer` for global Enum matching

When building CLI applications with multiple `Enum`-typed parameters,
users must individually set `case_sensitive=False` on each `Option()`
or `Argument()` call. There is no way to configure case-insensitive
Enum matching globally for an entire `Typer` application. Add a
`case_insensitive_enums: bool = False` parameter to `Typer.__init__`
in `main.py` and carry it through `TyperInfo` in `models.py`. In
`get_click_type` in `main.py`, when `case_insensitive_enums` is
`True` on the solved `TyperInfo`, create all `TyperChoice` instances
for `Enum`-annotated parameters with `case_sensitive=False` regardless
of the per-parameter setting, unless the user has explicitly set
`case_sensitive=True` on that parameter.

## Medium

### M1: Implement option dependency validation (`requires` / `conflicts_with`)

There is no built-in way in Typer to declare that one option requires
another to be set, or that two options are mutually exclusive. Users
must write manual validation in the command body. Add `requires` and
`conflicts_with` parameters to `Option()` in `params.py` that each
accept a list of option names. Store these on `OptionInfo` in
`models.py`. During click parameter construction in `main.py`, attach
the dependency metadata as custom attributes on the `TyperOption`
instance. In `TyperCommand.invoke` in `core.py`, after click resolves
all parameter values, iterate the `TyperOption` instances and validate
that required companions are present and conflicting options are
absent, raising a `click.UsageError` on violation. Update
`rich_utils.py` to display `requires:` and `conflicts with:` hints
in the option help panel.

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

Typer already supports `chain=True` and `result_callback` (inherited
from click) for running multiple commands in one invocation. However,
there is no Typer-native mechanism to automatically pass the return
value of one chained command as an injected context variable to the
next command in the chain. Implement a `pipe=True` parameter for
`Typer` that, when combined with `chain=True`, captures each command's
return value via a custom `TyperGroup.invoke` override in `core.py`
and stores it in the click `Context` object so that subsequent
commands can declare a `ctx: typer.Context` parameter and access the
previous command's result via `ctx.obj`. Changes touch `core.py`
(TyperGroup.invoke override), `main.py` (pipe flag and result
injection), `models.py` (TyperInfo pipe field), and `testing.py`
(helper assertions for piped results).

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
`_completion_shared.py`. Also update `CONTRIBUTING.md` to document how to test completion sources and add a completion development guide.

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

Extend Typer's type system to support additional Python types that
currently raise `RuntimeError: Type not yet supported`. Specifically:
`dict` parameters (parsed as `key=value` pairs from repeated
`--opt key=value` flags), `set` parameters (deduplicated lists using
the existing multiple-option mechanism), and `Union` types (try each
type converter in order, accepting the first that succeeds). Each
type needs conversion logic in `main.py` (`get_click_type`,
`get_click_param`, `determine_type_convertor`), click type generation
in `core.py` (TyperOption/TyperArgument metavar overrides), Rich help
rendering in `rich_utils.py` (metavar display for dict and set),
completion support in `_completion_shared.py`, typing utilities in
`_typing.py` (is_dict_type, is_set_type helpers), and model updates
in `models.py` (convertor metadata).

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

### N11: Update `CITATION.cff` metadata with missing required fields

The `CITATION.cff` file is missing several fields required by the
Citation File Format 1.2 specification. Specifically, `identifiers`
has no entries (the `doi` field is absent), `date-released` is missing
entirely, and `version` is not present. Update `CITATION.cff` to add
a `doi` identifier under `identifiers`, add the `date-released` field
with the current release date, and add the `version` field matching
the version in `typer/__init__.py`. Verify that the `cff-version`,
`title`, `authors`, `repository-code`, and `license` fields (which
are already correctly set) are preserved unchanged.

### M11: Restructure `mkdocs.yml` navigation for completion and Rich sections

The `mkdocs.yml` navigation does not include dedicated sections for
shell completion features or Rich output customization, even though
Typer has significant functionality in both areas (completion scripts,
install/show completion, Rich markup modes, help panel styling). The
`nav` key currently buries completion under `tutorial/options-autocompletion.md`
with no Rich output section at all. Restructure the `nav` key in
`mkdocs.yml` to add a top-level "Shell Completion" section grouping
all completion-related pages, and a "Rich Output" section covering
markup modes and help customization. Update `.pre-commit-config.yaml`
to add a `markdown-link-check` hook that validates internal links in
the restructured documentation. Verify the `mkdocs.env.yml` markdown
extension settings are still compatible with the updated nav.

### W11: Comprehensive project metadata and documentation configuration overhaul

Perform a full non-code refresh across all project configuration and
documentation files. Update `pyproject.toml` classifiers to add
`"Development Status :: 5 - Production/Stable"` and Python 3.14
classifier, and refine dependency version constraints. Restructure
`mkdocs.yml` with a complete navigation overhaul including API
reference, tutorials, and migration guides. Revise `CONTRIBUTING.md`
with updated development setup instructions using `uv`, testing
guidelines, and documentation contribution workflow. Update
`SECURITY.md` with the current vulnerability reporting policy and
supported versions table. Update `.pre-commit-config.yaml` hook
versions to their latest releases and add an `mdformat` hook for
consistent Markdown formatting. Revise `CITATION.cff` to add the
missing `doi`, `date-released`, and `version` fields.
