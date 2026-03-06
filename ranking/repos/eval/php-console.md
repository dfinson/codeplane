# symfony/console

| Field | Value |
|-------|-------|
| **URL** | https://github.com/symfony/console |
| **License** | MIT |
| **Language** | PHP |
| **Scale** | Medium-large |
| **Category** | CLI framework / console component |
| **Set** | eval |
| **Commit** | `d5795ce9e707206d9364c2cbec275cce6d4103ba` |

## Why this repo

- **Full CLI framework**: Application lifecycle, command registration, input parsing, output formatting, shell completion, signal handling, interactive questions, and testing utilities
- **Well-structured**: Clean separation into Command/, Input/, Output/, Helper/, Formatter/, Completion/, Descriptor/, Event/, Tester/, Style/, Question/, and more
- **Rich feature surface**: ProgressBar, Table, TreeHelper, SymfonyStyle, argument resolvers, invokable commands, section output, color modes, shell completion for bash/zsh/fish

## Structure overview

```
console/
├── Application.php              # Application lifecycle, command routing, signal handling
├── Command/
│   ├── Command.php              # Base command with configure/execute lifecycle
│   ├── InvokableCommand.php     # Closure-based command with argument resolution
│   ├── LazyCommand.php          # Deferred command instantiation
│   ├── TraceableCommand.php     # Command instrumentation
│   ├── CompleteCommand.php      # Shell completion command
│   ├── LockableTrait.php        # Command locking via filesystem
│   └── SignalableCommandInterface.php
├── Input/
│   ├── ArgvInput.php            # CLI argument parser
│   ├── ArrayInput.php           # Programmatic input
│   ├── StringInput.php          # String-parsed input
│   ├── InputDefinition.php      # Argument/option schema
│   ├── InputArgument.php        # Positional argument definition
│   ├── InputOption.php          # Option (flag/value) definition
│   └── Input.php                # Abstract input base
├── Output/
│   ├── ConsoleOutput.php        # stdout/stderr output
│   ├── ConsoleSectionOutput.php # Rewritable output sections
│   ├── StreamOutput.php         # Stream-based output
│   ├── BufferedOutput.php       # In-memory output buffer
│   └── Output.php               # Abstract output base
├── Formatter/
│   ├── OutputFormatter.php      # Tag-based style formatting (<info>, <error>)
│   ├── OutputFormatterStyle.php # Foreground/background/options style
│   └── OutputFormatterStyleStack.php
├── Helper/
│   ├── Table.php                # Table rendering with multiple styles
│   ├── ProgressBar.php          # Progress bar with format placeholders
│   ├── ProgressIndicator.php    # Spinner indicator
│   ├── QuestionHelper.php       # Interactive question prompts
│   ├── TreeHelper.php           # Tree structure rendering
│   ├── ProcessHelper.php        # External process execution
│   ├── FormatterHelper.php      # Text formatting utilities
│   ├── OutputWrapper.php        # Word-wrapping output
│   └── DebugFormatterHelper.php
├── Style/
│   ├── SymfonyStyle.php         # Opinionated output style (title, section, table, etc.)
│   └── OutputStyle.php          # Base output style
├── Completion/                  # Shell completion (bash, zsh, fish)
├── Question/                    # Question, ChoiceQuestion, ConfirmationQuestion, FileQuestion
├── Descriptor/                  # JSON, XML, Markdown, ReStructuredText, Text descriptors
├── Event/                       # ConsoleCommandEvent, ConsoleErrorEvent, ConsoleSignalEvent
├── Tester/                      # CommandTester, ApplicationTester, assertions
├── ArgumentResolver/            # Value resolvers for InvokableCommand
├── Attribute/                   # PHP 8 attributes (AsCommand, Argument, Option, etc.)
├── SignalRegistry/              # OS signal registration and dispatch
├── DependencyInjection/        # Compiler passes for Symfony DI
├── Messenger/                   # Integration with Symfony Messenger
└── Terminal.php                 # Terminal dimensions detection
```

## Scale indicators

- ~160 PHP source files (excluding tests)
- ~22K lines of library code
- ~190 test files
- Multiple output backends (console, stream, buffer, section, null)
- Shell completion for bash, zsh, and fish
- 8 descriptor formats (text, json, xml, markdown, rst)
- Full argument resolver pipeline with 10+ value resolvers

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `OutputFormatterStyleStack` not restoring style after nested empty tags

