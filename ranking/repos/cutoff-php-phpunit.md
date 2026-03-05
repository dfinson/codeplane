# sebastianbergmann/phpunit

| Field | Value |
|-------|-------|
| **URL** | https://github.com/sebastianbergmann/phpunit |
| **License** | BSD-3-Clause |
| **Language** | PHP |
| **Scale** | Medium (multi-module project) |
| **Category** | Testing framework |
| **Set** | Cutoff |

## Why this repo

- **Canonical PHP testing framework**: PHPUnit is the de-facto standard
  for unit and integration testing in PHP, used across virtually every
  major PHP project. Tasks require understanding a mature, deeply
  interconnected architecture covering assertion logic, test execution
  lifecycle, CLI configuration, and extension hooks.
- **Well-structured**: Source under `src/` with clear module boundaries —
  `Framework/` (TestCase, assertions, constraints, mocking),
  `Runner/` (test execution and suite orchestration),
  `TextUI/` (CLI and configuration), `Event/` (extension event system),
  `Metadata/` (attribute and annotation handling), and
  `Logging/` (result formatters).
- **Rich constraint system**: The `Constraint/` directory contains ~40
  individual constraint classes implementing a composable assertion
  architecture, providing fine-grained matching patterns.
- **Rich history**: 20K+ stars, the foundation of PHP testing
  infrastructure. Issues span assertion correctness, test lifecycle
  edge cases, metadata parsing, and output formatting.
- **Permissive**: BSD-3-Clause license.

## Structure overview

```
src/
├── Framework/
│   ├── TestCase.php                   # Base test class — setUp, tearDown, lifecycle
│   ├── TestSuite.php                  # Collection of tests, suite orchestration
│   ├── Assert.php                     # Static assertion methods (assertEquals, assertTrue, …)
│   ├── Constraint/
│   │   ├── Constraint.php             # Abstract base for all constraints
│   │   ├── IsEqual.php                # Equality comparison constraint
│   │   ├── IsIdentical.php            # Identity (===) comparison
│   │   ├── StringContains.php         # String containment check
│   │   ├── RegularExpression.php      # Regex matching constraint
│   │   ├── Count.php                  # Countable size constraint
│   │   ├── ArrayHasKey.php            # Array key existence
│   │   ├── IsType.php                 # Type checking constraint
│   │   ├── LogicalAnd.php             # Composite: all constraints pass
│   │   ├── LogicalOr.php              # Composite: any constraint passes
│   │   ├── LogicalNot.php             # Composite: negation
│   │   ├── IsInstanceOf.php           # instanceof check
│   │   ├── Exception/                 # Exception-related constraints
│   │   └── … (~40 constraint classes total)
│   ├── MockObject/
│   │   ├── MockBuilder.php            # Fluent mock configuration
│   │   ├── MockObject.php             # Mock interface
│   │   ├── Generator/
│   │   │   └── Generator.php          # Mock class code generation
│   │   ├── Rule/
│   │   │   ├── InvocationOrder.php    # Call count expectations
│   │   │   └── MethodName.php         # Method matching rules
│   │   └── Stub/
│   │       ├── ReturnStub.php         # Return value stubbing
│   │       └── ExceptionStub.php      # Exception throwing stub
│   └── DataProvider/
│       └── DataProvider.php           # Data provider resolution
├── Runner/
│   ├── TestRunner.php                 # Core execution engine
│   ├── TestSuiteLoader.php            # Suite discovery and loading
│   ├── ResultPrinter.php              # Test result output
│   ├── Filter/
│   │   ├── Factory.php                # Filter chain construction
│   │   └── NameFilterIterator.php     # --filter argument matching
│   └── Extension/
│       └── ExtensionBootstrap.php     # Extension loading mechanism
├── TextUI/
│   ├── Application.php                # CLI entry point
│   ├── Command/
│   │   └── Command.php                # CLI command parsing
│   ├── Configuration/
│   │   ├── Configuration.php          # Parsed configuration object
│   │   ├── Loader.php                 # XML configuration file parsing
│   │   ├── Merger.php                 # CLI + XML config merging
│   │   └── TestSuiteMapper.php        # Config → TestSuite resolution
│   └── Output/
│       ├── Default/
│       │   └── ProgressPrinter.php    # Dot-based progress output
│       └── TeamCity/
│           └── TeamCityLogger.php     # TeamCity service messages
├── Event/
│   ├── Dispatcher.php                 # Event dispatch hub
│   ├── TypeMap.php                    # Event type registration
│   ├── Events/
│   │   ├── Test/
│   │   │   ├── Prepared.php           # Test about to run
│   │   │   ├── Passed.php             # Test passed event
│   │   │   ├── Failed.php             # Test failed event
│   │   │   └── Errored.php            # Test errored event
│   │   └── TestSuite/
│   │       ├── Started.php            # Suite started event
│   │       └── Finished.php           # Suite finished event
│   └── Subscriber/
│       └── Subscriber.php             # Extension subscriber interface
├── Metadata/
│   ├── Metadata.php                   # Metadata value object
│   ├── MetadataCollection.php         # Collection of metadata items
│   ├── Parser/
│   │   ├── AttributeParser.php        # PHP 8 attribute parsing
│   │   └── AnnotationParser.php       # Docblock annotation parsing
│   └── Registry.php                   # Metadata cache and lookup
├── Logging/
│   ├── JUnit/
│   │   └── JUnitXmlLogger.php         # JUnit XML report generation
│   └── TeamCity/
│       └── TeamCityLogger.php         # TeamCity format logging
└── Util/
    ├── Color.php                      # Terminal color formatting
    ├── Filter.php                     # Stack trace filtering
    ├── Xml/
    │   ├── Loader.php                 # XML file loading utilities
    │   └── SchemaDetector.php         # XML schema version detection
    ├── ExcludeList.php                # Class exclusion for coverage
    └── Reflection.php                 # Reflection helper utilities
```

