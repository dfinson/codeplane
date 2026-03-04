# Tasks — composer/composer

8 tasks (3 narrow, 3 medium, 2 wide) for the PHP dependency manager.

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
