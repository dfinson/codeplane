# vitest-dev/vitest

| Field | Value |
|-------|-------|
| **URL** | https://github.com/vitest-dev/vitest |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Large |
| **Category** | Test framework |
| **Set** | eval |
| **Commit** | `e06f175cba08346bf0382c0b3e137a822bced280` |

## Why this repo

- **Multi-package monorepo**: Core runner, snapshot engine, expect/assertions, mocker, coverage providers, browser testing, UI dashboard, spy utilities
- **Well-structured**: Clear package separation under `packages/` with distinct concerns per package
- **Rich history**: 10K+ commits, Vite-native test framework with growing adoption

## Structure overview

```
packages/
├── vitest/src/              # Core test framework
│   ├── node/                # Node-side: core orchestrator, config, CLI, pools, reporters, watcher
│   ├── runtime/             # Test runtime: module runner, workers, VM isolation
│   ├── integrations/        # chai, mock, snapshot, coverage, environment adapters
│   ├── api/                 # WebSocket API for UI communication
│   ├── typecheck/           # Type-checking mode
│   └── utils/               # Shared utilities
├── runner/src/              # Test runner: suite collection, hook execution, fixture injection
├── expect/src/              # Assertion library: jest-expect, asymmetric matchers, custom matchers
├── snapshot/src/            # Snapshot testing: inline snapshots, file snapshots, serialization
├── spy/src/                 # Spy/stub utilities (tinyspy wrapper)
├── mocker/src/              # Module mocking: auto-mocker, mock registry
├── coverage-v8/src/         # V8-based code coverage provider
├── coverage-istanbul/src/   # Istanbul-based code coverage provider
├── browser/src/             # Browser test environment orchestration
├── browser-preview/src/     # Browser preview UI for test execution
├── ui/                      # Dashboard UI (Vue-based)
├── utils/src/               # Shared utilities: error processing, diff, serialization
├── web-worker/src/          # Web Worker test support
└── ws-client/src/           # WebSocket client for IDE integrations
```

## Scale indicators

- ~1900 TypeScript source files across packages
- ~80K lines of source code
- Multiple test execution pools (threads, forks, VM isolation)
- Coverage, snapshot, browser, and mocking subsystems

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `test.each` not preserving test context when used with fixtures

When `test.each(cases)` is combined with fixture injection via `test.extend`, the fixtures are not injected into the parameterized test function. The `collect.ts` in `packages/runner/src/` doesn't propagate fixture definitions through `each` wrappers.

### N2: Fix snapshot update mode not clearing obsolete snapshots from inline snapshot files

When running with `--update`, removed inline snapshots in test files leave stale `toMatchInlineSnapshot()` calls with old values. The `InlineSnapshotManager` in `packages/snapshot/src/port/inlineSnapshot.ts` detects unused file-based snapshots but skips inline ones.

### N3: Add `--fail-on-flaky` CLI flag to mark flaky tests as failures

When a test passes on retry after initially failing, it's reported as passed. Add a CLI flag that treats retried-then-passed tests as failures. Changes touch `packages/vitest/src/node/cli/` for the flag and `packages/runner/src/run.ts` for retry result handling.

### N4: Fix `vi.useFakeTimers()` not affecting `setTimeout` inside `Promise.then` chains

When fake timers are active, `setTimeout` calls inside resolved promise `.then()` callbacks use real timers because the microtask runs after the fake timer context is restored. The timer installation in `packages/vitest/src/integrations/vi.ts` doesn't intercept microtask-scheduled timers.

### N5: Fix coverage report showing 0% for files that only export types

When a TypeScript file exports only type definitions, V8 coverage reports it as 0% covered even though there's no executable code. The coverage provider in `packages/coverage-v8/src/provider.ts` should exclude type-only files from the coverage report.

### N6: Add `expect.soft()` assertion that collects failures without stopping the test

Currently `expect()` throws on first failure. Add `expect.soft()` that records failures and reports all of them at test end. Changes touch `packages/expect/src/jest-expect.ts` for the soft wrapper and `packages/runner/src/run.ts` for failure collection.

### N7: Fix `toMatchObject` not handling `Date` instances correctly in deep comparisons

When `expect(obj).toMatchObject({ date: new Date('2024-01-01') })` compares objects with `Date` fields, it uses reference equality instead of value equality. The comparison logic in `packages/expect/src/jest-utils.ts` doesn't special-case `Date` objects.

### N8: Fix test file watcher not detecting changes in symlinked dependencies

