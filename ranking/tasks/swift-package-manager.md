# Tasks — swiftlang/swift-package-manager

10 tasks (3 narrow, 4 medium, 3 wide) for the Swift build system and package manager.

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

### W3: Implement binary dependency framework

Add support for pre-built binary dependencies as an alternative to
source dependencies. Support XCFramework bundles, Swift module
interfaces (`.swiftinterface`), and C module maps. Implement a
binary artifact server protocol where packages can declare binary
targets hosted on a remote server. Support binary compatibility
checks (Swift version, ABI stability). Cache downloaded binaries
across projects on disk.
