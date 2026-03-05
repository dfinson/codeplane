# composer/composer

| Field | Value |
|-------|-------|
| **URL** | https://github.com/composer/composer |
| **License** | MIT |
| **Language** | PHP |
| **Scale** | Medium (multi-module project) |
| **Category** | Dependency manager |

## Why this repo

- **Multi-module with clear boundaries**: Dependency resolution (SAT solver),
  package repository sources (Packagist, VCS, path), autoload generation,
  installer plugins, lock file management, script execution, platform
  requirements — each a distinct subsystem requiring navigation between them.
- **Well-structured**: Source under `src/Composer/` with clear namespace
  split. Dependency solver, repository layer, installer, downloader, and
  autoload generator are separated by concern.
- **Rich history**: 10K+ commits, foundational to the PHP ecosystem. Every
  PHP project uses it. PRs cover dependency resolution edge cases, repository
  source handling, and performance improvements.
- **Permissive**: MIT license.

## Structure overview

```
src/Composer/
├── Composer.php              # Main Composer class
├── Factory.php               # Factory for creating Composer instances
├── Command/                  # CLI commands (require, install, update, etc.)
├── DependencyResolver/       # SAT-based dependency resolution
│   ├── Solver.php            # Constraint solver
│   ├── Pool.php              # Package pool
│   ├── Request.php           # Resolution request
│   └── Rule.php              # Solver rules
├── Repository/               # Package sources
│   ├── ComposerRepository.php    # Packagist/Composer repos
│   ├── VcsRepository.php         # Git/SVN/Hg repos
│   ├── PathRepository.php        # Local path repos
│   └── RepositoryManager.php     # Repository orchestration
├── Installer/                # Package installation
├── Downloader/               # Package downloading (zip, git, etc.)
├── Autoload/                 # Autoload file generation
│   ├── AutoloadGenerator.php
│   └── ClassMapGenerator.php
├── Package/                  # Package model
├── Plugin/                   # Plugin system
└── Script/                   # Script execution
```

## Scale indicators

- ~200 PHP source files
- ~50K lines of code
- 2-3 levels of namespace nesting
- Clear subsystem boundaries

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `composer update --minimal-changes` removing unrelated packages

When running `composer update --minimal-changes pkg/specific`, unrelated
packages that have newer versions available are sometimes downgraded
to their minimum required version. The solver treats the `--minimal-changes`
flag as "minimize all changes" rather than "update only the specified
packages." Fix the solver to lock non-targeted packages at their
current versions.

### N2: Add `--sort` option to `composer show` command

The `composer show` command lists packages but provides no sorting
options. Add `--sort=name` (default, current behavior), `--sort=size`
(by installed size), and `--sort=date` (by last update date) options.
Include a `--reverse` flag for descending order.

### N3: Fix `platform-check` false positive for replaced packages

When a package provides a platform extension replacement via
`"replace": {"ext-json": "*"}`, the platform check at autoload time
still warns that `ext-json` is missing. The platform checker does not
consult the `replace` field. Fix the checker to treat replaced
extensions as available.

### N4: Fix `VcsRepository` failing to detect tags with slashes in names

When a Git repository uses tags containing slashes (e.g., `release/v2.1.0`),
`VcsRepository` fails to parse them as valid version references. The tag
listing code in the Git driver splits on `/` expecting only a refs/tags
prefix and discards everything after the first slash in the tag name itself.
Fix the Git driver's tag parser to preserve slashes within tag names and
map them to valid Composer versions.

### N5: Fix `AutoloadGenerator` producing duplicate classmap entries

When a package defines both a `classmap` and a `psr-4` entry that overlap
on the same directory, `AutoloadGenerator` emits duplicate entries in the
generated `autoload_classmap.php`. The class scanner in `ClassMapGenerator`
does not deduplicate against PSR-4 discovered classes. Fix the generator
to detect overlapping autoload rules and emit each class exactly once,
preferring the PSR-4 entry.

### N6: Fix `PathRepository` symlink mode ignoring nested composer.json

When using a path repository with `"symlink": true` pointing at a directory
that contains subdirectories with their own `composer.json` files,
`PathRepository` only reads the top-level `composer.json` and silently
ignores nested packages. Fix `PathRepository` to correctly scope its
package discovery to only the target directory without descending into
subdirectories that are separate packages.

### N7: Add `--patch-only` flag to `composer update`

The `update` command currently accepts `--with-dependencies` and
`--no-dev` but provides no way to restrict updates to patch-level
version bumps only. Add a `--patch-only` flag that constrains the solver
to select versions within the same minor version as the currently locked
version for each targeted package. Emit a warning if a package has no
newer patch version available.

### N8: Fix `Factory::createComposer` leaking file handles on invalid JSON

When `Factory::createComposer` encounters a malformed `composer.json`,
it throws a `JsonValidationException` but does not close the file stream
opened to read the file. On systems processing many repositories in a
loop (e.g., Satis), this causes file descriptor exhaustion. Fix the
factory to use a try/finally block ensuring the stream is closed before
the exception propagates.

### N9: Fix `Pool` ignoring stability flags for transitively required packages

