# sebastianbergmann/phpunit

| Field | Value |
|-------|-------|
| **URL** | https://github.com/sebastianbergmann/phpunit |
| **License** | BSD-3-Clause |
| **Language** | PHP |
| **Scale** | Medium (multi-module project) |
| **Category** | Testing framework |
| **Set** | Cutoff |
| **Commit** | `7d26bdd5d9ae5499ef4191cd79567c550d0b8ca0` |

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

### N1: Fix IsEqualWithDelta::toString() displaying delta as zero for very small values

`IsEqualWithDelta::toString()` formats the delta value using `sprintf('%F', $this->delta)`, which always produces exactly 6 decimal places. For very small delta values such as `1.0E-7`, this format rounds to `0.000000`, producing a constraint description like `is equal to ... with delta <0.000000>`. This makes the failure message appear as though no delta tolerance was set, concealing the actual configured value. Fix `IsEqualWithDelta.php` so that `toString()` formats the delta with sufficient precision to always display a non-zero value — for example by using `var_export()` or a trimmed high-precision `sprintf` — so that small deltas remain visible in assertion failure messages.

### N2: Fix TestCase::expectExceptionMessageMatches not validating the regex pattern

`TestCase::expectExceptionMessageMatches()` stores the provided string as the expected exception message pattern without validating that it is a valid PCRE regular expression. If a caller passes a malformed pattern (e.g., a string that is missing delimiters or contains invalid syntax), `expectExceptionMessageMatches()` silently accepts it. The error only surfaces later when the `ExceptionMessageMatchesRegularExpression` constraint tries to use `preg_match()` against the actual exception message, producing a cryptic PHP warning or unexpected assertion failure instead of an immediate, clear error. Fix `TestCase.php` so that `expectExceptionMessageMatches()` validates the pattern immediately using `@preg_match()` and throws an `InvalidArgumentException` with a descriptive message if the pattern is not a valid regular expression.

### N3: Fix AttributeParser silently mishandling #[Depends] with a class-qualified method name

`AttributeParser` parses `#[Depends]` attributes by passing the entire argument string directly to `Metadata::dependsOnMethod()` as the method name on the current class. When a developer writes `#[Depends('OtherTest::testSetup')]` intending to declare a cross-class dependency, the full string `'OtherTest::testSetup'` is used as the method name without any splitting or validation. The dependency is registered against a method whose name contains `::`, which never matches any real method, so the declared dependency is silently ignored. Fix `AttributeParser.php` so that when a `#[Depends]` value contains `::`, it detects this case and throws an `InvalidAttributeException` with a message directing the developer to use `#[DependsExternal]` (or `#[DependsExternalUsingDeepClone]` / `#[DependsExternalUsingShallowClone]`) for cross-class dependencies.

### N4: Fix Count constraint throwing a generic exception for all Generator values

The `Count` constraint detects `Generator` instances in `getCountOf()` and immediately throws `GeneratorNotSupportedException` for every generator, regardless of whether it has been started. A generator whose `key()` is `null` and `valid()` returns `true` is a fresh, unconsumed generator that could be iterated to obtain a count via `iterator_count()` without loss of data. Fix `Count.php` so that `getCountOf()` allows fresh generators (where `$generator->key() === null && !$generator->valid()` is false but no values have been consumed) to be counted with `iterator_count()`, while still throwing `GeneratorNotSupportedException` with an informative message for generators that have already yielded values (detectable by `$generator->key() !== null`).

### N5: Fix TestDoubleBuilder::onlyMethods accepting private methods that cannot be overridden

`TestDoubleBuilder::onlyMethods()` validates that each specified method exists on the target class using `ReflectionClass::hasMethod()`, throwing `CannotUseOnlyMethodsException` when a method is not found. However, it does not check whether the matched method is `private`. Private methods cannot be overridden in a subclass, so a mock generated with a private method name in `onlyMethods()` either fails at code-generation time with a cryptic error or silently produces a mock that does not intercept the private method. Fix `TestDoubleBuilder.php` to also check the method's visibility after `hasMethod()` succeeds, and throw `CannotUseOnlyMethodsException` (with an appropriate message) when the named method is declared `private`.

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