When test files import from symlinked local packages, changes to those packages don't trigger re-runs. The watcher in `packages/vitest/src/node/watcher.ts` resolves symlinks but doesn't add the resolved paths to the watch list.

### N9: Add `onTestFinished` hook for cleanup that runs after each test regardless of result

Tests need a way to register cleanup callbacks that run after the test completes (pass or fail), similar to Go's `t.Cleanup()`. Add `onTestFinished(callback)` to the test context in `packages/runner/src/context.ts` with execution in `packages/runner/src/run.ts`.

### N10: Fix `vi.mock()` hoisting not working with dynamic import expressions

When `vi.mock('./module')` is used alongside `const mod = await import('./module')`, the mock factory isn't applied because the hoisting transform in `packages/vitest/src/node/plugins/` doesn't intercept dynamic `import()` calls following `vi.mock()`.

## Medium

### M1: Implement test tagging and tag-based filtering

Add `test.tag('slow', 'integration')` and `--tags slow --exclude-tags integration` CLI filtering. Changes span `packages/runner/src/collect.ts` for tag collection, `packages/vitest/src/node/tags.ts` for filtering, `packages/vitest/src/node/cli/` for CLI options, and reporter output.

### M2: Add parallel suite execution within a single test file

Currently suites within a file run sequentially. Implement `describe.concurrent()` that runs child suites in parallel with configurable concurrency limits. Changes span `packages/runner/src/run.ts` for parallel suite orchestration, `packages/runner/src/collect.ts` for concurrent marking, and `packages/runner/src/fixture.ts` for fixture isolation.

### M3: Implement snapshot testing with custom serializers per test file

Add the ability to register custom snapshot serializers scoped to a test file via `vi.snapshotSerializer(serializer)`. Changes span `packages/snapshot/src/client.ts` for per-file serializer storage, `packages/snapshot/src/port/state.ts` for state tracking, and `packages/vitest/src/integrations/snapshot/` for the API.

### M4: Add test dependency declaration and ordering

Implement `test.dependsOn('other test name')` that ensures a test only runs after its dependency passes. Detect circular dependencies and skip dependents when a dependency fails. Changes span `packages/runner/src/collect.ts` for dependency graph, `packages/runner/src/run.ts` for ordered execution, and `packages/vitest/src/node/sequencers/`.

### M5: Implement automatic test retry with exponential backoff

Extend the retry mechanism to support `retry: { count: 3, delay: 1000, backoff: 'exponential' }`. Changes span `packages/runner/src/run.ts` for retry scheduling with delays, `packages/vitest/src/node/config/resolveConfig.ts` for config options, and type definitions.

### M6: Add module mock validation to detect unmocked imports

Implement `vi.mock('./module', { strict: true })` that throws when the test code accesses a property not explicitly defined in the mock factory. Changes span `packages/mocker/src/automocker.ts` for proxy-based strict mocking, `packages/mocker/src/registry.ts` for mock tracking, and `packages/vitest/src/integrations/mock/`.

### M7: Implement coverage diff reporting against a baseline

Add `--coverage-baseline coverage-baseline.json` that compares current coverage against a saved baseline and reports regressions. Changes span `packages/coverage-v8/src/provider.ts` and `packages/coverage-istanbul/src/` for baseline loading and comparison, and `packages/vitest/src/node/coverage.ts` for orchestration.

### M8: Add structured test metadata and custom properties

Implement `test.meta({ ticket: 'JIRA-123', owner: 'team-a' })` that attaches metadata to test results, available in reporters and the UI. Changes span `packages/runner/src/types.ts` for metadata types, `packages/runner/src/collect.ts`, `packages/vitest/src/node/reporters/` for output, and `packages/ui/` for display.

### M9: Implement global test setup/teardown with resource sharing

Add `globalSetup` files that can export resources accessible to all test files via `inject()`. Changes span `packages/vitest/src/node/globalSetup.ts` for resource lifecycle, `packages/vitest/src/integrations/inject.ts` for type-safe access, and `packages/vitest/src/node/pools/` for resource propagation across workers.

### M10: Add test file impact analysis using module graph

Implement `--changed` mode that uses the Vite module graph to determine which test files are affected by source changes, running only impacted tests. Changes span `packages/vitest/src/node/git.ts` for change detection, `packages/vitest/src/node/vite.ts` for module graph traversal, and `packages/vitest/src/node/specifications.ts` for test selection.

## Wide

### W1: Implement distributed test execution across multiple machines

