# tj/commander.js

| Field | Value |
|-------|-------|
| **URL** | https://github.com/tj/commander.js |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Small |
| **Category** | CLI framework |
| **Set** | Cutoff |
| **Commit** | `8247364da749736570161e95682b07fc2d72497b` |

## Why this repo

- **Minimal and well-structured**: Six source files under `lib/` —
  `command.js` (2777 lines, core Command class with parsing, help,
  subcommands), `option.js` (380 lines, Option/DualOptions),
  `argument.js` (150 lines, Argument), `help.js` (747 lines,
  HelpFormatter with column layout and color support), `error.js`
  (39 lines, CommanderError), and `suggestSimilar.js` (101 lines,
  Levenshtein-based suggestions). Clean EventEmitter-based architecture.
- **Widely used**: 27K+ stars, one of the most popular Node.js CLI
  libraries. Powers thousands of CLI tools.
- **Permissive**: MIT license.
- **Scale anchor**: Intentionally small to anchor the "Small" end of
  scale assessment.

## Structure overview

```
lib/
├── command.js          # Command class — subcommands, option/arg registration,
│                       #   parsing, action handlers, help invocation, executable
│                       #   subcommand dispatch, lifecycle hooks, option value
│                       #   sources tracking, state save/restore
├── option.js           # Option class — flag parsing, variadic/required/optional
│                       #   values, negation, choices, env var reading, conflicts,
│                       #   presetArg; DualOptions for boolean+value split
├── argument.js         # Argument class — positional args, required/optional,
│                       #   variadic, choices, default values, argParser
├── help.js             # Help class — column formatting, term/description layout,
│                       #   subcommand listing, usage generation, color support,
│                       #   stripColor utility, wrap/pad helpers
├── error.js            # CommanderError, InvalidArgumentError
├── suggestSimilar.js   # Levenshtein distance for "did you mean?" suggestions
typings/
├── index.d.ts          # Full TypeScript type definitions
├── index.test-d.ts     # Type-level tests
└── esm.d.mts           # ESM type entry
index.js                # CJS entry point
esm.mjs                 # ESM entry point
```

## Scale indicators

- 6 JavaScript source files
- ~4.2K lines of code
- Flat structure (single `lib/` directory)
- Zero runtime dependencies

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Add option grouping in help output

Options in help text are listed in a flat list regardless of their
purpose. Add `option.helpGroup(name)` that assigns an option to a
named group, so `--help` output renders options under labeled sections
(e.g., "Output Options:", "Connection Options:"). Implement in
`option.js` for storage and `help.js` `formatHelp()` for grouped
rendering, falling back to the default list for ungrouped options.

### N2: Fix DualOptions.valueFromOption() collision when positive value matches negative preset

The `DualOptions` class in `option.js` tracks paired positive/negative
option forms (e.g., `--format` / `--no-format`) and its `valueFromOption()`
method determines whether the current stored value came from the positive
option or the negative one. The logic computes `option.negate === (negativeValue === value)`,
where `negativeValue` is the negative option's `presetArg` (defaulting to
`false`). When a custom `presetArg` is set on the negative option (e.g.,
`"none"`) and the positive option is explicitly given the same value
(e.g., `--format=none`), `valueFromOption()` incorrectly returns `false`
for the positive option, treating it as though the negative option was
used instead. This causes `_parseOptionsImplied()` in `command.js` to
skip applying the positive option's implied values. Fix `valueFromOption()`
in `option.js` to correctly handle this collision, for example by tracking
the value source separately rather than inferring it from the stored value.

### N3: Add option value history tracking

The `_optionValueSources` field in `command.js` tracks the source of
each option's value (default, env, cli, config) but only retains the
last source. When an option is specified multiple times (e.g., `-v -v`),
intermediate values are lost. Add `_optionValueHistory` that stores
an ordered list of `{source, value}` entries for each option, and
expose it via `getOptionValueHistory(name)`.

### N4: Fix suggestSimilar not handling camelCase option names

