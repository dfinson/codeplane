# xunit/xunit

| Field | Value |
|-------|-------|
| **URL** | https://github.com/xunit/xunit |
| **License** | Apache-2.0 |
| **Language** | C# |
| **Scale** | Large |
| **Category** | Test framework |
| **Set** | eval |
| **Commit** | `63aad206c62c2db373a9420486aa8ebc1a3daad9` |

## Why this repo

- **Multi-project solution**: Core framework (`xunit.v3.core`), assertions (`xunit.v3.assert`), runner infrastructure (`xunit.v3.runner.common`, `.utility`, `.console`, `.msbuild`), common abstractions (`xunit.v3.common`)
- **Deep pipeline architecture**: Discovery → filtering → ordering → execution → reporting, with extensibility at every stage
- **Backward compatibility**: Supports v1, v2, and v3 test assemblies through adapter layers and Microsoft Testing Platform integration

## Structure overview

```
src/
├── xunit.v3.core/               # Test framework core
│   ├── Runners/                 # Assembly/collection/class/method/test runners
│   ├── Framework/               # Discoverers, front controller, test framework
│   ├── Attributes/              # BeforeAfterTest, DataAttribute, MemberData
│   ├── ObjectModel/             # XunitTest, XunitTestCase, XunitTestAssembly
│   ├── Messages/                # Message types for pipeline communication
│   ├── Internal/                # MessageBus, TestContext, runner helpers
│   └── Abstractions/            # Interfaces for attributes, object model, runners
├── xunit.v3.assert/             # Assert methods (Equal, Contains, Throws, etc.)
├── xunit.v3.common/             # Shared types, serialization, utilities
│   ├── Options/                 # ExplicitOption, ParallelAlgorithm, TestMethodDisplay
│   ├── Serialization/           # JSON serialization for test messages
│   └── Utility/                 # DisposalTracker, UniqueIDGenerator, TypeHelper
├── xunit.v3.runner.common/      # Shared runner infrastructure
│   ├── Filtering/               # XunitFilters, query filter parser, trait filters
│   ├── Sinks/                   # ExecutionSink, TestDiscoverySink, event sinks
│   ├── Reporters/               # Registered runner reporters
│   ├── Transforms/              # Output transforms (XML, JSON, etc.)
│   └── Configuration/           # JSON config reader
├── xunit.v3.runner.console/     # Console runner executable
├── xunit.v3.runner.msbuild/     # MSBuild task runner
├── xunit.v3.runner.utility/     # Runner utility library
│   ├── SimpleRunner/            # Simplified AssemblyRunner API
│   ├── Frameworks/              # v1/v2/v3 framework adapters
│   └── Configuration/           # Configuration readers
└── xunit.v3.mtp-v1/v2/         # Microsoft Testing Platform adapters
```

## Scale indicators

- ~1025 C# source files
- ~142K lines of code
- Multi-framework backward compatibility (v1, v2, v3)
- Extensive runner pipeline with pluggable components

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `TestCollectionRunner` not respecting `MaxParallelThreads` when set to 1

When `MaxParallelThreads` is configured to 1 in the JSON config, `XunitTestCollectionRunner` still executes test classes concurrently within the collection. The `TestCollectionRunnerContext` passes the parallel limit to the scheduler but the base `TestCollectionRunner.Run` method doesn't enforce the constraint when dispatching class runners.

### N2: Fix `QueryFilterParser` not handling escaped quotes in trait value filters

In `src/xunit.v3.runner.common/Filtering/QueryFilterParser.cs`, filter expressions like `Trait("Category", "it's tricky")` fail to parse when the trait value contains single quotes. The parser splits on quote boundaries without handling escape sequences.

### N3: Add `TestMethodDisplay.ClassAndMethodWithParameters` option

The `TestMethodDisplay` enum in `xunit.v3.common/Options/` supports `ClassAndMethod` and `Method`. Add a `ClassAndMethodWithParameters` option that includes the serialized parameter values in the display name for `[Theory]` tests, making parameterized test names more distinctive in output.

### N4: Fix `UniqueIDGenerator` producing collisions for overloaded methods with different generic parameters

In `xunit.v3.common/Utility/UniqueIDGenerator.cs`, when two test methods have the same name but different generic type parameters (e.g., `TestMethod<int>` and `TestMethod<string>`), the generated unique IDs can collide because generic parameters are not included in the hash input.

### N5: Fix `ExecutionSink` not reporting `LongRunningTestSeconds` warning for async tests that await

