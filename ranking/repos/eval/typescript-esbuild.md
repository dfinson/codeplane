# evanw/esbuild

| Field | Value |
|-------|-------|
| **URL** | https://github.com/evanw/esbuild |
| **License** | MIT |
| **Language** | Go (core), TypeScript (API) |
| **Scale** | Large |
| **Category** | JS/TS bundler |
| **Set** | eval |
| **Commit** | `9129e00e6c36a3e374820cb5e3fc2cd319c8ab85` |

## Why this repo

- **Multi-subsystem**: JS/TS parser, CSS parser, module resolver, bundler, linker, code printer, source map engine, tree shaking
- **Well-structured**: Clean `internal/` package layout with distinct phases (parse → resolve → bundle → link → print)
- **Performance-critical**: Single-author codebase with extreme optimization focus, 150K+ LOC Go

## Structure overview

```
pkg/
├── api/                     # Public Go API (Build, Transform)
├── cli/                     # CLI argument parsing
internal/
├── js_parser/               # JavaScript/TypeScript parser (~19K LOC)
│   ├── js_parser.go         # Core parser: expressions, statements, modules
│   ├── ts_parser.go         # TypeScript-specific: type annotations, enums, decorators
│   ├── js_parser_lower.go   # Syntax lowering transforms
│   ├── js_parser_lower_class.go # Class feature lowering
│   ├── json_parser.go       # JSON parser
│   └── sourcemap_parser.go  # Source map parser
├── js_lexer/                # JavaScript/TypeScript lexer
├── js_ast/                  # JavaScript AST node types
├── js_printer/              # JavaScript code generator
├── css_parser/              # CSS parser with nesting, calc, color spaces
├── css_lexer/               # CSS tokenizer
├── css_ast/                 # CSS AST types
├── css_printer/             # CSS code generator
├── bundler/                 # Entry point scanning, file loading orchestration
├── linker/                  # Module linking, chunk splitting, cross-chunk imports (~7K LOC)
├── graph/                   # File dependency graph and chunk assignment
├── resolver/                # Module resolution (node, browser, neutral platforms)
├── config/                  # Build configuration types
├── compat/                  # Browser/engine compatibility tables
├── renamer/                 # Identifier minification
├── sourcemap/               # Source map generation and merging
├── cache/                   # Incremental build cache
├── fs/                      # Virtual and real filesystem abstraction
├── helpers/                 # Bit sets, string helpers, hash utilities
└── runtime/                 # Injected runtime helpers
lib/
├── shared/                  # TypeScript API types and stdio protocol
│   ├── types.ts             # Public TypeScript type definitions
│   ├── common.ts            # Shared JS/TS API implementation
│   └── stdio_protocol.ts   # Binary protocol between JS and Go
├── npm/                     # npm package entry points
└── deno/                    # Deno module entry
```

## Scale indicators

- ~150 Go source files
- ~148K lines of Go code + ~5K lines of TypeScript
- Single-pass parser/bundler architecture
- Parallel file loading and linking

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix tree shaking not removing unused `enum` members in TypeScript

When a TypeScript `enum` is partially used, the entire enum declaration is kept in the output. The tree shaking logic in `internal/linker/linker.go` marks the whole enum declaration as live when any member is referenced, instead of emitting only used members via `internal/js_printer/js_printer.go`.

### N2: Fix source map column offsets incorrect after minification with `mangleProps`

When `MangleProps` is enabled with source maps, the column offsets in the generated source map don't account for the shortened property names. The source map generation in `internal/js_printer/js_printer.go` records positions before property mangling is applied.

### N3: Add `--log-format=json` for machine-readable error output

Build errors and warnings are formatted for human consumption. Add a JSON output format to `internal/logger/` that emits structured error objects with file, line, column, message, and error code, parseable by editors and CI systems.

### N4: Fix CSS `@import` resolution not respecting `browser` field in `package.json`

When a CSS file uses `@import 'package/style.css'`, the resolver in `internal/resolver/resolver.go` checks `main` and `module` fields but doesn't consult the `browser` field for CSS entry remapping, unlike JS resolution.

### N5: Fix `keepNames` not preserving names for function expressions in `for` loop initializers

