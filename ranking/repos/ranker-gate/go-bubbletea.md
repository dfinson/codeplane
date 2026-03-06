# charmbracelet/bubbletea

| Field | Value |
|-------|-------|
| **URL** | https://github.com/charmbracelet/bubbletea |
| **License** | MIT |
| **Language** | Go |
| **Scale** | Small (focused library) |
| **Category** | Terminal UI framework |
| **Set** | ranker-gate |
| **Commit** | `8cc4f1a832aa6f268e0b7e97a31530c5e961360f` |

## Why this repo

- **Single-purpose**: Elm Architecture-inspired TUI framework for Go.
  Core loop (Init, Update, View) is simple and self-contained. One developer
  can hold the entire codebase in their head.
- **Well-structured**: Clean package layout with the core tea package plus
  supporting modules for input handling, rendering, and key mapping. No deep
  nesting.
- **Rich history**: 2K+ commits, active development, widely adopted (30K+
  stars). Part of the Charm ecosystem but standalone.
- **Permissive**: MIT license.

## Structure overview

```
.
├── tea.go               # Core Program, Model, Cmd types; event loop, Run()
├── commands.go          # Built-in commands (Quit, Batch, Sequence, Tick, Every)
├── renderer.go          # Renderer interface + Println/Printf commands
├── cursed_renderer.go   # Primary rendering implementation (alt screen, flush)
├── nil_renderer.go      # No-op renderer for non-TUI mode
├── screen.go            # WindowSizeMsg, ClearScreen, ModeReportMsg
├── signals_unix.go      # SIGWINCH resize listener (Unix)
├── key.go               # Key event types (KeyPressMsg, KeyReleaseMsg)
├── mouse.go             # Mouse event types (MouseClickMsg, MouseWheelMsg)
├── input.go             # Input event translation (ultraviolet → tea types)
├── tty.go               # TTY init, raw mode, read loop, checkResize
├── exec.go              # ExecProcess / ExecCommand for spawning processes
├── options.go           # ProgramOption functions (WithInput, WithFPS, etc.)
└── logging.go           # LogToFile helpers
```

## Scale indicators

- ~30 Go source files
- ~8K lines of code
- Flat package structure
- Minimal dependencies

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `handleSignals` sending on unbuffered channel without context check

In `tea.go`, `handleSignals()` sends `InterruptMsg{}` and `QuitMsg{}`
directly to `p.msgs` using a bare channel send (`p.msgs <- InterruptMsg{}`).
Unlike `Send()` which uses a `select` on `p.ctx.Done()`, this can block
indefinitely if the event loop has already exited and no one is reading
from the channel. Wrap the signal sends in a `select` with `p.ctx.Done()`
to prevent the goroutine from hanging on shutdown.

### N2: Add `WindowSizeMsg` debouncing

When the terminal is resized by dragging, the program receives a flood
of `WindowSizeMsg` messages that cause expensive re-renders. Add
configurable debouncing to window size events so the program only
receives the final size after the resize gesture completes. Default
debounce interval should be 50ms.

### N3: Fix `checkResize` race condition on width/height fields

In `tty.go`, `checkResize()` writes to `p.width` and `p.height` without
holding `p.mu`, while these fields may be read concurrently by the
renderer or other goroutines. The `startRenderer` goroutine also reads
these values. Add proper mutex protection around the width/height
updates in `checkResize()` to prevent data races.

### N4: Fix `Println` command silently dropping output with `nilRenderer`

When a program is created with `WithoutRenderer()`, the `nilRenderer`
in `nil_renderer.go` implements `insertAbove` as a no-op (`return nil`).
This means the `Println` command in `renderer.go` sends a
`printLineMessage` that gets dispatched to `insertAbove` in the event
loop, but the message is silently discarded. Add a fallback in the
`nilRenderer.insertAbove` that writes directly to the program's output
writer so that `Println` still produces visible output.

### N5: Add `WithLogger` program option to inject a custom logger

The `Program` struct in `tea.go` has a `logger` field (`uv.Logger`)
that is only populated when the `TEA_TRACE` environment variable is
set (in `NewProgram`). There is no `ProgramOption` to inject a logger
programmatically. Add a `WithLogger(logger)` option in `options.go`
so callers can provide their own logger instance without relying on
environment variables. This should integrate with the existing
`setLogger` call on `cursedRenderer` in `Run()`.

### N6: Add `tea.Tick` with custom ID for cancellable timers