## Scale indicators

- ~200–250 PHP source files under src/
- ~30K lines of code
- 3–4 levels of namespace nesting
- Clear module boundaries with Framework/ as the largest subsystem

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix IsEqual constraint not respecting delta for nested float arrays

`IsEqual` accepts a `$delta` parameter for float comparison tolerance,
but when comparing arrays containing nested float values, the delta is
not propagated to recursive comparisons. Arrays like `[1.0, [2.001]]`
vs `[1.0, [2.002]]` fail with delta `0.01` even though all floats are
within tolerance. Fix `IsEqual.php` to pass the delta through the
recursive comparison path.

### N2: Fix TestCase::expectException not clearing between data provider iterations

When a test method uses both `expectException()` and a data provider,
the expected exception set in one iteration carries over to the next
if the subsequent iteration does not call `expectException()` again.
Fix `TestCase.php` to reset the expected exception state between data
provider iterations in the lifecycle reset logic.

### N3: Fix AnnotationParser not handling multi-line @depends annotations

`AnnotationParser` parses `@depends` annotations but fails when the
annotation spans multiple lines (e.g., with a long class-qualified
method name that wraps). The parser only reads the first line after the
tag. Fix `AnnotationParser.php` to handle continuation lines for
`@depends` annotations.

### N4: Fix Count constraint failing on generators that have already started

The `Count` constraint calls `iterator_count()` on generators, but if
the generator has already yielded values, the count only reflects
remaining elements, not the total. Fix `Count.php` to detect partially
consumed generators and report an informative failure message instead
of an incorrect count.

### N5: Fix MockBuilder::onlyMethods silently ignoring non-existent method names

`MockBuilder::onlyMethods()` accepts an array of method names to mock
but does not validate that the methods exist on the mocked class. If a
method name is misspelled, the mock silently ignores it, leading to
confusing test behavior. Fix `MockBuilder.php` to throw an
`InvalidArgumentException` when a method name does not exist on the
target class.

### N6: Fix ProgressPrinter not accounting for multi-byte characters in alignment

`ProgressPrinter` right-aligns the progress counter (e.g.,
`... 3 / 15`) based on string length. When test names or group labels
contain multi-byte UTF-8 characters, the alignment calculation is
wrong because `strlen` counts bytes, not display columns. Fix
`ProgressPrinter.php` to use `mb_strwidth()` for alignment.