When `keepNames` is enabled, function expressions assigned in `for` statement initializer declarations like `for (var fn = function() {}; ...; ...)` are not wrapped with the `__name` helper, even though the equivalent statement-level declaration `var fn = function() {}` correctly receives name preservation. The name-propagation logic in `internal/js_parser/js_parser.go` handles `SLocal` variable declarations at statement level but does not propagate `nameToKeep` for the initializer clause of `for` statements.

### N6: Add `--analyze=detailed` flag for per-module size breakdown

The `--analyze` flag shows file sizes but doesn't show how much of each file's size comes from code vs. comments vs. strings. Add per-module detailed breakdown to `internal/linker/linker.go` by tracking AST node categories during printing.

### N7: Fix `/* @__PURE__ */` annotations discarded before tagged template literal expressions

When `/* @__PURE__ */ tag\`template\`` appears in source, the annotation comment is consumed by the lexer but does not mark the tagged template call as side-effect-free for tree-shaking purposes. The pure-annotation handling in `internal/js_parser/js_parser.go` applies the annotation to `ECall` and `ENew` expressions but not to `ETemplate` expressions that have a tag, so unused pure-annotated tagged template calls are never eliminated by the tree shaker.

### N8: Fix CSS `composes` from external files not resolving relative paths correctly

When CSS modules use `composes: name from './other.module.css'`, the `composes` resolution in `internal/css_parser/css_decls_composes.go` resolves relative to the output directory instead of the source file's directory.

### N9: Fix `--drop-labels` not warning when a specified label name is never found

When `--drop-labels=DEV` is specified but no `DEV: { ... }` labeled statement appears in the processed files, esbuild silently succeeds with no indication that the label name was unused—making it impossible to catch typos such as `--drop-labels=DEv`. Add a diagnostic in `internal/js_parser/js_parser.go` that tracks which label names from `DropLabels` (`internal/config/config.go`) were actually matched, and emits a warning via the logger when a specified label name was never encountered in a file.

### N10: Fix chunk splitting not respecting `sideEffects: false` in `package.json`

When code splitting creates chunks, modules marked with `sideEffects: false` in their `package.json` are still included in chunks even when their exports aren't used by that chunk. The chunk assignment in `internal/graph/graph.go` doesn't consult side-effects metadata during splitting.

### N11: Update `CHANGELOG.md` and `docs/architecture.md` to document the `--drop-labels` unused-label warning

Add a `CHANGELOG.md` entry describing the new warning emitted when a label name specified via `--drop-labels` is never encountered in the processed files, including a usage example showing how the warning helps catch typos. Update `docs/architecture.md` to document the label-stripping mechanism in the parse pipeline section, explaining how `DropLabels` is matched against labeled statements and when the diagnostic fires.

## Medium

### M1: Implement import map support for module resolution

Add `--import-map=importmap.json` that overrides module resolution using the WICG import maps specification. Changes span `internal/resolver/resolver.go` for resolution override, `internal/config/config.go` for the option, `pkg/api/api.go` for API exposure, and `internal/bundler/bundler.go` for import map loading.

### M2: Add CSS custom property tracking and dead code elimination

Track `var(--custom-prop)` usage across CSS files and remove unused `:root { --unused: value }` declarations. Changes span `internal/css_parser/css_parser.go` for variable tracking, `internal/css_parser/css_decls.go` for declaration analysis, `internal/linker/linker.go` for cross-file variable usage, and `internal/css_printer/css_printer.go` for elimination.

### M3: Implement bundle size budget enforcement

Add `--size-budget=500kb` that fails the build when the total output exceeds the budget and per-entry budgets via API. Changes span `internal/config/config.go` for budget configuration, `internal/linker/linker.go` for size calculation after linking, `pkg/api/api.go` for the API option, and `internal/logger/` for budget violation reporting. Update `docs/development.md` with budget testing procedures and add a `CHANGELOG.md` entry.

### M4: Add TypeScript project references support

Implement `--tsconfig-paths` that resolves `paths` and `references` from `tsconfig.json` for multi-project TypeScript repos. Changes span `internal/resolver/resolver.go` for path resolution, `internal/bundler/bundler.go` for tsconfig loading, `internal/config/config.go` for options, and `internal/js_parser/ts_parser.go` for project-scoped type handling.

