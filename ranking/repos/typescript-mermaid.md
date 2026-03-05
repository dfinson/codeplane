# mermaid-js/mermaid

| Field | Value |
|-------|-------|
| **URL** | https://github.com/mermaid-js/mermaid |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Medium (multi-module project) |
| **Category** | Diagram generation engine |

## Why this repo

- **Multi-module with clear boundaries**: Each diagram type (flowchart,
  sequence, class, state, gantt, pie, ER, git, etc.) is its own subsystem
  with parser, renderer, and data model. Shared infrastructure for layout,
  theming, and configuration sits alongside.
- **Well-structured**: Source under `packages/mermaid/src/` with per-diagram
  directories. Clear separation between parsing (JISON/Langium grammars),
  data models, and D3-based rendering.
- **Rich history**: 4K+ commits, active development, widely used (75K+ stars).
  Regular feature additions (new diagram types) provide varied PR patterns.
- **Permissive**: MIT license.

## Structure overview

```
packages/mermaid/src/
├── diagrams/
│   ├── flowchart/       # Flowchart parser, renderer, styles
│   ├── sequence/        # Sequence diagram subsystem
│   ├── class/           # Class diagram subsystem
│   ├── state/           # State diagram subsystem
│   ├── gantt/           # Gantt chart subsystem
│   ├── pie/             # Pie chart subsystem
│   ├── er/              # ER diagram subsystem
│   ├── git/             # Git graph diagram
│   └── ...              # ~15 diagram types total
├── rendering/           # D3-based rendering infrastructure
├── config/              # Configuration and theming
├── utils/               # Shared utilities
├── mermaidAPI.ts        # Public API
└── mermaid.ts           # Entry point
```

## Scale indicators

- ~200 TypeScript source files
- ~50K lines of code
- Clear per-diagram subsystem boundaries
- Moderate depth (2-3 levels within each diagram type)

---

## Tasks

8 tasks (3 narrow, 3 medium, 2 wide) for the TypeScript diagram generation engine.

## Narrow

### N1: Fix flowchart subgraph title clipping with long text

When a flowchart subgraph has a long title, the title text overflows the
subgraph boundary box and overlaps with nodes outside the subgraph. The
SVG text element is not clipped to the subgraph dimensions. Fix the
subgraph renderer to either truncate the title with an ellipsis or expand
the subgraph box to fit the title.

### N2: Add `direction` support to sequence diagrams

Sequence diagrams currently always flow top-to-bottom. Add support for
a `direction LR` directive that renders the sequence diagram left-to-right,
with participants stacked vertically and messages flowing horizontally.
This requires changes to the sequence diagram parser and renderer.

### N3: Fix class diagram relationship label positioning

In class diagrams, relationship labels (on association lines) overlap
with the line itself when the line is short or nearly vertical. The
label positioning algorithm does not account for line angle. Fix the
label placement to offset labels perpendicular to the line direction
so they are always readable.

## Medium

### M1: Implement Gantt chart critical path highlighting

Add automatic critical path detection and highlighting to Gantt charts.
The critical path (longest dependency chain determining project duration)
should be calculated from task dependencies and highlighted with a
distinct style (bold border or different color). Add a `criticalPath`
theme configuration option. Include tests for various dependency graph
topologies.

### M2: Add interactive click handlers to diagram elements

Implement a click handler system that allows diagram authors to attach
click callbacks or navigation URLs to diagram elements. Support
`click nodeId callback "tooltip"` syntax across flowcharts, class
diagrams, and state diagrams. The SVG output should include proper
cursor styling and ARIA attributes. Sanitize URLs to prevent XSS.

### M3: Implement diagram diff visualization

Add a "diff mode" that takes two versions of the same diagram definition
and renders a single diagram showing additions (green), deletions (red),
and modifications (yellow). This requires parsing both versions, computing
a structural diff of the diagram elements, and rendering the merged
result with diff annotations.

## Wide

### W1: Add accessibility support across all diagram types