### N7: Fix Filter stack trace exclusion not matching namespaced class paths

`Filter.php` excludes internal PHPUnit frames from failure stack
traces using prefix matching against file paths. When PHPUnit is
installed via a symlinked Composer path repository, the real path
does not match the exclusion prefix. Fix `Filter.php` to resolve
symlinks with `realpath()` before applying the exclusion list.

### N8: Fix Configuration Loader not validating duplicate testSuite names in XML

`Configuration/Loader.php` parses `<testsuite>` elements from
`phpunit.xml` but accepts duplicate `name` attributes without error.
When two suites share a name, the `--testsuite` CLI filter matches
only the first one, silently dropping tests. Fix the loader to detect
and report duplicate test suite names during configuration parsing.

### N9: Fix JUnitXmlLogger emitting invalid XML for assertion messages with CDATA

`JUnitXmlLogger` wraps failure messages in CDATA sections, but if the
assertion message itself contains `]]>`, the resulting XML is
malformed. Fix `JUnitXmlLogger.php` to escape or split CDATA sections
when the message contains the CDATA end delimiter.

### N10: Fix Event Dispatcher not catching subscriber exceptions during Test::Prepared

When `Dispatcher.php` fires `Test\Prepared` events, an exception
thrown by one subscriber aborts dispatch to subsequent subscribers and
propagates into the runner, causing the test to be marked as errored
rather than simply logging the subscriber failure. Fix the dispatcher
to catch subscriber exceptions during `Prepared` events and report
them via a warning instead.

## Medium

### M1: Add configurable assertion failure diff output format

Implement a `--diff-format` CLI option supporting `unified` (default),
`side-by-side`, and `json` output modes for assertion failure diffs.
Changes span `TextUI/Command/Command.php` for argument parsing,
`TextUI/Configuration/Configuration.php` for the setting,
`TextUI/Configuration/Merger.php` for CLI-to-config mapping, and
`Framework/Assert.php` plus `Constraint/IsEqual.php` for invoking the
selected formatter.

### M2: Implement retry-on-failure support for flaky tests

Add a `#[Retry(times: 3)]` attribute that causes a failing test to be
re-executed up to the specified number of times before being marked as
failed. Changes span `Metadata/Parser/AttributeParser.php` for
parsing the new attribute, `Metadata/Metadata.php` for the retry
metadata type, `Runner/TestRunner.php` for the retry loop, and
`Event/Events/Test/` for new `Retried` event emission.

### M3: Add parallel data provider execution within a test method

Implement `#[ParallelDataProvider]` that runs data provider iterations
concurrently using PHP fibers or child processes. Results are collected
and reported once all iterations complete. Changes span
`Framework/DataProvider/DataProvider.php` for parallel scheduling,
`Framework/TestCase.php` for fiber-based iteration,
`Runner/TestRunner.php` for result aggregation, and
`Event/Events/Test/` for per-iteration event granularity.

### M4: Implement constraint composition DSL with named matchers

Add `Assert::that($value)->is()->greaterThan(5)->and()->lessThan(10)`
fluent assertion syntax that composes constraints via a builder.
Changes span `Framework/Assert.php` for the `that()` entry point,
new `Framework/Constraint/Builder/` classes for the fluent chain,
`Framework/Constraint/LogicalAnd.php` and `LogicalOr.php` for
integration, and `Framework/Constraint/Constraint.php` for builder
protocol support.

### M5: Add mock method call recording with assertion replay

Implement `MockBuilder::enableCallRecording()` that records all method
calls on a mock and provides `assertCallSequence()` to verify specific
orderings. Changes span `MockObject/MockBuilder.php` for the
configuration, `MockObject/MockObject.php` for the recording
interface, new `MockObject/CallRecorder.php` for storage, and
`MockObject/Rule/InvocationOrder.php` for sequence validation
integration.

### M6: Implement test impact analysis using metadata dependencies