### M5: Implement persistent build cache for incremental rebuilds

Add file-system-based caching of parse and resolution results keyed by file content hash. Changes span `internal/cache/` for cache storage, `internal/bundler/bundler.go` for cache-aware file loading, `internal/js_parser/js_parser.go` for AST serialization, and `internal/resolver/resolver.go` for cached resolution.

### M6: Add CSS `@layer` ordering validation and optimization

Validate that `@layer` orderings are consistent across files and optimize layer declarations by merging compatible layers. Changes span `internal/css_parser/css_parser.go` for layer tracking, `internal/css_parser/css_nesting.go` for nested layer handling, `internal/linker/linker.go` for cross-file layer ordering, and `internal/css_printer/css_printer.go` for optimized output.

### M7: Implement module federation plugin support

Add a plugin API extension for Module Federation: shared modules, remote entry generation, and federated imports. Changes span `pkg/api/api.go` for plugin hooks, `internal/bundler/bundler.go` for federated resolution, `internal/linker/linker.go` for shared chunk generation, and `internal/js_printer/js_printer.go` for runtime federation code.

### M8: Add scope hoisting for CJS-to-ESM conversion

When bundling CommonJS modules into ESM output, implement scope hoisting that inlines `require()` calls as direct references when safe. Changes span `internal/js_parser/js_parser.go` for CJS pattern analysis, `internal/linker/linker.go` for scope merging decisions, `internal/graph/graph.go` for dependency tracking, and `internal/js_printer/js_printer.go` for inlined output.

### M9: Implement configurable CSS minification levels

Add granular CSS minification options: `minifyColors`, `minifyGradients`, `minifyFontWeight`, `mergeDuplicateRules`. Changes span `internal/config/config.go` for options, `internal/css_parser/css_decls_color.go` for color optimization, `internal/css_parser/css_decls_gradient.go` for gradient shortening, `internal/css_parser/css_decls_font_weight.go` for numeric weights, and `internal/css_printer/css_printer.go` for conditional minification.

### M10: Add build manifest generation with asset integrity hashes

Generate a JSON manifest mapping input entry points to output files with content hashes, chunk dependencies, and subresource integrity (SRI) hashes. Changes span `internal/linker/linker.go` for manifest data collection, `internal/config/config.go` for manifest options, `pkg/api/api.go` for API exposure, and add manifest serialization logic.

### M11: Add build pipeline documentation and CI validation for import maps

Update `docs/architecture.md` with the import map resolution flow and precedence rules. Add import map integration tests to `.github/workflows/ci.yml` covering resolution overrides and scoped mappings. Update `RUNBOOK.md` with import map debugging procedures and common pitfalls. Add a `CHANGELOG.md` entry for the import map feature.

## Wide

### W1: Implement HTML entry point support with asset pipeline

Add HTML files as entry points that automatically discover and bundle `<script>`, `<link>`, and inline assets. Changes span `pkg/api/api.go` for HTML loader, `internal/config/config.go` for loader type, `internal/bundler/bundler.go` for HTML parsing and asset extraction, `internal/linker/linker.go` for HTML output with hashed references, `internal/resolver/resolver.go` for asset resolution, and add HTML parser/printer modules. Update `README.md` with HTML entry point examples and usage guide, and add entries to `CHANGELOG.md`.

### W2: Add watch mode with incremental rebuild and HMR server

Implement file watching with dependency-aware incremental rebuilds and a dev server that pushes hot module replacement updates. Changes span `internal/bundler/bundler.go` for incremental tracking, `internal/cache/` for invalidation, `internal/linker/linker.go` for partial re-linking, `internal/graph/graph.go` for dependency tracking, `pkg/api/api.go` for watch API, `lib/shared/` for TS API, and add HMR server and runtime modules.

### W3: Implement pluggable output format system

Add support for custom output format plugins beyond IIFE/CJS/ESM: AMD, SystemJS, and custom wrappers. Changes span `internal/config/config.go` for format registration, `internal/js_printer/js_printer.go` for format-specific printers, `internal/linker/linker.go` for format-aware linking, `internal/js_parser/js_parser.go` for format-specific transforms, `internal/runtime/` for format runtimes, and `pkg/api/api.go` for the API.