In `src/xunit.v3.runner.common/Sinks/ExecutionSink.cs`, the long-running test detection timer starts when the test begins but is not properly tracked for async test methods. Tests that `await` long-running operations don't trigger the warning because the timer check races with async continuations.

### N6: Add `[RetryFact]` attribute for automatically retrying flaky tests

Add a `RetryFactAttribute` that retries a failing test up to a configurable number of times before reporting failure. Implement a corresponding `RetryFactDiscoverer` in the Framework directory and a `RetryTestCaseRunner` that wraps `XunitTestCaseRunner` with retry logic.

### N7: Fix `ConfigReader_Json` silently ignoring unknown configuration keys

In `src/xunit.v3.runner.common/Configuration/ConfigReader_Json.cs`, unknown keys in the JSON configuration file are silently ignored. A typo like `"maxParallelThread"` (missing 's') produces no warning, leading to confusing behavior where the setting appears ineffective.

### N8: Add support for `[MemberData]` sourcing from static properties on base classes

The `MemberDataAttributeBase` in `xunit.v3.core/Attributes/` resolves member data from the declaring class. When a `[Theory]` test in a derived class uses `[MemberData(nameof(TestData))]` referencing a static property on a base class, discovery fails with a member-not-found error because the lookup only checks the immediate declaring type.

### N9: Fix `DisposalTracker` not awaiting `IAsyncDisposable` objects in disposal order

In `xunit.v3.common/Utility/DisposalTracker.cs`, objects implementing `IAsyncDisposable` are disposed in FIFO order, but the disposals should be LIFO (reverse registration order) to match the expected disposal pattern. Additionally, async disposals are fire-and-forget instead of being awaited sequentially.

### N10: Add test attachment size limit to `ExecutionSinkOptions`

The `ExecutionSinkOptions` in `src/xunit.v3.runner.common/Sinks/` allows configuring various execution behaviors but has no limit on test attachment sizes. Add a `MaxAttachmentSizeBytes` option that truncates or rejects attachments exceeding the configured limit, preventing memory exhaustion from large test output attachments.

## Medium

### M1: Implement structured test output capture with named sections

Currently, `TestOutputHelper` in `xunit.v3.core/Framework/` captures all test output as a single string. Implement named output sections (e.g., `output.Section("Setup")`, `output.Section("Assertion")`) that partition captured output into labeled segments. Update the message pipeline, `TestOutput` message, and console/MSBuild reporters to display sectioned output.

### M2: Add test dependency declaration and execution ordering

Implement a `[DependsOn(typeof(OtherTest))]` attribute that declares execution dependencies between test classes. Add dependency resolution in `XunitTestCollectionRunner` that topologically sorts test classes, detects cycles, and skips dependent tests when prerequisites fail. Update `DefaultTestCaseOrderer` to respect dependency order.

### M3: Implement parallel test execution with resource locking

Add a `[RequiresResource("database")]` attribute and a resource lock manager in the runner pipeline. Test collections requiring the same resource are serialized, while tests needing different resources run in parallel. Implement the lock manager in `xunit.v3.core/Internal/`, update `TestCollectionRunner`, and integrate with the parallel scheduler.

### M4: Add real-time test execution progress API for IDE integration

Implement a communication channel in `InProcessFrontController` that streams test execution events to an external process via named pipes or Unix domain sockets. Add a `TestProgressReporter` sink that serializes `MessageSinkMessage` events in real-time. Update the message bus to support external subscribers alongside internal sinks.

### M5: Implement test data snapshot assertions

Add `Assert.MatchesSnapshot<T>(T actual, string snapshotName)` that compares objects against stored JSON snapshots. On first run, create the snapshot file. On subsequent runs, diff against the stored snapshot and report structural differences. Add snapshot update mode via configuration. Changes span `xunit.v3.assert/`, `xunit.v3.common/` for serialization helpers, and configuration.

### M6: Add test execution profiling with per-phase timing

Implement timing breakdown for each test execution phase: fixture construction, `BeforeAfterTest.Before`, test method execution, `BeforeAfterTest.After`, and fixture disposal. Add a `TestExecutionProfile` to the `TestPassed`/`TestFailed` messages. Update `XunitTestRunner` and `XunitTestCaseRunner` to capture phase timings, and update reporters to display profiling data.

### M7: Implement dynamic test generation via `IAsyncEnumerable` data sources

Extend the `[Theory]` infrastructure to support `IAsyncEnumerable<object[]>` as a data source. Add `[AsyncMemberData]` attribute, implement `AsyncTheoryDiscoverer` in the Framework directory, and update `XunitDelayEnumeratedTheoryTestCase` to handle async enumeration during execution. Support cancellation and timeout for async data generation.