The current `tea.Tick` returns a `Cmd` with no way to cancel it. Add
`tea.TickWithID(duration, id)` that returns a cancellable tick. Add
`tea.CancelTick(id)` to cancel a pending tick by ID.

### N7: Fix `ExecProcess` swallowing `RestoreTerminal` error on command failure

In `exec.go`, the `exec` method calls `p.RestoreTerminal()` after a
failed `c.Run()`, but discards the restore error and only sends the
command error via the callback. If both the spawned command and the
terminal restore fail, the caller only sees the command error while
the terminal may be left in a broken state. Chain the errors using
`errors.Join` so the callback receives both failures.

### N8: Add `RequestWindowSize` as a public command

In `commands.go`, `windowSizeMsg` is an unexported type and
`RequestWindowSize()` returns a `Msg` directly (not a `Cmd`). This
means callers cannot request a fresh window size from within `Update`
using the standard command pattern. Rename the internal
`windowSizeMsg` to a public message type and convert
`RequestWindowSize` into a proper `Cmd`-returning function that
triggers `checkResize()` in `tty.go` and delivers a new
`WindowSizeMsg` to the update loop.

### N9: Fix `View.AltScreen` toggle causing double alt-screen enter on startup

In `cursed_renderer.go`, the `flush` method checks
`shouldUpdateAltScreen` by comparing `view.AltScreen` against
`s.lastView.AltScreen`. On the very first render, `s.lastView` is nil
and the code enters `enableAltScreen` unconditionally when
`view.AltScreen` is true. But the `start()` method in
`cursed_renderer.go` also initializes screen state. If a model sets
`AltScreen = true` in its first `View()` call, the alt screen
sequence may be emitted twice—once during `start()` initialization
and once in the first `flush`. Guard the initial alt-screen transition
to avoid the redundant escape sequence.

### N10: Fix `waitForReadLoop` timeout not propagating a diagnostic error

In `tty.go`, `waitForReadLoop()` has a 500ms timeout fallback when the
cancel reader's `readLoopDone` channel doesn't close in time. When the
timeout fires, the function returns silently—no error is logged and no
diagnostic is surfaced. This makes it hard to debug hangs in the input
layer. Add a log message via `p.logger` (when set) and optionally
surface a non-fatal error so callers of `shutdown` and
`releaseTerminal` can detect that the read loop did not exit cleanly.

## Medium

### M1: Implement focus management for composite models

Add a focus management system for applications that compose multiple
interactive sub-models (e.g., a form with multiple text inputs). Provide
`FocusNext`, `FocusPrev` commands and a `Focusable` interface that
models can implement. Tab and Shift-Tab should navigate between focusable
sub-models. Track which sub-model has focus and only forward key events
to the focused model.

### M2: Add built-in animation support

Implement an animation framework that allows smooth transitions between
visual states. Add an `Animate(from, to, duration, easing)` command that
emits interpolated `TickMsg` values over time. Include common easing
functions (linear, ease-in, ease-out, ease-in-out). Support concurrent
animations with independent timers. The animation system should be
composable with existing `tea.Cmd` patterns.

### M3: Implement terminal capability detection

Add automatic detection of terminal capabilities (true color support,
Unicode width, mouse protocol, alt screen, bracketed paste) at program
startup. Surface the detected capabilities through a `TerminalInfo`
message sent before `Init`. Use the capabilities to automatically
select the best mouse protocol and color mode instead of requiring
manual configuration.

### M4: Implement text input component with history

Add a reusable `textinput.Model` with command history (up/down arrow
to navigate previous inputs), history persistence to disk, history
search (Ctrl-R fuzzy search), and max history length configuration.
Support multi-line input with Shift-Enter for newlines.

### M5: Add table component with sorting and filtering

Implement a `table.Model` component for rendering tabular data with
column headers, sortable columns (click header to sort), filterable
rows (type to filter), scrollable body, resizable columns, and
customizable cell rendering. Support keyboard navigation between cells.

### M6: Implement progress bar with ETA estimation

Add a `progress.Model` component that shows a progress bar with
percentage, elapsed time, ETA (estimated time of arrival), and
throughput rate. Support configurable bar characters, color gradients,
and indeterminate mode (pulsing animation for unknown total).

### M7: Add file browser component

Implement a `filebrowser.Model` for navigating the filesystem.
Show directories and files with icons/indicators, support directory
traversal (Enter to open, Backspace to go up), file type filtering,
hidden file toggling, and filepath output on selection. Support
both single file and multi-file selection modes.

### M8: Implement modal dialog system

