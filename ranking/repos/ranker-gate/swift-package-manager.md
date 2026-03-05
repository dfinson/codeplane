# apple/swift-package-manager

| Field | Value |
|-------|-------|
| **URL** | https://github.com/swiftlang/swift-package-manager |
| **License** | Apache-2.0 |
| **Language** | Swift |
| **Scale** | Large (multi-team project) |
| **Category** | Build system and package manager |

## Why this repo

- **No single developer knows it all**: Package manifest parsing (Package.swift
  DSL), dependency resolution (pubgrub algorithm), build system (llbuild
  integration), workspace management, registry client, plugin system, test
  runner integration, cross-compilation, binary targets — each a substantial
  subsystem with deep internals.
- **Well-structured**: Multiple Swift packages in the workspace (`PackageModel`,
  `PackageGraph`, `PackageLoading`, `Build`, `Workspace`, `SourceControl`,
  `PackageRegistry`). Each package has clear responsibility boundaries.
- **Rich history**: 8K+ commits, maintained by Apple/Swift core team. Dense
  PR history covering dependency resolution, build planning, registry support,
  and platform-specific behavior.
- **Permissive**: Apache-2.0.

## Structure overview

```
Sources/
├── PackageModel/            # Package manifest data model
│   ├── Package.swift        # Package type
│   ├── Target.swift         # Target definitions
│   ├── Product.swift        # Product definitions
│   └── Manifest/            # Manifest loading and parsing
├── PackageGraph/            # Dependency graph
│   ├── Resolution/          # pubgrub dependency resolver
│   └── ModulesGraph.swift   # Resolved module graph
├── PackageLoading/          # Package.swift evaluation
├── Workspace/               # Workspace management
│   ├── Workspace.swift      # Main workspace type
│   ├── ResolverPrecomputationProvider.swift
│   └── ManagedDependency.swift
├── Build/                   # Build planning and execution
│   ├── BuildPlan/           # Build plan generation
│   ├── BuildOperation.swift # llbuild integration
│   └── SwiftCompilerOutputParser.swift
├── SourceControl/           # Git operations
├── PackageRegistry/         # Swift package registry client
├── PackagePlugin/           # Plugin infrastructure
└── Commands/                # CLI commands (build, test, run, resolve)
```

## Scale indicators

- ~500 Swift source files
- ~150K+ lines of code
- Deep module hierarchies (3-5 levels)
- Cross-cutting workspace, resolution, and build concerns

## Notes

- Repo recently moved from `apple/swift-package-manager` to
  `swiftlang/swift-package-manager`. Use the current canonical URL.

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `Package.resolved` not updating for transitive dependency changes

When a transitive dependency releases a new patch version that satisfies
the version requirement, `swift package update` updates the built package
but does not update `Package.resolved`. The resolved file retains the
old version, causing CI builds to use stale versions. Fix the update
command to re-resolve and write the updated pins.

### N2: Add `--platform` filter to `swift package show-dependencies`

The `show-dependencies` command lists all dependencies regardless of
platform availability. Add a `--platform ios|macos|linux|windows` flag
that filters dependencies to only show those that declare support for
the specified platform. Respect platform conditions in package manifests.

### N3: Fix `swift test --filter` not matching parameterized test names

When using Swift Testing's parameterized tests (via `@Test(arguments:)`),
the `--filter` option cannot match individual parameterized test cases
because their names include special characters (commas, parentheses)
that the filter regex doesn't expect. Fix the filter matching to
properly handle parameterized test case names.

### N4: Fix `swift build` not detecting changes in C header files

When a C header file included via a C target is modified, `swift build`
does not trigger recompilation of the Swift targets that import it.
The dependency tracker does not scan C header include graphs. Fix the
incremental build to track C header dependencies.

### N5: Fix `Package.resolved` version pinning lost after `swift package reset`

Running `swift package reset` clears the build directory but also
corrupts the pinning entries in `Package.resolved`. Some resolved
versions revert to older pins. Fix `reset` to only clear build
artifacts, not resolution state.

### N6: Add `--verbose` flag to `swift package show-dependencies`

The `show-dependencies` command shows the dependency tree but not
version constraints or resolution details. Add a `--verbose` flag
that shows the version constraint from Package.swift alongside each
resolved version.

### N7: Fix `swift build` not reporting which target caused a linker error

When a linker error occurs during `swift build`, the error output
shows raw linker diagnostics (undefined symbols, duplicate symbols)
without indicating which target or product triggered the link step.
The `SwiftCompilerOutputParser` in
`Sources/Build/SwiftCompilerOutputParser.swift` parses compiler
diagnostics but the linker output path in `BuildOperation` does not
map linker invocations back to their target. Add target attribution
to linker error messages so developers know which target to fix.