Implement comprehensive accessibility for all diagram types. Generate
ARIA labels for all SVG elements, add a text-based alternative description
auto-generated from the diagram definition, support keyboard navigation
between diagram elements, and add screen reader announcements for
interactive elements. Each diagram type needs its own accessibility
strategy based on its semantic content.

### W2: Implement a diagram type plugin system

Refactor the diagram type registration to support third-party diagram
types as plugins. Extract the common diagram lifecycle (parse → layout →
render → interact) into a plugin API. Each diagram type should register
its parser grammar, renderer, theme handler, and accessibility provider
through the plugin interface. Migrate at least two existing diagram
types (pie, git) to use the plugin API as proof of the pattern.

### N4: Fix entity names with underscores breaking ER diagram rendering

In ER diagrams, entity names containing underscores (e.g., `user_profile`)
are rendered with the underscore interpreted as a subscript marker.
The entity name parser doesn't escape underscores before passing to the
SVG text renderer. Fix the ER diagram renderer to escape underscores.

### N5: Fix sequence diagram note box width not adapting to long text

When a `Note over` in a sequence diagram contains long text, the note
box clips the text instead of expanding to fit. The box width is
calculated from the first line only. Fix the note renderer to compute
width from the longest line in the note.

### N6: Add `autonumber` reset syntax to sequence diagrams

Sequence diagrams support `autonumber` for message numbering but provide
no way to reset the counter mid-diagram. Add `autonumber off` (stop
numbering) and `autonumber restart` (reset to 1) directives. Update the
parser and renderer to handle the new keywords.

### N7: Fix pie chart legend overlapping the chart on small viewports

On small viewports, the pie chart legend renders on top of the pie
slices instead of below them. The layout algorithm assumes a fixed
width. Fix the pie chart renderer to detect available space and
reposition the legend below the chart when horizontal space is
insufficient.

### N8: Fix state diagram transition labels truncated at special characters

Transition labels in state diagrams that contain colons, brackets, or
parentheses are truncated at the first special character. The parser
treats these characters as syntax delimiters. Fix the state diagram
parser to support escaped or quoted transition labels.

### N9: Add color customization for individual flowchart nodes

Add per-node styling syntax to flowcharts:
`A[Label]:::customClass` where `customClass` is defined in the diagram
or theme. Currently only global theme colors apply. Fix the flowchart
renderer to apply per-node CSS classes.

### N10: Fix Git graph displaying branch names in wrong order

In the git graph diagram, branch names in the legend appear in creation
order rather than the order they appear in the diagram definition.
When branches are defined in a specific order for clarity, the legend
doesn't respect that order. Fix the legend renderer to use definition
order.

### M4: Implement mindmap auto-layout improvements

The mindmap diagram's automatic layout produces overlapping nodes when
sibling counts exceed 6. Implement a force-directed layout pass that
spreads siblings evenly around the parent. Support configurable spacing,
radial vs tree layout modes, and minimum separation constraints.

### M5: Add live preview with incremental parsing

Implement incremental parsing for the editor integration. When only a
few characters change, re-parse only the affected portion of the
diagram instead of the entire definition. Maintain a parse tree that
can be incrementally updated. Return partial render results while
the full re-render is in progress.

### M6: Implement diagram-to-code reverse engineering

Add `mermaid.fromSVG(svgElement)` that analyzes a rendered diagram SVG
and reconstructs the Mermaid definition text. Support flowcharts,
sequence diagrams, and class diagrams. Handle node labels, edge
relationships, and styling. This enables round-tripping between visual
editing and text editing.

### M7: Add timeline diagram type

Implement a new timeline diagram type for visualizing chronological
events. Support horizontal and vertical layouts, event grouping by
category, date-based positioning (auto-spaced by time intervals),
milestone markers, and period spans (events that span a time range).
Includes parser, data model, and D3-based renderer.

### M8: Add diagram export to multiple formats