In `Formatter/OutputFormatterStyleStack.php`, when processing `<info>outer<error></error>continued</info>`, the empty `<error></error>` pair pops the error style and leaves the stack pointing to the base style instead of restoring `info`. The `pop()` method should verify it is popping the matching style rather than always popping the top of the stack.

### N2: Fix `ProgressBar` not recalculating terminal width on window resize

In `Helper/ProgressBar.php`, the terminal width is captured once during `start()` via `Terminal::getWidth()`. If the terminal is resized mid-progress, the bar format overflows or underflows. The width should be re-queried on each `advance()` call or on a configurable interval.

### N3: Fix `ArgvInput` not handling `--option=` (empty value) for required-value options

In `Input/ArgvInput.php`, parsing `--name=` with an `InputOption` of mode `VALUE_REQUIRED` throws `RuntimeException` ("option requires a value") instead of accepting the empty string as a valid value. The parser should distinguish between `--name` (missing value) and `--name=` (explicit empty value).

### N4: Fix `ConsoleSectionOutput` overwrite corruption when content contains ANSI escape sequences

In `Output/ConsoleSectionOutput.php`, `overwrite()` calculates the number of lines to erase based on `substr_count($content, "\n")`. When the content includes ANSI color codes that span multiple lines, the visible line count differs from the newline count, causing partial overwrites that leave ghost text from previous renders.

### N5: Fix `QuestionHelper` not respecting `max_attempts` for `ChoiceQuestion` with multiselect

In `Helper/QuestionHelper.php`, when a `ChoiceQuestion` has `multiselect: true` and `maxAttempts: 3`, each invalid comma-separated entry counts as one attempt per item selected rather than one attempt per prompt. A single multiselect prompt with three items exhausts all attempts immediately.

### N6: Fix `TreeHelper` not handling circular references in node structures

In `Helper/TreeHelper.php`, if a `TreeNode` has a child that references an ancestor node, `renderNode()` recurses infinitely until stack overflow. The helper should detect cycles and render a `[circular reference]` placeholder or throw an explicit error.

### N7: Fix `CompletionInput` binding failure for options with `VALUE_NONE` mode

In `Completion/CompletionInput.php`, `bind()` attempts to set the value of a `VALUE_NONE` option (boolean flag) when the cursor is positioned right after the flag name. This triggers `InvalidArgumentException` from `InputDefinition` because no-value options cannot accept a value. The binding should skip value assignment for `VALUE_NONE` options.

### N8: Fix `Color` class not clamping 24-bit color values

In `Color.php`, the `convertHexColor()` method passes raw RGB values to ANSI escape sequences. Values above 255 produce malformed escape codes that corrupt terminal output. The method should clamp each channel to the 0–255 range.

### N9: Fix `OutputWrapper` splitting words mid-multibyte character

In `Helper/OutputWrapper.php`, the `wrap()` method uses `wordwrap()` which operates on byte length rather than character length. For multibyte UTF-8 strings (e.g., CJK characters), the wrapping can split in the middle of a character, producing invalid UTF-8 output.

### N10: Fix `Cursor` not flushing output after movement methods

In `Cursor.php`, methods like `moveUp()`, `moveToColumn()`, and `clearLine()` write ANSI escape sequences to the output but do not call `flush()`. When the output stream is buffered, cursor movements are delayed until the next explicit write, causing visual glitches in interactive commands.

## Medium

### M1: Add table column alignment and numeric formatting

Extend `Helper/Table.php` to support per-column alignment (`left`, `right`, `center`) and numeric formatting (thousands separator, decimal places). Requires changes to `Table` (column-level alignment logic), `TableStyle` (alignment configuration), `TableCell` (formatted value rendering), and `TableCellStyle` (numeric format options).

### M2: Implement command grouping with collapsible sections in list output

Add `#[AsCommand(group: 'database')]` support so `list` command displays commands in collapsible groups. Changes span `Attribute/AsCommand.php` (group property), `Command/Command.php` (group accessor), `Application.php` (group collection during `all()`), `Descriptor/TextDescriptor.php` (grouped rendering), and `Command/ListCommand.php` (group filter option).

### M3: Add progress bar multi-bar support for parallel operations