`TextUI/Configuration/Xml/Loader.php` parses `<testsuite>` elements from
`phpunit.xml` but accepts duplicate `name` attributes without error.
When two suites share a name, the `--testsuite` CLI filter matches
only the first one, silently dropping tests. Fix the loader to detect
and report duplicate test suite names during configuration parsing.
Also update `phpunit.xsd` to add an XSD `unique` constraint on the
`name` attribute of `<testsuite>` elements so that XML-level
validation catches duplicates before the loader runs.

### N9: Fix JUnitXmlLogger double-encoding special characters in failure messages

`JunitXmlLogger` passes failure message text through `Xml::prepareString()` before setting it as element text content via `DOMDocument::createElement()`. `Xml::prepareString()` calls `htmlspecialchars()` to encode characters such as `&` to `&amp;`, `<` to `&lt;`, and `>` to `&gt;`. However, `DOMDocument::createElement()` accepts a raw text value and the DOM re-encodes it during serialization, resulting in double-encoding: a literal `&` in a failure message becomes `&amp;amp;` in the JUnit XML output instead of the correct `&amp;`. Fix `JunitXmlLogger.php` to not pre-encode the text content with `htmlspecialchars()` before passing it to the DOM; instead use only the character-stripping portion of `Xml::prepareString()` (removing invalid XML control characters) and let the DOM handle the XML escaping.

### N10: Fix DirectDispatcher misclassifying subscriber-triggered PHPUnit exceptions as internal errors

`DirectDispatcher::handleThrowable()` uses `isThrowableFromThirdPartySubscriber()` to decide whether to log a subscriber exception as a PHPUnit warning or rethrow it as an internal error. That check uses `$t->getFile()` — the file where the exception was instantiated — to determine whether the exception originated in third-party code. When a third-party extension subscriber calls a PHPUnit utility method that internally throws a `Throwable`, the exception file points to a PHPUnit-internal source file, so `isThrowableFromThirdPartySubscriber()` returns `false` and the exception is rethrown into the runner, marking the test as errored rather than logging a subscriber warning. Fix `DirectDispatcher.php` to inspect the exception's stack trace frames to find the subscriber dispatch frame and determine if that frame belongs to third-party code, rather than relying solely on the exception's originating file.

### N11: Fix stale PHPStan result cache path in phpstan.neon

`phpstan.neon` specifies `resultCachePath: %tmpDir%/phpunit-13.0.php` as the PHPStan analysis result cache location, but the repository is now at version 13.1. Using a cache path that references the previous series means the cached analysis data from 13.0 analysis runs may be reused unexpectedly, masking new issues that arise from 13.1 code changes. Fix `phpstan.neon` to update `resultCachePath` to `%tmpDir%/phpunit-13.1.php` so that PHPStan uses a fresh cache bucket for the current series.

## Medium

### M1: Add configurable assertion failure diff output format

Implement a `--diff-format` CLI option supporting `unified` (default),
`side-by-side`, and `json` output modes for assertion failure diffs.
Changes span `TextUI/Command/Command.php` for argument parsing,
`TextUI/Configuration/Configuration.php` for the setting,
`TextUI/Configuration/Merger.php` for CLI-to-config mapping, and
`Framework/Assert.php` plus `Constraint/IsEqual.php` for invoking the
selected formatter. Also update `.github/workflows/ci.yaml` to add a
CI matrix entry that runs the test suite with `--diff-format=json` to
ensure the new output mode does not regress.

### M2: Implement retry-on-failure support for flaky tests

Add a `#[Retry(times: 3)]` attribute that causes a failing test to be
re-executed up to the specified number of times before being marked as
failed. Changes span `Metadata/Parser/AttributeParser.php` for
parsing the new attribute, `Metadata/Metadata.php` for the retry
metadata type, `Framework/TestRunner/TestRunner.php` for the retry loop, and
`Event/Events/Test/` for new `Retried` event emission.

### M3: Add parallel data provider execution within a test method

Implement `#[ParallelDataProvider]` that runs data provider iterations
concurrently using PHP fibers or child processes. Results are collected
and reported once all iterations complete. Changes span
`Metadata/Api/DataProvider.php` for parallel scheduling,
`Framework/TestCase.php` for fiber-based iteration,
`Framework/TestRunner/TestRunner.php` for result aggregation, and
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
orderings. Changes span `Framework/MockObject/MockBuilder.php` for the
configuration, `Framework/MockObject/Runtime/Interface/MockObject.php` for the recording
interface, new `Framework/MockObject/CallRecorder.php` for storage, and
`Framework/MockObject/Runtime/Rule/InvocationOrder.php` for sequence validation
integration.

