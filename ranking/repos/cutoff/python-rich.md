# Textualize/rich

| Field | Value |
|-------|-------|
| **URL** | https://github.com/Textualize/rich |
| **License** | MIT |
| **Language** | Python |
| **Scale** | Medium |
| **Category** | Terminal formatting library |
| **Set** | Cutoff |
| **Commit** | `fc41075a3206d2a5fd846c6f41c4d2becab814fa` |

## Why this repo

- **Well-structured**: Clean single-package layout under `rich/` with
  per-concern modules — console output (`console.py`), styled text
  (`text.py`), color system (`color.py`, `style.py`), tables
  (`table.py`), panels (`panel.py`), trees (`tree.py`), syntax
  highlighting (`syntax.py`), markdown rendering (`markdown.py`),
  progress bars (`progress.py`), live display (`live.py`), and
  tracebacks (`traceback.py`). One developer can follow the full
  render pipeline from widget to terminal segments.
- **Rich history**: 5K+ commits, 50K+ stars. The most widely used
  Python terminal formatting library. PRs cover rendering edge cases,
  new renderables, and style system extensions.
- **Permissive**: MIT license.

## Structure overview

```
rich/
├── console.py         # Console — main entry point for all output
├── text.py            # Text object with per-character styling
├── style.py           # Style — color + attributes (bold, italic, etc.)
├── color.py           # Color parsing and representation (named, hex, RGB)
├── table.py           # Table rendering with column sizing
├── panel.py           # Panel — bordered content boxes
├── tree.py            # Tree — hierarchical display with guides
├── syntax.py          # Syntax highlighting via Pygments
├── markdown.py        # Markdown rendering to terminal
├── progress.py        # Progress bars and task tracking
├── live.py            # Live display with auto-refresh
├── layout.py          # Screen layout with splitters
├── traceback.py       # Rich tracebacks with syntax highlighting
├── pretty.py          # Pretty printing of Python objects
├── logging.py         # RichHandler for stdlib logging
├── prompt.py          # User prompts (Prompt, Confirm, IntPrompt)
├── markup.py          # BB-code-style markup parser ([bold]...[/bold])
├── segment.py         # Segment — low-level styled text unit for rendering
├── measure.py         # Measurement protocol for widget sizing
├── _inspect.py        # inspect() — object introspection display
├── columns.py         # Column layout for iterables
├── align.py           # Alignment renderable (left, center, right)
├── padding.py         # Padding renderable
├── rule.py            # Horizontal rule
├── spinner.py         # Spinner animations
├── status.py          # Status display with spinner
├── highlighter.py     # Base highlighter and RegexHighlighter
├── theme.py           # Theme — named style collections
├── box.py             # Box characters for tables, panels, borders
└── emoji.py           # Emoji shortcode support
```

## Scale indicators

- ~60 Python source files
- ~20K lines of code
- Flat structure (1 level, single package)
- Minimal dependencies (only `markdown-it-py` and `pygments`)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add overflow strategy parameter to Panel for long content

When content inside a `Panel` exceeds the panel width, it is wrapped
by default, but there is no option to truncate with an ellipsis or crop
instead. Add an `overflow` parameter to `Panel.__init__()` in `panel.py`
that accepts `"wrap"`, `"ellipsis"`, or `"crop"` and passes it through
to the renderable's `ConsoleOptions` so content wider than the panel is
handled according to the chosen strategy.

### N2: Add Style.subtract() method for removing specific attributes

The `Style` class in `style.py` supports combining styles via `__add__`
but has no way to selectively remove attributes (e.g., remove `bold`
from a combined style while keeping color and italic). Add a
`Style.subtract(other)` method that returns a new `Style` with attributes
from `other` cleared, useful for style overrides in nested renderables
where a child needs to undo a parent's styling.

### N3: Add named color alias registry to Color class

The `Color` class in `color.py` supports standard named colors and hex
codes but does not allow users to register custom named aliases (e.g.,
`"brand-primary"` → `"#336699"`). Add a `Color.register_alias(name, color_str)`
class method that stores aliases in a module-level dictionary consulted
by `Color.parse()` before falling back to the standard color lookup,
so themes can define semantic color names. Also update `CHANGELOG.md` to document the new alias registration API with usage examples.

### N4: Fix Table not rendering empty cells when show_lines=True

When a `Table` has `show_lines=True` and a cell contains an empty
string, the row separator lines are misaligned because the empty cell
is measured as zero height instead of one line. Fix the cell height
calculation in `table.py` to ensure empty cells occupy at least one
line when `show_lines` is enabled.