Add a modal overlay system where a `modal.Model` renders on top of
the underlying view. Support configurable positioning (center, top,
bottom), backdrop dimming, focus trapping (key events only go to the
modal), stacked modals, and dismiss on Escape. The modal should work
with any inner `tea.Model`.

### M9: Add form component with validation

Implement a `form.Model` that composes multiple input fields (text,
select, checkbox, date) into a form with tab navigation between fields,
per-field validation with inline error messages, submit/cancel actions,
and dirty-state tracking. Support required fields and custom validators.

### M10: Implement split-pane layout with drag-to-resize

Add a `split.Model` that divides the terminal into two panes
(horizontal or vertical) with a draggable divider. Support minimum
pane sizes, keyboard-based resize (Alt+arrows), collapsed pane state,
and nested splits. Each pane renders an independent `tea.Model`.

## Wide

### W1: Add accessible screen reader output

Implement a screen reader compatibility layer that runs alongside the
visual renderer. Maintain a logical model of the UI content and emit
OS-specific accessibility announcements when the content changes.
Support NVDA/JAWS on Windows via MSAA, VoiceOver on macOS via
NSAccessibility, and Orca on Linux via AT-SPI. Add `aria-live`
equivalent semantics for dynamic content regions.

### W2: Implement a layout engine inspired by CSS Flexbox

Add a layout system that handles the spatial arrangement of sub-models
within a container. Support flex direction (row/column), wrapping,
alignment (start/center/end/space-between), and flex-grow/shrink
properties. The layout engine should run during the View phase, taking
the terminal width/height and producing positioned render regions
for each child. Integrate with the existing Lipgloss styling.

### W3: Implement remote TUI serving over SSH

Add the ability to serve a Bubbletea application over SSH using the
Wish library integration. An SSH server hosts the TUI, and each SSH
connection gets its own program instance. Support per-session state,
shared state between sessions, terminal capability negotiation per
client, and graceful disconnection handling. Changes span the program
runner, renderer, input handling, and add an SSH server module.

### W4: Add application state persistence and restore

Implement state snapshot and restore for Bubbletea applications. On
exit, serialize the model state to disk. On restart, restore from the
saved state. Support configurable state serialization (JSON, msgpack),
selective state persistence (mark which fields to persist via tags),
and state migration between versions. Changes span the model
interface, program lifecycle, and add a persistence module.

### W5: Implement headless testing framework

Add a testing framework for Bubbletea applications that runs programs
without a real terminal. Provide `teatest.New(model)` that creates a
headless program, `Send(msg)` for injecting messages, `WaitFor(condition)`
for assertion, and `Output()` for capturing rendered frames. Support
simulating key input sequences, mouse events, and window resize.
Changes span the program runner, renderer, and add a test module.

### W6: Add theme system with runtime switching

Implement a theming system that decouples visual styling from model
logic. Define themes as structured style collections (colors, borders,
padding). Support runtime theme switching (light/dark mode toggle),
theme inheritance (base theme + overrides), and theme loading from
TOML/YAML config files. Integrate with Lipgloss for style application.
Changes span all components, rendering, and add a theme module.

### W7: Implement multi-window TUI framework

Add support for running multiple independent TUI windows within a
single program. Each window has its own model, update loop, and render
area. Windows can be tiled (non-overlapping layout) or floating
(overlapping with z-order). Support window creation, destruction,
focus switching, and inter-window messaging. This requires a window
manager, layout coordinator, and input routing layer.

### W8: Add internationalization support

Implement i18n for Bubbletea components. Support locale-aware string
formatting (dates, numbers, currencies), right-to-left text rendering
for Arabic/Hebrew, locale-based keyboard shortcuts, and translation
loading from message files. All built-in components (spinner, progress,
text input) should respect the active locale. Changes span the
renderer, all built-in components, and add a locale module.

### W9: Implement terminal multiplexer integration

Add integration with tmux/screen that allows Bubbletea programs to
manage terminal multiplexer sessions. Create and switch between tmux
panes from within the TUI, run commands in multiplexer panes, capture
pane output, and display multiplexer status. Support both tmux and
screen protocols. Changes span the program runner, add a multiplexer
abstraction, and a command execution module.

### W10: Add plugin system for extensible components

Implement a plugin architecture that allows third-party Go packages to
register custom components, key bindings, and commands with a
Bubbletea application. Plugins declare their models, init commands,
and key maps through a registration interface. Support plugin lifecycle
management, configuration, and sandboxed message handling. Changes span
the program runner, key handling, model composition, and add a plugin
registry.