### M8: Add test categorization and selective execution by category groups

Implement a category group system where traits can be grouped (e.g., `"Integration" = ["Database", "API", "Queue"]`). Add category group definitions to the JSON config, update `XunitFilters` and `QueryFilterParser` to resolve category groups, and implement group-aware filtering in the runner pipeline.

### M9: Implement flaky test detection and quarantine

Add automatic flaky test detection that tracks test result history across runs using a JSON database file. When a test alternates between pass/fail across N runs, mark it as flaky. Add a `[Quarantine]` auto-applied trait and a configuration option to skip or warn on quarantined tests. Changes span `ExecutionSink`, configuration, and filtering.

### M10: Add test matrix support for combinatorial `[Theory]` data

Implement `[MatrixData]` attribute that generates test cases from the Cartesian product of multiple data sources. `[MatrixData(nameof(Browsers), nameof(Resolutions))]` generates all combinations. Add `MatrixDataDiscoverer`, integrate with `TheoryDiscoverer`, and support filtering specific matrix combinations.

## Wide

### W1: Implement distributed test execution across multiple machines

Add a coordinator/worker architecture for distributing test execution across machines. The coordinator discovers tests, partitions them across workers, dispatches work via gRPC, collects results, and produces unified reports. Changes span runner infrastructure, message serialization, sink aggregation, and add `xunit.v3.runner.distributed/` project.

### W2: Add code coverage integration with per-test attribution

Implement coverage collection that attributes line coverage to individual tests. Integrate with System.Diagnostics.DiagnosticSource to track which lines execute during each test. Aggregate per-test coverage into a per-assembly report. Changes span the test runner pipeline, `XunitTestRunner`, message types, reporting transforms, and add a coverage collection module.

### W3: Implement test impact analysis based on code changes

Add a system that determines which tests to run based on source code changes. Build a dependency graph from test-to-source mappings (via coverage data or static analysis). Given a set of changed files, compute the affected test set. Changes span discovery, filtering, configuration, a dependency graph builder, and add a `TestImpactAnalysis` project.

### W4: Add visual regression testing support

Implement a visual testing framework that captures screenshots or rendered output, compares against baselines using perceptual diff, and reports visual regressions. Add `Assert.MatchesVisualBaseline()`, image comparison algorithms, baseline management, and diff report generation. Changes span `xunit.v3.assert/`, add image processing utilities, and update reporters for visual diff output.

### W5: Implement comprehensive test execution recording and replay

Add test execution recording that captures all external interactions (file I/O, network, time, randomness) during test runs. Enable replay mode that replays recorded interactions for deterministic offline test execution. Changes span the runner pipeline, add recording/replay infrastructure in `xunit.v3.core/`, framework hooks for intercepting side effects, and storage management.

### W6: Add multi-framework test aggregation runner

Implement a runner that discovers and executes tests from xUnit, NUnit, and MSTest assemblies in a unified pipeline. Add framework adapters for NUnit and MSTest that translate their test models into xUnit's message pipeline. Changes span `xunit.v3.runner.common/Frameworks/`, message translation, discovery, execution, and unified reporting.

### W7: Implement test environment provisioning and lifecycle management

Add infrastructure for provisioning test environments (Docker containers, databases, cloud resources) before test execution and tearing them down after. Define environments in JSON config, implement provisioning providers, health checking, and shared environment reuse across test collections. Changes span configuration, `xunit.v3.core/Framework/`, runner infrastructure, and add an environments module.

### W8: Add performance benchmarking integrated with test execution

Implement a `[Benchmark]` attribute and benchmarking infrastructure that measures execution time, memory allocations, and GC pressure for annotated test methods. Add statistical analysis (mean, median, percentiles, outlier detection), regression detection against baselines, and benchmark result reporting. Changes span attributes, runners, object model, sinks, and reporters.

### W9: Implement live test dashboard with WebSocket streaming

Add a web-based test execution dashboard that displays real-time test progress, results, and statistics via WebSocket streaming. Implement a `DashboardReporter` that serves an embedded web UI, streams execution events to connected browsers, and persists run history. Changes span reporters, sinks, add an embedded HTTP/WebSocket server, and frontend assets.

### W10: Add mutation testing integration for test suite quality assessment

Implement mutation testing that systematically modifies source code (mutants) and verifies tests catch the mutations. Add a mutation engine with operator library (arithmetic, conditional, null, void), mutant generation, selective test execution per mutant, and survival analysis reporting. Changes span the runner pipeline, add a mutation engine project, test mapping infrastructure, and report generation.