Implement `ProgressBar::createMulti($output, $count)` that renders multiple progress bars simultaneously using `ConsoleSectionOutput`. Changes span `Helper/ProgressBar.php` (multi-bar coordinator, section-based rendering), `Output/ConsoleSectionOutput.php` (multi-section updates), `Style/SymfonyStyle.php` (multi-progress factory), and `Helper/ProgressIndicator.php` (multi-indicator support).

### M4: Implement argument resolver for Symfony UID types

Add value resolvers for `Uuid`, `Ulid`, and custom UID types used with `InvokableCommand`. Changes span a new `ArgumentResolver/ValueResolver/UidValueResolver.php`, `Attribute/Argument.php` (UID validation hints), `InvokableCommand.php` (UID type detection), and `ArgumentResolver/ArgumentResolver.php` (resolver registration order).

### M5: Add command execution timeout with configurable alarm signal

Implement `#[AsCommand(timeout: 30)]` that registers a `SIGALRM` handler to abort long-running commands. Changes span `Attribute/AsCommand.php` (timeout property), `Application.php` (alarm registration in `doRunCommand`), `SignalRegistry/SignalRegistry.php` (alarm signal handling), `Event/ConsoleAlarmEvent.php` (event dispatch), and `Command/Command.php` (timeout accessor).

### M6: Implement output format auto-detection for CI environments

Add automatic format switching when running in CI (GitHub Actions, GitLab CI, Jenkins). When detected, use `GithubActionReporter` for error/warning annotations and disable interactive features. Changes span `Application.php` (CI detection), `CI/GithubActionReporter.php` (annotation formatting), `Output/ConsoleOutput.php` (decorator mode selection), `Style/SymfonyStyle.php` (disable interactive prompts), and `Helper/QuestionHelper.php` (CI fallback to defaults).

### M7: Add interactive file browser for `FileQuestion`

Extend `Question/FileQuestion.php` with an interactive directory-browsing mode using `Cursor` for navigation. Changes span `FileQuestion.php` (browse mode, directory listing), `Helper/QuestionHelper.php` (special handling for file browse), `Cursor.php` (keyboard navigation helpers), `Input/StreamableInputInterface.php` (raw key detection), and `Style/SymfonyStyle.php` (file chooser method).

### M8: Implement command dependency declaration and execution ordering

Add `#[AsCommand(requires: ['db:migrate'])]` that ensures prerequisite commands run before the current command. Changes span `Attribute/AsCommand.php` (requires property), `Application.php` (dependency graph resolution, topological sort), `Command/Command.php` (dependency accessor), `Exception/` (circular dependency exception), and `Tester/CommandTester.php` (dependency stubbing).

### M9: Add shell completion for dynamic values from external data sources

Extend the completion system to support async value providers that fetch suggestions from databases or APIs. Changes span `Completion/CompletionSuggestions.php` (deferred suggestion provider), `Completion/Suggestion.php` (metadata for external sources), `Command/CompleteCommand.php` (async suggestion resolution), `Completion/Output/BashCompletionOutput.php` (cached suggestion rendering), and `Completion/CompletionInput.php` (provider context).

### M10: Implement colored diff output helper for text comparison

Add `Helper/DiffHelper.php` that renders side-by-side or unified diffs with ANSI coloring. Changes span a new `Helper/DiffHelper.php` (diff algorithm, formatting), `Style/SymfonyStyle.php` (diff rendering method), `Formatter/OutputFormatterStyle.php` (diff-specific styles: added, removed, changed), and `Helper/HelperSet.php` (diff helper registration).

## Wide

### W1: Implement interactive TUI framework with widget system

Add a terminal UI mode with reusable widgets: text input, select list, checkbox group, data table with pagination, and modal dialogs. Changes span a new `Widget/` directory (base widget, text input, select, checkbox, table, modal), `Cursor.php` (viewport management), `Output/ConsoleSectionOutput.php` (widget region rendering), `Input/Input.php` (raw keyboard event processing), `Style/SymfonyStyle.php` (widget factory methods), and `Application.php` (TUI mode lifecycle).

### W2: Add command workflow engine with step-based execution

Implement `Workflow` class for defining multi-step command pipelines with conditional branching, parallel steps, and rollback. Changes span a new `Workflow/` directory (Workflow, Step, ParallelStep, ConditionalStep, WorkflowRunner), `Command/Command.php` (workflow execution mode), `Application.php` (workflow registration), `Output/ConsoleSectionOutput.php` (step progress rendering), `Event/` (workflow events), and `Tester/` (workflow testing utilities).