Implement export functionality beyond SVG: PNG (via canvas rendering),
PDF (via SVG-to-PDF conversion), and PlantUML text (for
interoperability). Support configurable resolution for raster exports,
transparent backgrounds, and export of multi-page diagrams. Add both
API (`mermaid.export(def, format)`) and CLI support.

### M9: Implement error recovery in diagram parsing

Currently, a single syntax error prevents the entire diagram from
rendering. Implement error recovery in all diagram parsers: when an
error is encountered, skip to the next valid statement and continue
parsing. Render the valid portions of the diagram and highlight the
error locations with inline error indicators.

### M10: Add sequence diagram parallel execution blocks

Implement `par` / `and` / `end` syntax in sequence diagrams for
visualizing parallel message flows. Parallel blocks should be rendered
with a labeled frame showing "par" at the top, with each parallel flow
separated by a horizontal dashed line labeled "and". Support nested
parallel blocks.

### W3: Add real-time collaborative editing

Implement real-time collaboration on Mermaid diagrams. Multiple users
can edit the same diagram definition simultaneously with operational
transform (OT) conflict resolution. Show each user's cursor position
in the text editor and highlight their changes in the rendered diagram.
This requires a collaboration server, OT implementation, cursor
tracking, and differential re-rendering.

### W4: Implement layout engine abstraction layer

Refactor all diagram types to use a common layout engine abstraction.
Currently each diagram type has its own layout logic. Extract the
common layout primitives (box packing, edge routing, auto-sizing,
collision avoidance) into a shared layout module. Migrate at least
four diagram types (flowchart, class, state, ER) to use it. Support
pluggable layout algorithms per diagram type.

### W5: Add interactive diagram editing in the browser

Implement a browser-based visual editor for Mermaid diagrams. Users can
drag nodes, draw edges, edit labels inline, and reorder elements — all
producing valid Mermaid syntax in a synchronized text panel. Support
undo/redo, copy/paste of diagram fragments, and drag-to-create for new
elements. Requires a visual editing layer on top of the SVG renderer,
bidirectional sync between text and visual representations, and edit
operations that map to text mutations.

### W6: Implement diagram versioning with visual diff

Add the ability to compare two versions of a diagram and visualize the
differences. Compute a structural diff (added/removed/modified nodes and
edges), render a merged diagram with color-coded additions (green),
deletions (red), and modifications (yellow), and show a side-by-side
text diff of the definitions. Support integration with Git (diff two
commits of a .mmd file).

### W7: Add comprehensive testing framework for diagrams

Implement a testing framework that validates diagram rendering
correctness. Support snapshot testing (compare rendered SVG against
baseline), visual regression testing (pixel-level comparison with
configurable tolerance), accessibility testing (verify ARIA labels and
semantic structure), and performance testing (render time budgets).
Requires test runner integration, baseline management, and a reporting
system.

### W8: Implement server-side rendering with caching

Add a server-side rendering mode for Mermaid. A Node.js server receives
diagram definitions via HTTP, renders them to SVG/PNG/PDF using headless
rendering, caches results keyed by definition hash, and supports batch
rendering. Include cache invalidation on theme changes, concurrent
rendering with worker threads, and an admin API for cache management.
Changes span the rendering pipeline, add an HTTP server, caching layer,
and worker pool.

### W9: Add semantic zoom for large diagrams

Implement semantic zoom that changes the level of detail based on zoom
level. When zoomed out, show only top-level groups/packages with
aggregate information. When zoomed in, show full detail with individual
nodes and edges. Support smooth zoom transitions, minimap navigation,
and keyboard shortcuts. Requires a level-of-detail system, viewport
management, and incremental rendering to handle diagrams with hundreds
of nodes.

### W10: Implement diagram composition and imports

Add the ability to compose large diagrams from smaller files. Support
`import ./auth-flow.mmd as AuthFlow` syntax that includes an external
diagram definition as a subgraph. Handle namespace isolation (node IDs
in imported diagrams don't collide), cross-file edge references, and
recursive imports with cycle detection. Changes span the parser, module
resolution, namespace management, and rendering pipeline.