The `suggestSimilar()` function in `suggestSimilar.js` computes edit
distance on raw strings, which penalizes camelCase vs kebab-case
differences (e.g., `--outputDir` vs `--output-dir` has high edit
distance despite being semantically similar). Normalize option names
to lowercase-hyphenated form before computing distance, and match
against both the original and normalized forms.

### N5: Add required option validation with custom error messages

When a required option (`option.makeOptionMandatory()`) is missing,
the error message is generic. Add `option.mandatoryMessage(msg)` that
allows per-option custom error messages (e.g., "The --token option is
required for authentication. Set it or use TOKEN env var."). Implement
in `option.js` and use in `command.js` `_parseCommand()`. Add the
`mandatoryMessage(msg: string)` method signature to
`typings/index.d.ts` and add a type-level test case in
`typings/index.test-d.ts`.

### N6: Fix Help.boxWrap() losing ANSI reset codes at line break boundaries

The `boxWrap()` method in `help.js` correctly uses `displayWidth()` (which
calls `stripColor()`) to calculate the visible width of each whitespace-
delimited chunk. However, when a line break is introduced by the wrapping
logic, the method trims leading whitespace from the first chunk of the new
line using `chunk.trimStart()`. If the chunk begins with a whitespace
character followed immediately by an ANSI reset or color code (e.g., a
trailing reset that got placed after the last visible character in a word),
`trimStart()` can shift that reset sequence to appear at the start of the
following wrapped line rather than at the end of the preceding one. This
causes the terminal color state to leak across the wrap boundary, resulting
in incorrectly colored subsequent lines. Fix `boxWrap()` to ensure that
ANSI SGR sequences adjacent to whitespace are associated with the correct
output line when wrapping occurs, so that no color state leaks across
wrapped line boundaries.

### N7: Add argument value validation callbacks

The `Argument` class in `argument.js` supports `argParser()` for
custom parsing but no standalone validation that runs after parsing and
before the action handler. Add `argument.validate(fn)` that accepts a
function receiving the parsed value and throwing
`InvalidArgumentError` on failure. Invoke it in `command.js` during
`_parseCommand()` after argument processing.

### N8: Fix _parseOptionsEnv() not trimming whitespace from environment values

The `_parseOptionsEnv()` method in `command.js` reads environment variable
values verbatim via `process.env[option.envVar]` before emitting the
`optionEnv:` event. Any trailing newline or surrounding whitespace present
in the environment variable (e.g., from `export PORT="8080 "` or shell
command substitution) is passed unchanged into the option's `parseArg`
function and stored as the option value. Trim whitespace from the raw
environment variable string in `_parseOptionsEnv()` before it is
passed to the `optionEnv:` event emission, so that values like `"8080 "`
are normalized to `"8080"` regardless of how the variable was set.

### N9: Add hidden arguments support

Options can be marked as hidden with `option.hideHelp()`, but the
`Argument` class in `argument.js` has no equivalent. Add
`argument.hideHelp()` that excludes the argument from the usage
line and help output while still accepting values during parsing.
Implement in `argument.js` and update `help.js` `commandUsage()` to
skip hidden arguments.

### N10: Fix Command.parse() not restoring state after error in nested subcommands

The `saveStateBeforeParse()` and `restoreStateBeforeParse()` methods
in `command.js` save and restore option values for re-parsing. But when
an error occurs in a nested subcommand, the parent command's state is
not restored, leaving stale parsed values from previous invocations.
Call `restoreStateBeforeParse()` in the error handling path of
`_parseCommand()` for all ancestor commands.

## Medium

### M1: Implement option dependency declarations with dependsOn

The `Option` class in `option.js` already supports `option.implies()`
for automatically setting other option values, and `option.conflictsWith()`
for declaring mutual exclusions. What is missing is `option.dependsOn(name)`
for declaring that one option requires another option to be explicitly
provided on the command line. When `--output` has `.dependsOn('format')`,
omitting `--format` while providing `--output` should produce a clear error.
Add `option.dependsOn(...names)` in `option.js` for storage, add
post-parse validation in `command.js` (a new `_checkForMissingDependencies()`
method analogous to `_checkForConflictingOptions()`), add error messages
with appropriate `commander.missingDependency` error code, and include
help text integration in `help.js`. Add the `dependsOn()` method signature
to `typings/index.d.ts`. Document the `dependsOn()` API in `Readme.md`
under the existing option-related section alongside `conflictsWith()`, and
add a `CHANGELOG.md` entry describing the feature.

### M2: Add config file support as a parameter source

Implement `command.configFile(path, format)` that reads option values
from JSON, YAML, or TOML configuration files. Config values should
have lower precedence than CLI arguments but higher than environment
variables and defaults. Track the config source in
`_optionValueSources`. Requires a config reader, integration with
option resolution in `command.js`, and conflict handling when multiple
config files are specified.

### M3: Implement interactive prompt mode for missing required options

Add `command.interactive()` that prompts the user for any required
options not provided on the command line. Use `readline` for text
input, present choices for options with `choices()`, and confirm
boolean flags. Support a `--no-interactive` flag to disable prompting
in CI environments. Requires changes to `command.js` for the prompt
loop, integration with option types, and output formatting.

### M4: Add structured output support for help text

Implement `command.helpAsJson()` and expose via `--help-json` (or
`helpFormat('json')`) that emits the help information as a structured
JSON object containing command name, description, options (with types,
defaults, choices, env vars), arguments, and subcommands. Requires
a new serialization path in `help.js` parallel to `formatHelp()` and
registration of the format flag as a special option.

### M5: Implement command versioning and deprecation lifecycle

Add `command.since(version)` and `command.deprecatedSince(version,
replacement)` metadata that displays in help text and emits warnings
when deprecated commands are invoked. Include a `command.removed(
version, message)` that throws a clear error. Requires metadata
storage in `command.js`, help output in `help.js`, and lifecycle
checks during `_parseCommand()`.

### M6: Add option value coercion with type system

Implement a type system for options: `option.type('number')`
automatically parses to `Number`, validates NaN, and displays
`<number>` in help. Built-in types: `number`, `integer`, `boolean`,
`path`, `url`, `date`. Custom types via `commander.type('port',
{parse, validate, display})`. Requires a type registry, integration
with `option.js` parsing, `argument.js`, `help.js` for display, and
`typings/index.d.ts` for TypeScript support.

### M7: Extend multi-alias support across help, suggestions, and completions

The `command.alias()` / `command.aliases()` mechanism in `command.js`
already registers multiple aliases, but only the first alias is
leveraged: `subcommandTerm()` in `help.js` appends only `cmd._aliases[0]`
to the subcommand name in help output, and the unknown-command suggestion
path in `command.js` calls `command.alias()` (first alias only) when
building `candidateNames` for `suggestSimilar`. Add
`command.aliasesForHelp(names)` to control which aliases appear in the
help listing versus which are parse-only, update `subcommandTerm()` in
`help.js` to render all help-visible aliases (e.g., `name|alias1|alias2`),
update the `suggestSimilar` candidate-building code in `command.js` to
include all aliases of every subcommand so that any registered alias
triggers a "Did you mean?" hint, and add a `visibleAliases(cmd)` helper
in `help.js` analogous to `visibleOptions()`. Add the `aliasesForHelp()`
method signature to `typings/index.d.ts` and document the full multi-alias
behaviour in `Readme.md`.

### M8: Extend hook system with error hooks and globally-inherited hooks

The `command.hook(event, fn)` system in `command.js` already supports
`preSubcommand`, `preAction`, and `postAction` events via
`_lifeCycleHooks`, with async support through the promise chain in
`_chainOrCallHooks()`. What is missing is: (1) an `onError` lifecycle
event that fires before `process.exit()` is called from `command.error()`,
allowing hooks to log or transform errors; (2) a way to register a hook
on a parent command that automatically applies to all descendant
subcommands without requiring each subcommand to register its own hook;
and (3) a `postSubcommand` event symmetric to `preSubcommand`. Extend
`_lifeCycleHooks` to accept the new events, add `onError` hook invocation
inside `command.error()`, implement inherited-hook propagation in
`_chainOrCallHooks()` by walking ancestor commands for hooks with an
`inherit` flag, and update `typings/index.d.ts` with the new event
literals. Requires changes to `command.js` error handling and invocation
pipeline.

### M9: Implement output format negotiation for commands

Add `command.outputFormat(format)` that configures output rendering.
When `--output json` is passed, the action handler receives a
`context.format` property and can return structured data that the
framework serializes. Built-in formatters: `json`, `table`, `csv`,
`yaml`. Requires a formatter registry, output pipeline in
`command.js`, and integration with `configureOutput()`.

### M10: Add progress reporting utilities for long-running commands

Implement `command.createProgress({total, label})` that returns a
progress bar object supporting `.tick()`, `.update(n)`, and
`.finish()`. Support spinners for indeterminate progress. Integrate
with the output configuration system (`configureOutput()`) and support
`--quiet` mode suppression. Requires a new progress module, terminal
cursor manipulation, and integration with the command lifecycle.

## Wide

### W1: Implement full shell completion generation

Add `command.generateCompletion('bash')` that produces bash, zsh, and
fish completion scripts from the command tree. Support dynamic
completions for options with runtime values, file path completions for
path-typed arguments, subcommand completions, and option value
completions from `choices()`. Include a `completions` subcommand
that outputs the script. Changes span `command.js` (tree traversal),
`help.js` (completion formatting), a new `completion.js` module, and
`typings/index.d.ts`.

### W2: Add comprehensive TypeScript support with generic type inference

Rewrite the type definitions so that `program.opts()` returns a typed
object inferred from registered options. Use template literal types
to parse option flag strings (`"-p, --port <number>"`) and infer types
from `parseArg` return types. Support nested subcommand typing and
action handler parameter inference. Changes span `typings/index.d.ts`,
`typings/index.test-d.ts`, potentially a TypeScript implementation of
core modules, and the ESM/CJS entry points.

### W3: Implement a plugin and extension system

Add `command.use(plugin)` that accepts plugin objects with `install(
command)` methods. Plugins can register options, subcommands, hooks,
and custom types. Support plugin ordering, conflict detection for
overlapping registrations, and a plugin API for accessing internal
state. Include a `plugin list` subcommand. Changes span `command.js`
(plugin lifecycle), a new `plugin.js` module, `help.js` (plugin
attribution), error handling, and type definitions.

### W4: Implement command testing framework

Add a `TestRunner` class (analogous to Click's `CliRunner`) that
invokes commands programmatically with simulated argv, captures
stdout/stderr/exit code, provides mock stdin for interactive prompts,
and supports snapshot testing for help text. Include assertion helpers
and integration with common test frameworks (Jest, Mocha). Changes
span a new `testing.js` module, the command invocation pipeline, output
capture, stdin simulation, and type definitions.

### W5: Add automatic documentation generation

Implement `command.generateDocs({format: 'markdown'})` that produces
Markdown, man pages (roff), or HTML documentation from the command
tree. Support full subcommand hierarchies with cross-references,
option tables, examples, environment variable documentation, and exit
code documentation. Changes span a new `docs.js` module, `help.js`
(structured extraction), `command.js` metadata access, template
rendering, and type definitions.

### W6: Implement internationalization for help and error messages

Add i18n support: all built-in strings ("Usage:", "Options:",
"Commands:", error messages) should be translatable via locale files
or a custom message function. Support user-provided translations for
option descriptions and command summaries. Include locale detection
from `LANG`/`LC_ALL` environment variables. Changes span `help.js`,
`error.js`, `command.js`, a new `i18n.js` module with message
catalogs, and type definitions.

### W7: Implement command workflow orchestration

Add `command.workflow(steps)` that defines multi-step command
execution with dependency ordering, parallel execution of independent
steps, progress reporting, rollback on failure, and dry-run mode.
Each step is a command or function with declared inputs/outputs.
Changes span `command.js` (workflow registration), a new `workflow.js`
module, progress reporting, error handling/rollback, the hook system,
and type definitions.

### W8: Add remote command execution support

Implement `command.remote({host, transport: 'ssh'})` that proxies
command execution to remote machines. Support argument serialization
over SSH or HTTP, stdout/stderr streaming, exit code propagation, and
secure credential handling. Include a lightweight agent script for
the remote side. Changes span a new `remote.js` module, `command.js`
dispatch, serialization, transport layers (SSH/HTTP), error handling,
and type definitions.

### W9: Implement visual CLI builder and introspection UI

Add `command.inspect()` that launches a terminal UI showing the
command tree, registered options with their current values/sources,
argument definitions, and hook registrations. Support interactive
exploration via keyboard navigation, option value editing, and
test invocation. Changes span a new `inspector.js` module, terminal
UI rendering (box drawing, colors), `command.js` introspection API,
key handling, and type definitions.

### W10: Add API-first mode with HTTP server generation

Implement `command.serve({port: 3000})` that exposes the CLI as an
HTTP API. Each command maps to a route, options map to query
parameters or JSON body fields, and responses include stdout, stderr,
and exit code. Include OpenAPI spec generation, authentication
middleware, rate limiting, and CORS support. Changes span a new
`server.js` module, route generation from the command tree, request
parsing, response serialization, `command.js` invocation, and type
definitions.

### N11: Fix jest.config.js missing collectCoverageFrom configuration

The `jest.config.js` configuration enables `collectCoverage: true` but
does not specify a `collectCoverageFrom` array, so Jest determines coverage
scope automatically based on which files are imported by tests rather than
explicitly tracking all source files in `lib/`. Files that are lightly
tested or only imported transitively may show incomplete coverage data.
Add a `collectCoverageFrom` array to `jest.config.js` that explicitly
includes all library source files: `lib/command.js`, `lib/option.js`,
`lib/argument.js`, `lib/help.js`, `lib/error.js`, and
`lib/suggestSimilar.js`. Also update `.github/workflows/tests.yml` to
add a coverage upload step so that coverage results are published as part
of CI.

### M11: Add comprehensive example documentation and project configuration

Create a `docs/examples/` directory with documented usage examples for
all major features: subcommands, option choices, custom argument
parsing, error handling, and help customization. Each example should be
a standalone `.js` file that can be run directly. Update `Readme.md` to
link to each example with a brief description. Add a `docs:generate`
script to `package.json` that generates API documentation from
`typings/index.d.ts`. Update `CONTRIBUTING.md` to include a section on
how to add and test new examples. Update `tsconfig.json` to include the
`examples/` directory for type checking. Add missing content to
`SECURITY.md` with a vulnerability disclosure timeline.

### W11: Overhaul CI/CD pipeline, documentation, and project configuration

Expand `.github/workflows/tests.yml` to add separate jobs for type
checking (running both `tsconfig.js.json` and `tsconfig.ts.json`), a
linting job using `eslint.config.js`, and a documentation build
verification job. Add a `.github/workflows/release.yml` for automated
npm publishing with provenance. Create a `docs/migration/` directory
with `v11-to-v12.md` and `v12-to-v13.md` migration guides covering
API changes, renamed methods, and updated option parsing behavior.
Update `package.json` to add a `packageManager` field and `funding`
configuration (the `engines` field already exists). Update
`.github/dependabot.yml` (which already exists) to change the npm
package-ecosystem schedule from `monthly` to `weekly` and add a
scope for `lib/` path monitoring. Update `.prettierrc.js` to enforce
consistent formatting across `lib/`, `typings/`, `examples/`, and
`docs/`. Update `.editorconfig` to add settings for `.mjs` and `.d.ts`
files.