When a root `composer.json` sets `"minimum-stability": "stable"` but
adds `"prefer-stable": true` with an explicit `@beta` flag on a direct
dependency, transitive dependencies of that package do not inherit the
relaxed stability. The `Pool` filters out beta versions of transitive
packages even when they are the only versions satisfying the constraint.
Fix `Pool::addPackage` to propagate stability flags through the
dependency chain.

### N10: Fix `composer require` not updating the lock file hash

After `composer require` adds a new package, it updates `composer.json`
and `composer.lock` but does not recalculate the `content-hash` field
in the lock file. A subsequent `composer install` on a clean checkout
emits a "lock file out of date" warning even though the lock is correct.
Fix the require command to recalculate and write the content hash after
modifying the lock file.

## Medium

### M1: Implement parallel package downloads

Add parallel downloading of package archives. When `composer install`
determines the set of packages to download, spawn multiple concurrent
download workers (default 4, configurable via `--concurrency=N`).
Show a combined progress bar with per-package status. Handle partial
failures (retry individual downloads) without aborting the entire
operation. Support parallel git clones for source installs.

### M2: Add security vulnerability scanning

Implement `composer audit` that checks installed packages against a
vulnerability database (Packagist security advisories). Report CVE
IDs, severity, affected version ranges, and fixed versions. Support
`--format=json` for CI integration. Add `--ignore=CVE-2024-XXXX` for
acknowledged vulnerabilities. Run automatically after `composer install`
with a configurable severity threshold.

### M3: Implement workspace/monorepo support

Add `composer.json` workspace support for monorepos containing multiple
packages. A root workspace config lists member packages by directory.
Cross-references between workspace packages use local paths
automatically. `composer install` in the root resolves dependencies
for all workspace members together, deduplicating shared dependencies.
Add `--workspace-filter=pkg/*` to operate on a subset.

### M4: Implement a `composer outdated --direct` summary report

Add a `--direct` flag to `composer outdated` that limits output to
direct dependencies only and displays a summary table showing package
name, current version, latest version, semver-level of the update
(major/minor/patch), and a link to the changelog if available from the
repository metadata. Support `--format=json` and `--format=markdown`
output. Sort by semver-level (majors first) by default.

### M5: Add configurable retry and mirror fallback to Downloader

The download layer currently fails after a single HTTP timeout or 5xx
error. Implement configurable retry logic in the Downloader subsystem:
exponential backoff with jitter (default 3 retries), automatic fallback
to alternative mirror URLs listed in the repository metadata, and a
circuit-breaker that skips a mirror after repeated failures within a
time window. Add `--retry-count` and `--mirror-timeout` CLI options and
corresponding `config` keys.

### M6: Implement `composer doctor` for environment diagnostics

Add a `composer doctor` command that performs a comprehensive check of
the local environment. Verify PHP version and loaded extensions against
`require` and platform requirements, check that git/svn/hg binaries are
available and functional, validate `composer.json` schema and
`composer.lock` consistency, detect conflicting global plugins, test
connectivity to configured repositories, and report disk space in the
cache directory. Output a pass/warning/fail summary per check.

### M7: Add partial lock file update for `composer update` with package targets

When running `composer update vendor/package`, Composer currently
re-resolves the entire dependency graph and rewrites the full lock file.
Implement partial lock file updates where only the targeted packages and
their transitive dependents are re-resolved. Preserve the resolution
result for untouched packages. Add a `--dry-run` comparison showing
which lock entries would change. The solver must detect when a partial
update is impossible due to constraint conflicts and fall back to a full
resolution with a diagnostic message.

### M8: Implement autoload generation caching

The `AutoloadGenerator` rebuilds classmap and PSR-4 autoload files from
scratch on every `composer dump-autoload`. Implement a cache layer that
stores the previously generated autoload state and only regenerates
entries for packages whose installed version or autoload configuration
changed since the last dump. Add a cache manifest file alongside the
generated autoloader. Support `--force` to bypass the cache. Measure
and log time savings for large dependency trees.

### M9: Add `composer exec` with proper PATH and environment setup

Implement a `composer exec` command that runs a binary from
`vendor/bin/` with an automatically configured environment. Set `PATH`
to include `vendor/bin`, configure `COMPOSER_*` environment variables,
set the working directory to the project root, and pass through stdin
and exit codes. Support running any binary installed by any dependency,
show a helpful error when a binary is not found listing installable
packages that provide it, and add a `--list` flag that shows all
available binaries with their providing package.

### M10: Implement `composer why-not` with version constraint analysis

Extend the `composer why-not` command to provide detailed constraint
analysis when a package version cannot be installed. Walk the solver's
rule set to identify every constraint that blocks the requested version,
including transitive constraints from indirect dependencies. Display
a tree of conflicting requirements with the exact version ranges each
constraint allows. Suggest the minimum set of packages that need to be
updated to unblock the requested version. Support `--format=json` for
programmatic consumption.

## Wide

### W1: Add plugin sandboxing for security