Add `--affected-since=<commit>` that uses `@covers` and `@depends`
metadata to determine which tests are affected by changed classes.
Changes span `TextUI/Command/Command.php` for the CLI option,
`Metadata/Registry.php` for coverage-to-test mapping,
`Runner/TestSuiteLoader.php` for filtering the suite based on
affected classes, and `TextUI/Configuration/TestSuiteMapper.php` for
integration with suite construction.

### M7: Add structured test result output in JSON format

Implement `--result-format=json` that emits a JSON document with test
results, durations, assertion counts, and failure details. Changes
span `TextUI/Command/Command.php` for the option,
`TextUI/Output/Json/JsonResultPrinter.php` as a new printer
implementation, `Runner/TestRunner.php` for wiring the printer, and
`Event/Subscriber/Subscriber.php` for a JSON-emitting subscriber.

### M8: Implement mock generation caching for faster test startup

Add a disk cache for generated mock class code, keyed by the mocked
class signature hash. On subsequent runs, load cached mock classes
instead of regenerating them. Changes span
`MockObject/Generator/Generator.php` for cache lookup and storage,
`MockObject/MockBuilder.php` for cache path configuration,
`TextUI/Configuration/Configuration.php` for the cache directory
setting, and `Util/` for cache invalidation utilities.

### M9: Add custom constraint registration via configuration

Implement `<constraint>` elements in `phpunit.xml` that register
custom constraint classes for use in assertions without requiring
explicit imports. Changes span `TextUI/Configuration/Loader.php` for
parsing constraint registrations, `TextUI/Configuration/Configuration.php`
for storing them, `Framework/Assert.php` for looking up registered
constraints by alias, and `Framework/Constraint/Constraint.php` for
the registration protocol.

### M10: Implement snapshot testing support for assertions

Add `Assert::assertMatchesSnapshot($value)` that compares a value
against a stored snapshot file, creating it on first run. Support
JSON, serialized PHP, and text snapshot formats. Changes span
`Framework/Assert.php` for the assertion method, new
`Framework/Snapshot/` classes for storage and comparison,
`Framework/Constraint/MatchesSnapshot.php` for the constraint
implementation, and `TextUI/Command/Command.php` for
`--update-snapshots` flag support.

## Wide

### W1: Implement attribute-based test lifecycle hooks with priority ordering

Add `#[BeforeAll]`, `#[AfterAll]`, `#[BeforeEach]`, `#[AfterEach]`
attributes with `priority` parameter that replace the inheritance-based
`setUp`/`tearDown` lifecycle. Support multiple hook methods per class
with ordered execution and cross-class hook inheritance. Changes span
`Metadata/Parser/AttributeParser.php` for new attributes,
`Metadata/Metadata.php` for hook metadata types,
`Framework/TestCase.php` for lifecycle invocation rewrite,
`Runner/TestRunner.php` for suite-level hook execution,
`Event/Events/Test/` for hook-specific events, and
`TextUI/Output/` for hook failure reporting.

### W2: Add test isolation via process forking with shared fixture support

Implement `#[RunInSeparateProcess]` improvements that fork test
execution while sharing expensive fixtures (database connections,
service containers) via shared memory or Unix sockets. Changes span
`Framework/TestCase.php` for process forking,
`Runner/TestRunner.php` for process management and result collection,
`Framework/TestSuite.php` for fixture sharing protocol,
`Event/Dispatcher.php` for cross-process event marshaling,
`Logging/` for aggregating results from child processes, and
`Util/` for IPC helper classes.

### W3: Implement extensible code coverage driver architecture

Add a `CoverageDriver` plugin interface that supports custom coverage
backends beyond Xdebug and PCOV — including AST-based instrumentation
and sampling profilers. Changes span new `Coverage/` directory with
driver interface and built-in implementations,
`TextUI/Configuration/Loader.php` for driver selection,
`TextUI/Command/Command.php` for `--coverage-driver` option,
`Runner/TestRunner.php` for driver lifecycle management,
`Logging/` for coverage report generation with driver-specific data,
and `Metadata/` for `#[CoversClass]` integration with custom drivers.

### W4: Add real-time test result streaming over WebSocket

