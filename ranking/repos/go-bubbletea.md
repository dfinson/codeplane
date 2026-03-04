# charmbracelet/bubbletea

| Field | Value |
|-------|-------|
| **URL** | https://github.com/charmbracelet/bubbletea |
| **License** | MIT |
| **Language** | Go |
| **Scale** | Small (focused library) |
| **Category** | Terminal UI framework |

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
├── tea.go               # Core Program, Model, Cmd types
├── commands.go          # Built-in commands (Quit, Batch, Sequence)
├── renderer.go          # Terminal renderer
├── screen.go            # Screen management (alt screen, mouse)
├── signals.go           # OS signal handling
├── key.go               # Key event types
├── mouse.go             # Mouse event types
├── input.go             # Input reading and parsing
├── cursor/              # Cursor model/component
├── standard_renderer.go # Standard rendering strategy
└── options.go           # Program options
```

## Scale indicators

- ~30 Go source files
- ~8K lines of code
- Flat package structure
- Minimal dependencies

---

## Tasks

8 tasks (3 narrow, 3 medium, 2 wide) for the Go terminal UI framework.

## Narrow

### N1: Fix cursor blink timer leaking on program exit

When a program using a blinking cursor exits, the blink timer goroutine
continues running briefly after the program's `Quit` command is processed.
This causes occasional panics when the timer tries to send on a closed
channel. Fix the shutdown sequence to cancel the blink timer before
closing the program's message channel.

### N2: Add `WindowSizeMsg` debouncing

When the terminal is resized by dragging, the program receives a flood
of `WindowSizeMsg` messages that cause expensive re-renders. Add
configurable debouncing to window size events so the program only
receives the final size after the resize gesture completes. Default
debounce interval should be 50ms.

### N3: Fix mouse click coordinates wrong with alt screen scrollback

When using the alt screen buffer with mouse support enabled, mouse click
coordinates are reported relative to the visible viewport but the program
interprets them relative to the scrollback buffer origin. Fix the mouse
coordinate translation to account for the alt screen offset.

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