Add a coordinator/worker architecture that splits test files across machines, collects results, and produces merged reports. Changes span `packages/vitest/src/node/core.ts` for coordination, `packages/vitest/src/node/pools/` for remote pool, `packages/runner/src/` for result serialization, `packages/vitest/src/node/reporters/` for merged output, `packages/vitest/src/api/` for network protocol, and add a distributed execution module.

### W2: Add visual regression testing support

Implement screenshot comparison for browser tests: capture, diff against baselines, threshold configuration, and visual diff output. Changes span `packages/browser/src/` for screenshot capture, `packages/snapshot/src/` for image comparison, `packages/vitest/src/node/reporters/` for visual diff reports, `packages/expect/src/` for `toMatchScreenshot()` matcher, `packages/ui/` for visual diff display, and add image processing utilities.

### W3: Implement test analytics and trend tracking

Add persistent test result storage with historical trend analysis: flaky test detection, duration trends, failure rate tracking, and test health dashboards. Changes span `packages/vitest/src/node/reporters/` for data collection, `packages/vitest/src/node/cache/` for storage, `packages/ui/` for dashboard views, `packages/vitest/src/node/core.ts` for aggregation, `packages/ws-client/src/` for live updates, and add analytics infrastructure.

### W4: Add contract testing framework for API boundary validation

Implement contract definition between provider and consumer test suites: schema validation, mock server generation, and contract verification. Changes span `packages/expect/src/` for contract matchers, `packages/runner/src/` for contract test type, `packages/vitest/src/node/` for contract resolution, `packages/mocker/src/` for mock server, `packages/vitest/src/node/reporters/` for contract reports, and add a contracts module.

### W5: Implement intelligent test prioritization with failure prediction

Add ML-based test ordering that predicts likely failures based on code changes, test history, and file proximity. Changes span `packages/vitest/src/node/sequencers/` for prediction-based sequencer, `packages/vitest/src/node/git.ts` for change analysis, `packages/vitest/src/node/cache/` for history storage, `packages/vitest/src/node/core.ts` for data collection, `packages/vitest/src/node/config/` for configuration, and add a prediction engine.

### W6: Add comprehensive test environment management

Implement declarative test environment configuration with automatic setup/teardown for Docker containers, databases, and external services. Changes span `packages/vitest/src/integrations/env/` for environment definitions, `packages/vitest/src/node/globalSetup.ts` for lifecycle, `packages/vitest/src/node/pools/` for environment isolation, `packages/runner/src/` for environment injection, `packages/vitest/src/node/config/` for declarations, and add environment provider modules.

### W7: Implement mutation testing to measure test effectiveness

Add a mutation testing mode that modifies source code and checks whether tests catch the mutations. Changes span `packages/vitest/src/node/core.ts` for mutation orchestration, `packages/vitest/src/node/vite.ts` for source mutation via Vite plugins, `packages/runner/src/` for mutation-aware execution, `packages/vitest/src/node/reporters/` for mutation reports, `packages/coverage-v8/src/` for correlation with coverage, and add a mutation engine.

### W8: Add IDE protocol server for deep editor integration

Implement a Language Server Protocol-like server for test discovery, execution, and debugging from editors. Changes span `packages/vitest/src/api/` for protocol definition, `packages/ws-client/src/` for client library, `packages/vitest/src/node/core.ts` for test lifecycle events, `packages/vitest/src/node/reporters/` for streaming results, `packages/runner/src/` for debug breakpoint support, and add protocol handler modules.

### W9: Implement snapshot migration framework for cross-version compatibility

Add tools to migrate snapshot files between serializer versions, handle format changes, and validate snapshot integrity. Changes span `packages/snapshot/src/client.ts` for versioned formats, `packages/snapshot/src/port/state.ts` for migration logic, `packages/snapshot/src/port/utils.ts` for format detection, `packages/vitest/src/node/cli/` for migration commands, `packages/snapshot/src/port/plugins.ts` for serializer versioning, and add migration utilities.

### W10: Add multi-project workspace test orchestration

Implement cross-project test execution for monorepos: shared configuration, project dependency ordering, cross-project coverage, and unified reporting. Changes span `packages/vitest/src/node/core.ts` for workspace coordination, `packages/vitest/src/node/projects/` for project resolution, `packages/vitest/src/node/config/` for workspace config, `packages/vitest/src/node/reporters/` for merged output, `packages/coverage-v8/src/` for cross-project coverage, and `packages/vitest/src/node/pools/` for project isolation.