Implement a `--live` mode that starts a WebSocket server and streams
test events to connected clients in real-time. Support filtered
subscriptions per suite or test name pattern. Changes span
`TextUI/Command/Command.php` for the `--live` option,
new `TextUI/Output/Live/` WebSocket server classes,
`Event/Dispatcher.php` for streaming subscriber integration,
`Event/Events/` for serialization of all event types,
`Runner/TestRunner.php` for server lifecycle management, and
`TextUI/Configuration/` for live output configuration.

### W5: Implement dependency injection container for test fixtures

Add a `#[Inject]` attribute for constructor and method parameter
injection using a lightweight DI container scoped per test suite.
Support factory registrations, singleton fixtures, and
auto-wiring from type hints. Changes span new
`Framework/DependencyInjection/` container classes,
`Metadata/Parser/AttributeParser.php` for `#[Inject]` parsing,
`Framework/TestCase.php` for injected construction,
`Framework/TestSuite.php` for container scoping,
`Runner/TestRunner.php` for container lifecycle,
and `TextUI/Configuration/Loader.php` for XML-based service
registration.

### W6: Add parameterized test suite generation from external data sources

Implement `#[ExternalDataProvider(source: 'api://...')]` that fetches
test data from external sources (REST APIs, databases, CSV files) and
generates parameterized tests at suite construction time. Support
caching, schema validation, and incremental fetching. Changes span
`Framework/DataProvider/` for external source adapters, new
`Framework/DataProvider/External/` classes for each source type,
`Metadata/Parser/AttributeParser.php` for the new attribute,
`Runner/TestSuiteLoader.php` for deferred suite construction,
`TextUI/Configuration/` for source credentials and caching config,
and `Event/Events/` for data fetch lifecycle events.

### W7: Implement hierarchical test tagging with conditional execution

Add `#[Tag('slow', 'database')]` attribute with support for tag
expressions in `--filter` (e.g., `slow AND NOT database`, `ci OR
quick`). Implement tag inheritance through test class hierarchies and
suite nesting. Changes span `Metadata/Parser/AttributeParser.php`
for tag parsing, `Metadata/` for tag expression evaluation,
`Runner/Filter/` for tag-based filtering with boolean expressions,
`TextUI/Command/Command.php` for enhanced `--filter` syntax,
`TextUI/Configuration/Loader.php` for XML tag groups, and
`Runner/TestSuiteLoader.php` for tag-based suite pruning.

### W8: Add mutation testing integration with constraint-aware mutant detection

Implement `--mutation-testing` mode that instruments source code with
mutations and re-runs relevant tests to calculate mutation score.
Leverage `@covers` metadata to map tests to mutated code. Changes span
new `MutationTesting/` directory with mutator engine and operators,
`Metadata/Registry.php` for test-to-source mapping,
`Runner/TestRunner.php` for selective test re-execution,
`Framework/Assert.php` for mutation-killed detection,
`Logging/` for mutation score reporting, and
`TextUI/Command/Command.php` for mutation testing configuration.

### W9: Implement test execution profiling with bottleneck analysis

Add `--profile` mode that instruments test execution to identify
performance bottlenecks: slow setUp/tearDown, expensive assertions,
mock generation time, and data provider computation. Generate flame
graph data and an HTML report. Changes span new `Profiling/` classes
for instrumentation and report generation,
`Framework/TestCase.php` for lifecycle timing hooks,
`Framework/Assert.php` for assertion duration tracking,
`MockObject/Generator/Generator.php` for generation timing,
`Runner/TestRunner.php` for profiling lifecycle management, and
`TextUI/Output/` for profile summary display.

### W10: Add cross-suite test dependency resolution with topological ordering

Implement `#[DependsOnSuite('IntegrationTests')]` that models
dependencies between test suites and executes them in topological
order. Support cross-suite fixture sharing via a registry and
automatic skip propagation when a dependency suite fails. Changes span
`Metadata/Parser/AttributeParser.php` for suite dependency attributes,
`Framework/TestSuite.php` for dependency graph construction,
`Runner/TestSuiteLoader.php` for topological sorting,
`Runner/TestRunner.php` for ordered execution with skip propagation,
`Framework/TestCase.php` for cross-suite fixture access,
and `Event/Events/TestSuite/` for dependency lifecycle events.