### M6: Implement test impact analysis using metadata dependencies

Add `--affected-since=<commit>` that uses `@covers` and `@depends`
metadata to determine which tests are affected by changed classes.
Changes span `TextUI/Command/Command.php` for the CLI option,
`Metadata/Parser/Registry.php` for coverage-to-test mapping,
`Runner/TestSuiteLoader.php` for filtering the suite based on
affected classes, and `TextUI/Configuration/Xml/TestSuiteMapper.php` for
integration with suite construction.

### M7: Add structured test result output in JSON format

Implement `--result-format=json` that emits a JSON document with test
results, durations, assertion counts, and failure details. Changes
span `TextUI/Command/Command.php` for the option,
`TextUI/Output/Json/JsonResultPrinter.php` as a new printer
implementation, `TextUI/TestRunner.php` for wiring the printer, and
`Event/Subscriber.php` for a JSON-emitting subscriber.

### M8: Implement mock generation caching for faster test startup

Add a disk cache for generated mock class code, keyed by the mocked
class signature hash. On subsequent runs, load cached mock classes
instead of regenerating them. Changes span
`Framework/MockObject/Generator/Generator.php` for cache lookup and storage,
`Framework/MockObject/MockBuilder.php` for cache path configuration,
`TextUI/Configuration/Configuration.php` for the cache directory
setting, and `Util/` for cache invalidation utilities.

### M9: Add custom constraint registration via configuration

Implement `<constraint>` elements in `phpunit.xml` that register
custom constraint classes for use in assertions without requiring
explicit imports. Changes span `TextUI/Configuration/Xml/Loader.php` for
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

### M11: Update build configuration and CI for PHAR reproducibility

The `build.xml` Ant configuration provides targets for dependency
installation and PHAR building but does not produce reproducible
PHAR archives (timestamps and file ordering vary between builds).
Add a `reproducible-phar` target to `build.xml` that normalises
file timestamps and sorts entries deterministically. Update
`composer.json` to add a `scripts.build` command that invokes the
Ant target for environments without Ant installed. Update
`.github/workflows/ci.yaml` to add a `phar-build` job that builds
the PHAR and verifies its checksum matches across two independent
builds. Update `phpstan.neon` to add the `build/` directory
to `excludePaths` so the PHAR-scoped source files are not
analysed alongside the main source.

## Wide

### W1: Implement attribute-based test lifecycle hooks with priority ordering

Add `#[BeforeAll]`, `#[AfterAll]`, `#[BeforeEach]`, `#[AfterEach]`
attributes with `priority` parameter that replace the inheritance-based
`setUp`/`tearDown` lifecycle. Support multiple hook methods per class
with ordered execution and cross-class hook inheritance. Changes span
`Metadata/Parser/AttributeParser.php` for new attributes,
`Metadata/Metadata.php` for hook metadata types,
`Framework/TestCase.php` for lifecycle invocation rewrite,
`Framework/TestRunner/TestRunner.php` for suite-level hook execution,
`Event/Events/Test/` for hook-specific events, and
`TextUI/Output/` for hook failure reporting.

### W2: Add test isolation via process forking with shared fixture support

Implement `#[RunInSeparateProcess]` improvements that fork test
execution while sharing expensive fixtures (database connections,
service containers) via shared memory or Unix sockets. Changes span
`Framework/TestCase.php` for process forking,
`Framework/TestRunner/TestRunner.php` for process management and result collection,
`Framework/TestSuite.php` for fixture sharing protocol,
`Event/Dispatcher/DirectDispatcher.php` for cross-process event marshaling,
`Logging/` for aggregating results from child processes, and
`Util/` for IPC helper classes.

### W3: Implement extensible code coverage driver architecture

Add a `CoverageDriver` plugin interface that supports custom coverage
backends beyond Xdebug and PCOV — including AST-based instrumentation
and sampling profilers. Changes span new `Coverage/` directory with
driver interface and built-in implementations,
`TextUI/Configuration/Xml/Loader.php` for driver selection,
`TextUI/Command/Command.php` for `--coverage-driver` option,
`Framework/TestRunner/TestRunner.php` for driver lifecycle management,
`Logging/` for coverage report generation with driver-specific data,
and `Metadata/` for `#[CoversClass]` integration with custom drivers.