### W3: Implement remote command execution over SSH

Add `Application::connectRemote($host)` for executing commands on remote servers via SSH with output streaming back to the local terminal. Changes span `Application.php` (remote session management), `Command/Command.php` (remote execution flag), `Input/ArgvInput.php` (host prefix parsing), `Output/StreamOutput.php` (SSH channel output), `Helper/ProcessHelper.php` (SSH process adapter), and a new `Remote/` directory (SshSession, RemoteOutput, RemoteInput).

### W4: Add plugin system with command auto-discovery

Implement `Application::loadPlugins($path)` for auto-discovering and loading command bundles from filesystem paths or Composer packages. Changes span `Application.php` (plugin registry, command auto-discovery), `CommandLoader/` (PluginCommandLoader), `DependencyInjection/` (plugin compiler pass), `Attribute/AsCommand.php` (plugin metadata), `Descriptor/` (plugin listing in descriptors), and a new `Plugin/` directory (PluginInterface, PluginManager, PluginMetadata).

### W5: Implement comprehensive output theming system

Add configurable themes that control all visual aspects: colors, table styles, progress bar formats, tree styles, and question prompts. Changes span a new `Theme/` directory (Theme, ThemeLoader, ThemeCompiler), `Formatter/OutputFormatterStyle.php` (theme-aware style resolution), `Helper/Table.php` (themed table styles), `Helper/ProgressBar.php` (themed formats), `Helper/TreeHelper.php` (themed tree styles), `Style/SymfonyStyle.php` (theme integration), and `Application.php` (theme loading from config).

### W6: Add command profiling and performance dashboard

Implement execution profiling for all commands: timing, memory usage, I/O operations, signal counts, and sub-command calls. Changes span `Command/TraceableCommand.php` (profiling instrumentation), `Application.php` (global profiling toggle), `DataCollector/CommandDataCollector.php` (profile storage and aggregation), a new `Profile/` directory (Profiler, ProfileReport, ProfileFormatter), `Descriptor/` (profile output in all formats), and `Tester/` (profile assertion helpers).

### W7: Implement structured logging integration across all components

Add PSR-3 structured logging throughout the console lifecycle: command resolution, input parsing, signal handling, question prompts, and external process execution. Changes span `Application.php` (logger injection, command lifecycle logging), `Logger/ConsoleLogger.php` (structured log context), `Command/Command.php` (command-level log context), `Helper/ProcessHelper.php` (process logging), `Helper/QuestionHelper.php` (interaction logging), `SignalRegistry/SignalRegistry.php` (signal logging), and `Input/ArgvInput.php` (parse logging).

### W8: Add internationalization for all console output

Implement `Application::setLocale('fr')` with translated error messages, help text, progress bar labels, table headers, and question prompts. Changes span a new `Translation/` directory (Translator, MessageCatalogue, TranslationLoader), `Resources/translations/` (message files), `Application.php` (locale setting), `Command/Command.php` (translatable descriptions), `Helper/ProgressBar.php` (translatable format labels), `Helper/QuestionHelper.php` (translatable prompts), `Style/SymfonyStyle.php` (locale-aware formatting), and `Descriptor/TextDescriptor.php` (translated output).

### W9: Implement command versioning and deprecation framework

Add `#[AsCommand(since: '5.0', deprecatedAt: '6.0', replacedBy: 'new:command')]` with runtime deprecation warnings, migration guides, and compatibility shims. Changes span `Attribute/AsCommand.php` (version metadata), `Application.php` (deprecation checking, version-based command resolution), `Command/Command.php` (version accessors, migration helper), `Descriptor/` (version annotations in all formats), `Event/` (deprecation events), `Tester/` (deprecation assertion helpers), and `DependencyInjection/AddConsoleCommandPass.php` (compile-time deprecation checks).

### W10: Add distributed command execution with task queue integration

Implement `Application::dispatch('long:command', $args)` that serializes command invocations to a message queue (via Symfony Messenger) and processes them asynchronously with result collection. Changes span `Application.php` (dispatch mode, result collector), `Messenger/RunCommandMessage.php` (enhanced serialization), `Messenger/RunCommandMessageHandler.php` (async execution), `Command/Command.php` (async result reporting), `Output/BufferedOutput.php` (serializable output capture), `Tester/CommandTester.php` (async testing), and a new `Async/` directory (AsyncResult, ResultCollector, CommandSerializer).