Implement a security sandbox for Composer plugins. Plugins currently
have full access to the filesystem and network. Add a permission system
where plugins declare required capabilities (filesystem read/write paths,
network hosts, command execution) and the user approves them on first
use. Store approved permissions in a lock file. Block undeclared
operations at runtime. Add `composer plugin:audit` to review plugin
permissions.

### W2: Implement dependency resolution visualization and debugging

Add rich dependency resolution diagnostics: visual dependency tree
(`composer why-tree pkg/name`), conflict explanation with the full
resolution trace (`composer why-not pkg/name:2.0` with step-by-step
solver reasoning), resolution time profiling (which constraints took
the longest to satisfy), and a browser-based interactive dependency
graph that shows version constraints, conflicts, and resolution paths.

### W3: Implement lock file conflict auto-resolution for merge workflows

When merging branches that both modified `composer.lock`, Git leaves
conflict markers that require manual resolution. Implement a `composer
lock:resolve` command that parses the conflicted lock file, extracts both
sides' package lists, re-resolves the combined dependency set against
the merged `composer.json`, and writes a clean lock file. Handle cases
where both branches added conflicting versions of the same package,
where one branch removed a package the other updated, and where platform
requirements diverged. Integrate with the solver to detect unresolvable
conflicts and report them clearly.

### W4: Add dependency license compliance checking

Implement a `composer license:check` command that validates all installed
packages against a configurable license policy. Define allowed and
forbidden license identifiers in `composer.json` under an `extra` key.
Walk the full dependency tree including transitive dependencies, extract
SPDX license identifiers from each package's metadata, flag unknown or
ambiguous licenses, support SPDX compound expressions (AND, OR, WITH),
and generate a compliance report in text, JSON, and HTML formats. Add a
`--fail-on=deny` flag for CI pipelines that exits non-zero on policy
violations.

### W5: Implement repository priority and constraint routing

Add a repository priority system where each configured repository has
a numeric priority and optional package pattern filters. When resolving
dependencies, the `RepositoryManager` should prefer higher-priority
repositories, only falling back to lower-priority ones if a package
is not found or the version constraint cannot be satisfied. Support
per-package repository pinning in `composer.json` to force a specific
package to come from a specific repository. Handle version conflicts
across repositories by reporting which repository provided each
candidate version, and integrate priority into the solver's rule
generation in `DependencyResolver`.

### W6: Add `composer ci` meta-command for continuous integration workflows

Implement a `composer ci` command that orchestrates a full CI pipeline:
validate `composer.json` schema, verify `composer.lock` is up-to-date,
install dependencies with `--no-interaction`, run `composer audit` for
security advisories, execute configured test scripts, generate a
classmap-authoritative autoloader, and produce a summary report. Each
step should be independently skippable via `--skip=audit,test`. Support
`--fail-fast` to stop on first failure. Emit structured JSON output
suitable for CI systems. Add timing information for each step and an
overall pass/fail status code.

### W7: Implement cross-version dependency testing

Add a `composer test:matrix` command that tests the project against
multiple dependency version combinations. Parse each dependency's
version constraint, enumerate the lowest, highest, and a configurable
set of intermediate versions, generate a test matrix, and run the
configured test script against each combination. Parallelize test runs
up to a configurable concurrency limit. Use the solver to verify each
combination is installable before testing. Report a compatibility matrix
showing which version combinations pass or fail, including the specific
failure output for failed combinations.

### W8: Implement a plugin capability and lifecycle system

Redesign the plugin system to support a formal capability model and
lifecycle hooks. Define a `PluginCapability` interface that plugins
implement to declare the events they handle (pre/post install, resolve,
download, autoload, script execution). Add plugin ordering and priority
to control execution sequence when multiple plugins handle the same
event. Implement a plugin lifecycle with `activate`, `deactivate`, and
`uninstall` phases so plugins can perform cleanup. Add a `composer
plugin:list` command showing installed plugins, their capabilities,
and execution order. Support lazy plugin loading so plugins are only
instantiated when their declared events fire.

### W9: Implement offline mode with pre-populated cache

Add comprehensive offline mode support to Composer. When online,
`composer cache:prepare` downloads and caches all metadata and archives
for the current dependency tree plus a configurable depth of version
alternatives. When offline, Composer falls back to the local cache for
all repository metadata, package downloads, and VCS operations. Track
cache freshness per-package and warn when cached data exceeds a
configurable age. Support exporting and importing the cache as a
portable archive for air-gapped environments. Modify the
`RepositoryManager`, `Downloader`, and `Factory` to transparently
check cache availability before network access.

### W10: Add structured logging and telemetry across all subsystems

Implement a structured logging framework that replaces ad-hoc output
across all Composer subsystems. Define log channels for resolver,
installer, downloader, autoloader, plugin, and repository operations.
Each log entry includes a timestamp, channel, severity, structured
context data, and a correlation ID that ties together all operations
within a single command invocation. Support configurable log sinks
(stderr, file, JSON-lines) via `composer.json` config. Add
`--log-level` and `--log-file` CLI options. Implement performance
telemetry that records timing for dependency resolution, downloads,
and autoload generation, and optionally reports anonymized telemetry
to a configurable endpoint.