### N8: Fix `@testable import` failing for mixed Swift/C targets

When a target contains both Swift and C sources, `@testable import`
in test targets fails with "module was not compiled for testing."
The build system applies `-enable-testing` to Swift sources but not
to the module map configuration. Fix the build plan generator.

### N9: Add `swift package dump-manifest` for CI introspection

Add a command that outputs the parsed Package.swift manifest as
structured JSON without resolving dependencies or building. Useful
for CI pipelines that need to inspect package metadata (name, products,
targets, minimum platform versions) without a full resolve.

### N10: Fix conditional target dependencies ignored for non-primary platform

When Package.swift uses `.when(platforms: [.linux])` on a target
dependency, the dependency is ignored even when building for Linux.
The platform condition evaluator doesn't match the target triple
to the platform enum correctly. Fix platform condition evaluation.

## Medium

### M1: Implement dependency vulnerability scanning

Add `swift package audit` that checks resolved dependencies against a
vulnerability database. Query the Swift Package Index's security
advisories API. Report affected packages, severity, CVE IDs, and
fixed versions. Support `--severity=high` threshold filtering and
`--ignore=CVE-2024-XXXX` for acknowledged issues. Return non-zero
exit code for CI integration.

### M2: Add build caching with remote cache support

Implement a build artifact cache that stores compilation results
keyed by module name + source hash + compiler flags. Support local
disk cache (default) and remote cache (HTTP GET/PUT to a configurable
server). On cache hit, skip compilation for the module and use cached
artifacts. Add `swift build --cache-stats` for hit/miss reporting.
Handle incremental builds correctly when cache is partially populated.

### M3: Implement conditional dependencies with platform expressions

Add support for declaring dependencies that are only required on
specific platforms: `.package(url: "...", from: "1.0", when: .platforms([.linux]))`. Currently platform-specific dependencies must be
declared unconditionally and guarded only in target dependencies.
The resolver should exclude platform-irrelevant dependencies from
the resolution graph when the target platform is known.

### M4: Add workspace support for multi-package repositories

Implement workspace-style builds where a root `Package.swift` declares
a workspace of multiple local packages. All packages in the workspace
share a single dependency resolution graph. Cross-workspace dependencies
use local paths automatically. `swift build` at the root builds all
workspace members. Add `--workspace-filter` for building subsets.

### M5: Implement per-target build settings inheritance

Add the ability for targets to inherit build settings from a shared
configuration: `target.inherit(settings: .sharedDebug)`. Currently
each target must repeat swift compiler flags, linker flags, and
define macros. Support inheritance chains and per-target overrides.

### M6: Add test coverage delta reporting

Implement `swift test --coverage-delta=main` that shows only coverage
changes compared to a base branch. Output which lines gained or lost
coverage. Support threshold-based CI checks (fail if coverage drops
by more than N%). Include per-target coverage delta breakdown.

### M7: Implement dependency pinning with version ranges

Add `swift package pin <package> --to ">=1.0, <2.0"` that pins a
dependency to a version range rather than an exact version. The
resolver should find the best version within the pinned range.
Currently only exact version pinning is supported.

### M8: Add build configuration profiles

Implement named build configuration profiles (`.debug`, `.release`,
`.profile`, custom) that bundle compiler flags, optimization levels,
and defines. Support profile selection via `swift build --profile perf`.
Store profiles in Package.swift with a builder API.

### M9: Implement target-level resource access code generation

Add automatic code generation for type-safe resource access from
resource bundles. For each resource file, generate a Swift accessor
(`Resources.myImage` returns `Data`, `Resources.myStrings` returns
localized strings). Run code generation during `swift build`.

### M10: Add package dependency health check

Implement `swift package health` that reports: outdated dependencies
(newer versions available), deprecated packages (marked in Swift
Package Index), unpinned dependencies (version range too wide),
unused dependencies (declared but not imported), and circular
dependency risks. Include machine-readable JSON output.

## Wide

### W1: Implement resource bundle optimization

Overhaul the resource bundle system. Add: asset catalog compilation
(`.xcassets` → optimized binary), image resizing for multiple device
scales, dead resource detection (resources in the bundle that no code
references), resource access code generation (type-safe accessors), and
resource bundle size reporting. Support conditional resources that are
included only for specific platforms.

### W2: Add integrated code coverage reporting

