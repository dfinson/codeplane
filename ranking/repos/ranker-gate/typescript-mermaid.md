# mermaid-js/mermaid

| Field | Value |
|-------|-------|
| **URL** | https://github.com/mermaid-js/mermaid |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Medium (multi-module project) |
| **Category** | Diagram generation engine |
| **Set** | ranker-gate |
| **Commit** | `6e40ff272949ef2eec09c6efd42b6284b3d51148` |

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
│   └── ...              # ~25+ diagram types total
├── rendering-util/      # D3-based rendering utilities
├── config.ts            # Configuration module
├── defaultConfig.ts     # Default configuration values
├── utils.ts             # Shared utilities
├── utils/               # Additional utility modules
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

30 tasks (10 narrow, 10 medium, 10 wide).

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

### N6: Fix ER diagram attribute column misalignment with varying type lengths

In ER diagrams, the `drawAttributes` function in `erRenderer.js` positions
attribute type, name, key, and comment text nodes row by row independently.
When an entity has attributes with significantly different type name lengths
(e.g., `varchar(255)` vs `int`), the name column is not vertically aligned
because each row offsets the name text relative to its own type text width.
Fix `drawAttributes` to pre-compute the maximum width for each column across
all attributes and use those widths for consistent column positioning.

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

### N9: Fix Kanban card label overflow beyond column boundary

In the Kanban diagram renderer (`kanbanRenderer.ts`), card item nodes are
created via `insertNode` without constraining their width to the parent
column. When a card's label text is longer than the column width, the
rendered node extends beyond the column's right edge and overlaps adjacent
columns. Fix the renderer to set a maximum width on item nodes relative
to the computed column width before calling `insertNode`.

### N10: Fix git graph commit labels overlapping on closely spaced commits

In the git graph renderer (`gitGraphRenderer.ts`), the `drawCommitLabel`
function positions commit message labels horizontally below each commit
point. When commits on the same branch are closely spaced, labels overlap
because the positioning does not account for adjacent label widths. Fix
the label positioning to detect bounding box collisions between adjacent
commit labels and stagger them vertically when overlap occurs.

## Medium

### M1: Implement Gantt chart critical path highlighting

Add automatic critical path detection and highlighting to Gantt charts.
The critical path (longest dependency chain determining project duration)
should be calculated from task dependencies and highlighted with a
distinct style (bold border or different color). Add a `criticalPath`
theme configuration option. Include tests for various dependency graph
topologies.

### M2: Add xychart axis gridline customization

The xychart diagram renders axes with default gridlines that use hardcoded
styles. Add configuration properties for gridline appearance: `showGridLines`
(boolean, per axis), `gridLineColor`, `gridLineDashArray`, and
`gridLineWidth`. These properties should be configurable via the diagram
definition syntax and the theme. Requires updates to the xychart chart
builder components in `chartBuilder/`, `xychartDb.ts` for configuration
storage, and `xychartRenderer.ts` to apply the configured styles during
D3 axis rendering.

### M3: Implement diagram diff visualization

Add a "diff mode" that takes two versions of the same diagram definition
and renders a single diagram showing additions (green), deletions (red),
and modifications (yellow). This requires parsing both versions, computing
a structural diff of the diagram elements, and rendering the merged
result with diff annotations.

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

### M7: Add radar diagram custom axis scale ranges

The radar diagram (`diagrams/radar/`) renders axes with auto-scaled values
derived from data bounds. Add per-axis configuration for minimum, maximum,
and step values via syntax like `axis "Speed" min 0 max 100 step 20`.
When custom scales are specified, the renderer should use the given range
and draw tick marks at the specified intervals instead of auto-fitting.
Requires changes to the parser (`parser.ts`), database (`db.ts`), type
definitions (`types.ts`), and renderer (`renderer.ts`).

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

### M10: Add quadrant-chart per-quadrant color and label customization

The quadrant chart renders four quadrants with uniform styling from the
theme. Add syntax for customizing individual quadrant background colors
and label font styles (e.g., `quadrant-1 color #ff0000 font-size 14`).
Support gradient fills between adjacent quadrants. Requires changes to
`quadrantBuilder.ts` for style configuration, `quadrantDb.ts` for
parsing and storing per-quadrant styles, the parser for new syntax rules,
and `quadrantRenderer.ts` to apply custom colors and fonts during rendering.

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