### W4: Add real-time test result streaming over WebSocket

Implement a `--live` mode that starts a WebSocket server and streams
test events to connected clients in real-time. Support filtered
subscriptions per suite or test name pattern. Changes span
`TextUI/Command/Command.php` for the `--live` option,
new `TextUI/Output/Live/` WebSocket server classes,
`Event/Dispatcher/DirectDispatcher.php` for streaming subscriber integration,
`Event/Events/` for serialization of all event types,
`TextUI/TestRunner.php` for server lifecycle management, and
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
`Framework/TestRunner/TestRunner.php` for container lifecycle,
and `TextUI/Configuration/Xml/Loader.php` for XML-based service
registration.

### W6: Add parameterized test suite generation from external data sources

Implement `#[ExternalDataProvider(source: 'api://...')]` that fetches
test data from external sources (REST APIs, databases, CSV files) and
generates parameterized tests at suite construction time. Support
caching, schema validation, and incremental fetching. Changes span
`Metadata/Api/DataProvider.php` for external source adapters, new
`Metadata/Api/DataProvider/External/` classes for each source type,
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
`TextUI/Configuration/Xml/Loader.php` for XML tag groups, and
`Runner/TestSuiteLoader.php` for tag-based suite pruning. Also update
`phpunit.xsd` to add a `<tagGroups>` element definition that allows
named tag groups to be defined in `phpunit.xml`, and update
`build.xml` to add a `tagged-tests` target that runs only tests
tagged with `ci` for fast feedback during development.

### W8: Add mutation testing integration with constraint-aware mutant detection

Implement `--mutation-testing` mode that instruments source code with
mutations and re-runs relevant tests to calculate mutation score.
Leverage `@covers` metadata to map tests to mutated code. Changes span
new `MutationTesting/` directory with mutator engine and operators,
`Metadata/Parser/Registry.php` for test-to-source mapping,
`Framework/TestRunner/TestRunner.php` for selective test re-execution,
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
`Framework/MockObject/Generator/Generator.php` for generation timing,
`Framework/TestRunner/TestRunner.php` for profiling lifecycle management, and
`TextUI/Output/` for profile summary display.

### W10: Add cross-suite test dependency resolution with topological ordering

Implement `#[DependsOnSuite('IntegrationTests')]` that models
dependencies between test suites and executes them in topological
order. Support cross-suite fixture sharing via a registry and
automatic skip propagation when a dependency suite fails. Changes span
`Metadata/Parser/AttributeParser.php` for suite dependency attributes,
`Framework/TestSuite.php` for dependency graph construction,
`Runner/TestSuiteLoader.php` for topological sorting,
`Framework/TestRunner/TestRunner.php` for ordered execution with skip propagation,
`Framework/TestCase.php` for cross-suite fixture access,
and `Event/Events/TestSuite/` for dependency lifecycle events.

### W11: Overhaul project configuration for the 14.0 release

Prepare the repository's non-code configuration for the PHPUnit 14.0
major release. Create `schema/14.0.xsd` by copying `schema/13.0.xsd`
and adding new elements for baseline configuration, tag groups, and
diff format settings. Update `phpunit.xsd` (the root-level schema)
to reflect the 14.0 structure. Update `phpunit.xml` to reference
the new schema via `xsi:noNamespaceSchemaLocation="https://schema.phpunit.de/14.0/phpunit.xsd"`
and add `failOnNotice="true"`. Update `DEPRECATIONS.md`
to add a "14.0 Hard Deprecations" section listing features scheduled
for removal. Update `build.xml` to add a `validate-schema` target
that runs `xmllint` against the XSD files in `schema/`. Update
`.github/workflows/ci.yaml` to bump the minimum PHP version to 8.5
in the matrix and add a schema validation step using the new Ant
target. Update `.github/CONTRIBUTING.md` to add type-safety
requirements (all new code must pass PHPStan level 6) and reference
the updated `DEPRECATIONS.md`. Update `composer.json` to add
`phpstan/phpstan` to `require-dev` with version `^2.0` and add a
`scripts.phpstan` entry.