Implement `swift test --coverage` that produces code coverage reports
without requiring external tools. Instrument builds with LLVM coverage,
merge coverage data from parallel test runs, generate reports in
multiple formats (text summary, JSON, HTML, lcov), support coverage
thresholds (fail if coverage drops below N%), and support coverage
diff (show only coverage changes in a PR). Integrate with
`Package.swift` for per-target coverage exclusions.

### W3: Implement dependency license auditing and compliance

Add `swift package audit-licenses` that scans all resolved
dependencies, detects their licenses (from `LICENSE`, `COPYING`,
`Package.swift` metadata), and reports compliance against a
configurable policy. Support allow-lists and deny-lists of SPDX
license identifiers. Flag packages with missing or unrecognizable
licenses. Support `--format json|text|sarif` output for CI
integration. Generate a combined license attribution file suitable
for app distribution. Changes span `Sources/Commands/` for the
new subcommand, `Sources/PackageLoading/` for license detection,
and `Sources/Workspace/` for dependency metadata access.

### W4: Implement distributed build with remote caching

Add `swift build --remote-cache=server` that pushes and pulls
compiled modules from a shared cache. Cache keys include source hash,
compiler version, and build flags. Support cache warm on CI. Handle
incremental builds correctly with partial cache hits. Changes span
the build planner, compilation pipeline, cache protocol, and CLI.

### W5: Add parallel test execution with resource isolation

Implement `swift test --parallel` that runs test targets
concurrently and distributes individual test cases across
worker processes. Provide per-worker temporary directories to
isolate file system side effects. Merge test results from all
workers into a unified report. Support `--parallel-workers N`
for configurable parallelism. Handle test target dependencies
that require serial execution via annotation. Changes span
`Sources/Commands/SwiftTestCommand.swift` for CLI flags,
`Sources/Commands/Utilities/TestingSupport.swift` for worker
orchestration, and `Sources/Build/` for test product discovery.
Add JUnit XML output merging for CI systems.

### W6: Implement build trace profiling and bottleneck analysis

Add `swift build --trace` that records a detailed build timeline
tracing every compilation, linking, and resource processing step
with wall-clock timing and dependency relationships. Output a
Chrome Trace Event format (JSON) file viewable in
`chrome://tracing` or Perfetto. Identify the critical path and
report the longest sequential chain. Detect build bottlenecks:
targets that block parallelism, unnecessarily broad dependencies,
and slow individual compilations. Changes span
`Sources/Build/LLBuildProgressTracker.swift` for event capture,
`Sources/Build/BuildOperation.swift` for dependency tracking,
`Sources/Commands/SwiftBuildCommand.swift` for the CLI flag, and
a new trace serialization module.

### W7: Add visual dependency graph and build timeline

Implement `swift package graph` that generates an interactive
dependency visualization: dependency DAG with version labels,
security advisory overlay, update availability indicators, and build
order. Add `swift build --timeline` that produces a build timeline
showing parallel compilation, linking, and resource processing with
per-target timing. Changes span graph generation, build event
collection, and add visualization output.

### W8: Implement unused dependency detection and cleanup

Add `swift package clean-deps` that analyzes import statements
across all targets and cross-references them with declared
dependencies in Package.swift. Report dependencies that are
declared but never imported (unused) and imports that reference
modules not declared as dependencies (missing). Support
`--auto-fix` to remove unused entries from Package.swift via
`Sources/PackageModel/ManifestSourceGeneration.swift`. Handle
conditional imports (`#if canImport`), re-exports, and
`@_implementationOnly` imports. Changes span
`Sources/Commands/` for the new subcommand,
`Sources/Basics/ImportScanning.swift` for import analysis, and
`Sources/PackageGraph/` for dependency graph querying.

### W9: Add integrated benchmarking framework

Implement `swift package benchmark` with: benchmark discovery
(annotated functions), warm-up and measurement iterations, statistical
analysis (mean, median, stddev, percentiles), regression detection
against baselines, and result storage. Support `@Benchmark` macro
for annotating functions. Changes span the package model (benchmark
target type), build system, test runner, statistics engine, and CLI.

### W10: Implement dependency update impact analysis

Add `swift package update --dry-run --impact` that simulates a
dependency update without modifying `Package.resolved` and reports:
which packages would change versions, API differences between
current and new versions (added/removed/changed public symbols),
potential breaking changes detected via Swift module interface
comparison, and estimated rebuild scope (which targets would need
recompilation). Changes span `Sources/Commands/PackageCommands/Update.swift`
for the CLI, `Sources/Workspace/Workspace+Dependencies.swift` for
dry-run resolution, `Sources/Commands/Utilities/APIDigester.swift`
for symbol diffing, and `Sources/Build/BuildPlan/` for rebuild
scope estimation.