### N5: Add unescape() utility to markup module

The `markup.py` module provides `escape()` to protect bracket characters
from being interpreted as markup tags, but there is no inverse function
to undo that escaping. Add `unescape(markup: str) -> str` to `markup.py`
that reverses escaped sequences by replacing `\[` with `[` and `\\` with
`\`, so callers can recover the original user-supplied string from a
previously escaped markup value. Also export `unescape` in
`markup.__all__` alongside `escape` and `render`.

### N6: Fix Segment.split_cells not handling zero-width characters

`Segment.split_cells()` in `segment.py` incorrectly counts zero-width
characters (ZWJ, combining marks) as occupying one cell, causing
misaligned output when splitting segments at a cell boundary. Fix the
cell measurement to use `cell_len()` for zero-width character detection.

### N7: Add Tree.sort_children() for alphabetical node ordering

The `Tree` class in `tree.py` stores children in insertion order via
`self.children: List[Tree]`, but there is no built-in way to sort child
nodes alphabetically or by a custom key. Add a `sort_children(key=None, reverse=False)` method that sorts the children list in place,
recursively sorting subtrees when `recursive=True` is passed.

### N8: Fix Panel title truncation not adding ellipsis

When a `Panel` title is longer than the panel width, the title is
hard-truncated without any visual indicator. Inside
`Panel.__rich_console__()` in `panel.py`, the inner `align_text()`
function calls `text.truncate(width)` with no overflow indicator. Fix
`align_text()` to detect when truncation has occurred and append an
ellipsis (`…`) to the title text, reserving one cell of width for it, so
users can tell the title was cut short.

### N9: Add IntPrompt with range validation to prompt module

The `prompt.py` module provides `IntPrompt` for integer input but does
not offer built-in numeric range validation — users must re-prompt
manually if the value is out of bounds. Add optional `min_value` and
`max_value` parameters to `IntPrompt` that validate the parsed integer
against the range and re-prompt with a message like
`"Please enter a value between 1 and 100"` when the constraint is
violated.

### N10: Add max_lines parameter to Pretty for large output truncation

The `pretty.py` module's `Pretty` renderable and `pretty_repr()` function
have no way to limit the total number of output lines, so deeply nested
or wide structures can produce hundreds of lines of output. Add a
`max_lines` parameter to the `Pretty` class, `pretty_repr()`, and the
internal `_Layout.__rich_console__()` rendering path that caps the
rendered output at the given line count and appends a
`"... (+N lines)"` continuation indicator when truncation occurs.

## Medium

### M1: Implement column-span support in Table

Add support for cells that span multiple columns in `Table`. Introduce a
`colspan` parameter on `Table.add_row()` that allows a cell to stretch
across adjacent columns. Requires changes to the column sizing algorithm
in `table.py`, the cell rendering loop to merge columns, and the border
drawing logic to omit internal column separators for spanned cells.

### M2: Add style inheritance for nested renderables

Implement style inheritance so that a `Panel` or `Group` can set a base
style that all child renderables inherit unless overridden. Requires
changes to `Console.render()` to pass an inherited style down the render
tree, updates to `Text` and other renderables to merge the inherited
style with their own, and changes to `Segment` to apply cascading styles.

### M3: Implement conditional markup tags based on terminal capabilities

Add conditional markup syntax `[on color_system>=256]...[/on]` that only
applies enclosed styles when the terminal supports the required color
depth. Requires extending the markup parser in `markup.py` to recognize
conditional tags, querying `Console.color_system` during rendering, and
updating the `Style` resolution to skip unsupported styles gracefully.

### M4: Add CSV and TSV export for Table

Implement `Table.export_csv()` and `Table.export_tsv()` methods that
output table data as comma- or tab-separated values, stripping all
styling. Requires extracting plain text from styled cells using
`Text.plain`, handling multi-line cells by joining with spaces, and
supporting header inclusion/exclusion. Add corresponding
`Console.save_csv()` convenience methods.

### M5: Implement collapsible sections in Tree

Add a `collapsed` parameter to `Tree.add()` that marks a subtree as
collapsed by default. When rendered, collapsed nodes show a `[+]`
indicator instead of their children. When used with `Live`, clicking
or pressing a key toggles expansion. Requires changes to `Tree`
rendering, guide character logic, and integration with `Live` for
interactive toggling. Also update `CONTRIBUTING.md` to document the interactive widget testing guidelines.

### M6: Add word-wrap mode to Text with hyphenation

Implement a `wrap_mode="word"` option for `Text` that wraps at word
boundaries instead of character boundaries. When a single word exceeds
the available width, apply simple hyphenation rules (break at syllable
boundaries using a basic algorithm). Requires changes to `Text.wrap()`,
the `Segment` splitting logic, and the `Console.render()` width
calculation.

### M7: Implement progress bar ETA smoothing with exponential moving average

Replace the current linear ETA estimation in `progress.py` with an
exponential moving average that smooths out speed fluctuations. Add a
`smoothing` parameter to `Progress` that controls the EMA weight.
Requires changes to `Task.speed`, the `TimeRemainingColumn`
calculation, and the `TransferSpeedColumn` display.

### M8: Add theme hot-reloading from TOML files

Implement `Theme.from_toml()` that loads styles from a TOML file and
`Console.load_theme()` that watches the file for changes and reloads
styles automatically. Requires a TOML parser integration in `theme.py`,
a file-watcher thread in `Console`, and re-rendering of active `Live`
displays when the theme changes.

### M9: Implement Segment-level caching for repeated renders

Add a render cache to `Console` that stores `Segment` output for
renderables that have not changed. Use a content-hash key so that
identical renderables hit the cache. Requires a cache store in
`Console`, a `__rich_hash__()` protocol method on renderables, and
cache invalidation when the console width changes.

### M10: Add accessibility attributes to Console output

Implement ANSI OSC sequences for screen reader accessibility: add a
`role` parameter to `Panel`, `Table`, and `Tree` that emits semantic
annotations. Add `Console.announce()` for screen reader announcements.
Requires changes to `Segment` to carry accessibility metadata, updates
to the `Console._render_buffer()` to emit OSC sequences, and additions
to `Panel`, `Table`, and `Tree` for role propagation.

## Wide

### W1: Implement a terminal UI widget system with focus management

Add an interactive widget system with `Input`, `Button`, `Select`, and
`Checkbox` widgets that render in a `Live` context and respond to
keyboard input. Implement focus management with tab-order navigation,
a `Form` container that groups widgets, and a submit action that returns
collected values. Changes span `live.py` for input handling, new widget
modules, `console.py` for raw-mode input, `segment.py` for cursor
positioning, and `style.py` for focus-state styles.

### W2: Enhance HTML export with table layouts and interactive features

The existing `Console.export_html()` and `Console.save_html()` in
`console.py` render all output as styled `<span>` elements, losing
structural information: tables are flattened to text, syntax blocks
use inline styles instead of Pygments CSS classes, and there is no
dark/light theme toggle. Enhance the HTML export pipeline by: adding
`Table.__rich_html__()` in `table.py` that emits a `<table>` element
with CSS grid layout; adding `Syntax.highlight_to_html()` in
`syntax.py` that returns Pygments CSS classes instead of inline
rules; adding `Style.to_css()` in `style.py` and `Theme.to_css()`
in `theme.py` for stylesheet generation; extracting the HTML
template and JavaScript dark/light toggle into a new
`_html_export.py` module; and updating `Console.export_html()` to
use the new structural rendering path. Also update `Console.save_html()`
to accept a `theme_toggle` boolean parameter.

### W3: Implement a dashboard layout system with auto-refreshing panels

Add a `Dashboard` class that arranges multiple `Live` renderables in a
grid layout computed from terminal dimensions. Each panel auto-refreshes
independently at configurable intervals. Support panel resizing on
terminal resize, panel-level titles and borders, and a status bar.
Changes span `layout.py` for grid computation, `live.py` for multi-
renderable refresh, `console.py` for terminal resize detection,
`panel.py` for titled containers, and `segment.py` for screen
buffer management.

### W4: Implement a theming and plugin system for custom renderables

Add a plugin architecture that allows third-party packages to register
custom renderables, styles, and markup tags via entry points. Implement
a `PluginManager` that discovers plugins, validates their interfaces,
resolves conflicts (duplicate tag names), and exposes them through the
`Console` API. Changes span `console.py` for plugin integration, a new
`plugins.py` module, `markup.py` for custom tag dispatch, `theme.py`
for plugin-provided styles, and `style.py` for extended attribute
registration.

### W5: Add internationalization support for all user-facing strings

Implement i18n across Rich's user-facing output. All error messages,
progress bar labels ("eta", "elapsed", "speed"), prompt defaults
("y/n"), column headers in `inspect()`, and traceback labels should be
translatable via gettext. Support locale detection from environment
variables and user-provided translation catalogs. Changes span
`progress.py`, `prompt.py`, `_inspect.py`, `traceback.py`,
`logging.py`, and a new `_locale.py` module.

### W6: Implement a test harness for renderable output verification

Add a testing framework `rich.testing` that captures rendered output
as `Segment` lists and provides assertion helpers: `assert_contains`,
`assert_style`, `assert_dimensions`, and snapshot testing for visual
regression. Include a pytest plugin with fixtures for `Console`
capture. Changes span a new `testing.py` module, a pytest plugin
module, `console.py` for capture mode, `segment.py` for segment
comparison, and `style.py` for style matching utilities.

### W7: Implement streaming log viewer with filtering and search

Add a `LogViewer` renderable that displays log records in a scrollable,
filterable live view. Support level-based coloring, regex search with
match highlighting, log source filtering, and timestamp formatting.
Implement a ring buffer for log storage and virtual scrolling for
performance. Changes span a new `log_viewer.py` module, `live.py` for
scroll input handling, `console.py` for raw-mode keyboard input,
`text.py` for search highlighting, `highlighter.py` for log-level
coloring, and `logging.py` for RichHandler integration.

### W8: Add automatic API documentation generation from Console recordings

Implement `Console.record()` mode that captures all output operations
as a replayable transcript, and `Console.export_docs()` that generates
Markdown or RST documentation from recordings. Support annotating
recordings with descriptions, grouping related outputs into sections,
and including code examples alongside rendered output. Changes span
`console.py` for recording, a new `docs_export.py` module, `text.py`
for plain-text extraction, `table.py` for Markdown table conversion,
`syntax.py` for code block formatting, and `markup.py` for markup-
to-Markdown translation.

### W9: Implement a diff viewer with side-by-side rendering

Add a `Diff` renderable that displays unified or side-by-side diffs
with syntax highlighting, line numbers, and inline change highlighting.
Support configurable context lines, word-level diff highlighting within
changed lines, and collapsible unchanged sections. Changes span a new
`diff.py` module, `syntax.py` for language-aware highlighting, `text.py`
for word-level diff marking, `table.py` for side-by-side layout,
`panel.py` for diff headers, and `console.py` for pager integration.

### W10: Implement a notification and toast system for terminal applications

Add a `Notifications` system that displays temporary messages (toasts)
overlaid on existing console output. Support priority levels (info,
warning, error), auto-dismiss timers, stacking of multiple notifications,
animation (slide-in/fade), and an action callback for interactive
toasts. Changes span a new `notifications.py` module, `live.py` for
overlay rendering, `console.py` for screen buffer compositing,
`segment.py` for z-order layering, `style.py` for notification-level
theming, and `layout.py` for overlay positioning.

### N11: Add [Unreleased] section to CHANGELOG.md

The `CHANGELOG.md` follows Keep a Changelog format but has no
`[Unreleased]` section at the top to accumulate changes that have not
yet been assigned a version. Add an `## [Unreleased]` heading immediately
after the introductory paragraph with empty `### Added`, `### Changed`,
and `### Fixed` subsections, and add a corresponding
`[Unreleased]: https://github.com/Textualize/rich/compare/v14.3.3...HEAD`
link entry at the bottom of the file alongside the existing version
comparison URLs.

### M11: Add benchmark tox environment and Makefile target

The `tox.ini` has `lint` and `docs` environments but no dedicated
environment for running the `asv` benchmarks under `benchmarks/`.
Add a `bench` tox environment that installs `asv` and runs
`asv run --quick` against the current working tree. Update the
`Makefile` to add a `make bench` target that invokes the new tox
environment. Also update `.coveragerc` to add `benchmarks/` to the
`omit` list so benchmark helper code is excluded from coverage
reports.

### W11: Comprehensive project configuration and documentation overhaul

Perform a full non-code refresh: update `pyproject.toml` metadata
with current classifiers, project URLs, and PEP 639 license
declarations. Revise `CONTRIBUTING.md` to add sections on Rich
renderable development guidelines and visual regression testing.
Restructure `CHANGELOG.md` to follow Keep a Changelog format across
all versions. Update `tox.ini` to add type-checking and linting
environments. Revise the `Makefile` with consolidated targets.
Update `.coveragerc` to add branch coverage and exclusion patterns.
Update `.pre-commit-config.yaml` hook versions and add `ruff`
formatting hooks.