### W4: Add comprehensive bundle analysis and visualization

Implement deep bundle analysis: dependency graph visualization, duplicate module detection, dynamic import boundaries, circular dependency reporting, and tree shaking effectiveness metrics. Changes span `internal/graph/graph.go` for analysis, `internal/linker/linker.go` for size attribution, `internal/bundler/bundler.go` for dependency data, `internal/resolver/resolver.go` for duplicate detection, `pkg/api/api.go` for analysis API, and add visualization output modules.

### W5: Implement multi-target build with shared compilation

Add `--target-matrix` that builds for multiple targets (e.g., ES2020+Chrome, ES2018+Node) in a single pass, sharing parsing and only diverging at the lowering/printing stage. Changes span `internal/config/config.go` for matrix config, `internal/bundler/bundler.go` for shared parsing, `internal/js_parser/js_parser_lower.go` for per-target lowering, `internal/linker/linker.go` for multi-output linking, `internal/compat/` for target resolution, and `pkg/api/api.go` for the API.

### W6: Add WebAssembly module bundling support

Implement WASM as a first-class module type: parse WASM imports/exports, resolve WASM-to-JS and JS-to-WASM boundaries, and generate appropriate loading code per platform. Changes span `internal/config/config.go` for WASM loader, `internal/bundler/bundler.go` for WASM parsing, `internal/resolver/resolver.go` for WASM resolution, `internal/linker/linker.go` for WASM chunk handling, `internal/js_printer/js_printer.go` for WASM loading code, and add WASM parser/AST modules.

### W7: Implement build pipeline with transform stages

Add a configurable pipeline of transform stages that run between parsing and printing: dead code injection, polyfill insertion, auto-import, and custom AST transforms. Changes span `internal/bundler/bundler.go` for pipeline orchestration, `internal/js_parser/js_parser.go` for transform hooks, `internal/js_ast/` for AST mutation API, `internal/linker/linker.go` for stage ordering, `internal/config/config.go` for pipeline config, and `pkg/api/api.go` for the plugin API.

### W8: Add build reproducibility verification and hermetic builds

Implement deterministic build guarantees: content-addressable outputs, build input fingerprinting, reproducibility verification across platforms, and hermetic mode that disallows filesystem access outside declared inputs. Changes span `internal/fs/` for hermetic filesystem, `internal/bundler/bundler.go` for input tracking, `internal/linker/linker.go` for deterministic ordering, `internal/resolver/resolver.go` for restricted resolution, `internal/cache/` for content addressing, and `pkg/api/api.go`.

### W9: Implement CSS-in-JS extraction and optimization

Add support for extracting CSS from tagged template literals (`css\`...\``) and `styled()` calls into optimized CSS files with dead code elimination. Changes span `internal/js_parser/js_parser.go` for CSS-in-JS detection, `internal/css_parser/css_parser.go` for extracted CSS parsing, `internal/linker/linker.go` for CSS chunk coordination, `internal/bundler/bundler.go` for extraction orchestration, `internal/js_printer/js_printer.go` for class name injection, and `internal/graph/graph.go` for JS-CSS dependencies.

### W10: Add monorepo-aware multi-package bundling

Implement workspace-aware bundling that understands monorepo package boundaries, deduplicates shared dependencies, and generates per-package bundles with cross-package imports. Changes span `internal/resolver/resolver.go` for workspace resolution, `internal/bundler/bundler.go` for multi-package orchestration, `internal/linker/linker.go` for cross-package linking, `internal/graph/graph.go` for package boundary tracking, `internal/config/config.go` for workspace config, and `pkg/api/api.go` for the API.

### W11: Overhaul release process and documentation for multi-target builds

Update `.github/workflows/publish.yml` for multi-target artifact publishing across platform/architecture combinations. Update `Makefile` with multi-target build rules and validation targets. Update `docs/development.md` with a multi-target development and testing guide. Add architecture diagrams to `images/` illustrating the shared-parse divergent-lowering pipeline. Update `CHANGELOG.md` with multi-target feature entries, update `version.txt` versioning strategy, and update `README.md` with new CLI flags and examples.
