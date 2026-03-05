# google/googletest

| Field | Value |
|-------|-------|
| **URL** | https://github.com/google/googletest |
| **License** | BSD-3-Clause |
| **Language** | C++ |
| **Scale** | Medium (multi-module project) |
| **Category** | Testing framework |

## Why this repo

- **Multi-module with clear boundaries**: Google Test (test framework —
  assertions, test fixtures, parameterized tests, death tests, typed tests)
  and Google Mock (mocking framework — matchers, actions, expectations,
  cardinalities) are two distinct subsystems with shared infrastructure.
  Both have their own macro systems, internal implementations, and public
  APIs.
- **Well-structured**: `googletest/` and `googlemock/` are cleanly separated
  top-level directories. Each has its own `include/` (public headers) and
  `src/` (implementation). Internal helpers are separated from the public API.
- **Rich history**: 5K+ commits, maintained by Google. The standard C++ test
  framework. PRs cover new assertion types, matcher combinators, and
  cross-platform compatibility.
- **Permissive**: BSD-3-Clause.

## Structure overview

```
googletest/
├── include/gtest/
│   ├── gtest.h              # Main public header
│   ├── gtest-param-test.h   # Parameterized tests
│   ├── gtest-typed-test.h   # Typed tests
│   ├── gtest-death-test.h   # Death tests
│   ├── gtest-matchers.h     # Matchers
│   └── internal/            # Internal implementation headers
│       ├── gtest-internal.h
│       ├── gtest-param-util.h
│       └── gtest-port.h     # Platform abstraction
├── src/
│   ├── gtest.cc             # Core framework implementation
│   ├── gtest-death-test.cc  # Death test implementation
│   ├── gtest-filepath.cc    # File path utilities
│   └── gtest-port.cc        # Platform implementations

googlemock/
├── include/gmock/
│   ├── gmock.h              # Main mock header
│   ├── gmock-matchers.h     # Matcher combinators
│   ├── gmock-actions.h      # Mock actions
│   ├── gmock-spec-builders.h # Expectation building
│   └── internal/            # Internal headers
├── src/
│   ├── gmock.cc
│   ├── gmock-matchers.cc
│   └── gmock-spec-builders.cc
```

## Scale indicators

- ~60 C++ header/source files across both libraries
- ~50K lines of code
- 2-3 levels of include nesting
- Clear gtest/gmock boundary with shared internal utilities

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `EXPECT_EQ` producing unhelpful diff for long multi-line strings

When `EXPECT_EQ(actual_string, expected_string)` fails with long
multi-line strings, the failure output dumps both strings in full
without highlighting which lines differ. The printer in
`gtest-printers.cc` does not generate a line-by-line diff. Add
line-diffing to the string comparison output in `gtest.cc` and
`gtest-printers.cc`.

### N2: Add `EXPECT_NEAR` for `std::chrono::duration` types

`EXPECT_NEAR` works for floating-point types but not for
`std::chrono::duration`. Add an overload that accepts durations and
a tolerance: `EXPECT_NEAR(actual_duration, expected_duration, tolerance)`.
Support mixed duration types (e.g., comparing milliseconds with
microseconds).

### N3: Fix death test child process inheriting open file descriptors

Death tests (`EXPECT_DEATH`) fork the test process, and the child
inherits all open file descriptors from the parent. This can cause
resource leaks and test interference. Add `O_CLOEXEC` handling and
explicit fd closing in the death test child setup. Support both
`fork()` and `clone()` backends.

### N4: Fix `EXPECT_THROW` not checking exception message content

`EXPECT_THROW(expr, ExcType)` verifies the exception type but provides
no way to check the exception message. Add `EXPECT_THROW_MESSAGE(expr,
ExcType, message_matcher)` that also validates `what()` against a
matcher.

### N5: Fix `SetUp()` failures not reported per-test in XML output

When `SetUp()` fails in a test fixture, the XML test report attributes
the failure to the fixture class rather than the specific test case.
Fix the XML reporter to attribute `SetUp()` failures to the currently
running test case.

### N6: Fix `EXPECT_THAT` with `Each` matcher not showing the failing element value

When `EXPECT_THAT(container, Each(Gt(0)))` fails, the error message
says "whose element #2 doesn't match" but does not show the actual
value of the failing element. The `Each` matcher implementation in
`gmock-matchers.h` should include the failing element's value and
its formatted representation in the match explanation.

### N7: Fix `TEST_P` parameter values truncated in failure output on MSVC

On MSVC, parameterized test failures (`TEST_P`) do not include the
full parameter value in the failure output because `PrintToString` in
`gtest-printers.h` truncates output at a short fixed limit. Increase
the truncation threshold for test parameter printing and add an
ellipsis indicator when values are truncated.

### N8: Add `SCOPED_TRACE` with automatic variable capture

`SCOPED_TRACE(message)` requires a manual message string. Add
`SCOPED_TRACE_AUTO()` that automatically captures the current source
location and local variable values in the trace output.

### N9: Fix `TYPED_TEST` not including type name in assertion failure messages

When a `TYPED_TEST` assertion fails, the failure message does not
include which type instantiation caused the failure. The type
information is available via `GetTypeName()` in `gtest-type-util.h`
but the default assertion output in `gtest.cc` omits it. Add the
concrete type name to `TYPED_TEST` failure messages.

### N10: Fix `GTEST_SKIP()` not setting correct status in JSON reporter

When `GTEST_SKIP()` is used, the JSON output (`--gtest_output=json`)
reports the test as `"RUN"` with a `"COMPLETED"` status instead of
`"SKIPPED"`. The JSON reporter in `gtest.cc` does not check for skipped
status when emitting test result entries. Fix the JSON output handler
to include a `"skipped"` field.

## Medium

### M1: Implement parameterized test combination filtering

Add `Combine(gen1, gen2, ...).Where(predicate)` that filters invalid
parameter combinations before test instantiation. Currently
`Combine()` in `gtest-param-test.h` produces the full Cartesian
product, forcing users to skip invalid combos inside the test body.
Add a predicate-based filter in `gtest-param-util.h` that excludes
combinations during generation. Include the filter in test name output.

### M2: Add async test support with timeout

Implement support for testing async operations. Add `EXPECT_EVENTUALLY`
that polls a condition with configurable interval and timeout:
`EXPECT_EVENTUALLY(IsReady(future), timeout_ms(5000))`. Add
`ASSERT_COMPLETES_IN(duration, statement)` that fails if the statement
takes longer than the specified duration. Support integration with
common async patterns (futures, callbacks, condition variables).

### M3: Implement test retry mechanism for flaky tests

Add `--gtest_retry_failed=N` that re-runs failed tests up to N times,
only reporting failure if all retries fail. Track per-test flakiness
statistics (pass/fail counts across retries). Include retry information
in XML and JSON output. Changes span `gtest.cc` (test runner retry
loop), `gtest-internal-inl.h` (result aggregation), `gtest.h` (retry
configuration flag), and the XML/JSON reporters.

### M4: Implement snapshot testing for complex output

Add `EXPECT_SNAPSHOT(name, value)` that compares the formatted value
against a golden file. On first run, create the golden file. On
subsequent runs, compare. Add `--gtest_update_snapshots` flag to
regenerate all snapshots. Support text, JSON, and binary snapshots.

### M5: Add mock function call recording and replay

Implement `EXPECT_CALL_SEQUENCE` that records actual mock calls during
one test run and replays the expected call sequence in subsequent runs.
Useful for characterization testing of legacy code. Store recorded
sequences in files alongside the test.

### M6: Implement test resource management with RAII

Add `TEST_RESOURCE(name, setup_fn, teardown_fn)` that registers
resources shared across tests in a suite. Resources are set up once
before the suite and torn down after. Support dependency ordering
between resources. Resources are available via `GetResource<T>(name)`.

### M7: Add parallel test execution within a single binary

Implement `--gtest_parallel=N` that runs tests across N threads within
the same binary. Handle shared state isolation, per-thread output
buffering, and thread-safe test result collection. Support marking
tests as non-parallelizable with `TEST_SERIAL`.

### M8: Implement test tagging and filtering by tags

Add `TEST_TAG(test, "tag1", "tag2")` for categorizing tests. Support
`--gtest_filter_tags=slow,integration` for running tests by tag.
Support tag-based exclusion `--gtest_exclude_tags=flaky`. Tags are
orthogonal to the existing name-based filtering.

### M9: Implement GMock expectation call-site tracking

When a GMock expectation is violated, the error message shows where
the expectation was set but not where the violating call occurred.
Add call-site tracking that records the source location of each mock
function invocation. Show both the expectation location and the call
location in failure messages. Changes span `gmock-spec-builders.h`
(call recording), `gmock-spec-builders.cc` (source location capture),
and `gmock-function-mocker.h` (call-site propagation).

### M10: Implement test fixture inheritance with scope control

Add fixture scope control: `TEST_F_SCOPED(Fixture, test, Scope::SUITE)`
runs the fixture's SetUp/TearDown once per suite rather than per test.
Support per-test (default), per-suite, and per-binary scopes. Add a
shared state accessor for suite-scoped fixtures.

## Wide

### W1: Implement contract testing framework for mock verification

Add a contract testing mode where GMock expectations recorded in one
test suite can be exported as contracts and verified against a real
implementation in a separate suite. Support
`EXPORT_CONTRACT(mock_obj, "contract.json")` to serialize all
expectations and `VERIFY_CONTRACT(real_obj, "contract.json")` to
replay expected calls. Changes span `gmock-spec-builders.h`
(expectation serialization), `gmock-spec-builders.cc` (contract
export/import), `gmock-matchers.h` (matcher serialization), `gtest.cc`
(contract verification runner), and add contract I/O infrastructure.

### W2: Implement property-based testing extension

Add a property-based testing module (`gtest/property.h`) that
generates random test inputs and checks invariants. Support
`PROPERTY_TEST(name, generator, property_fn)` that generates N random
inputs (configurable) and checks the property. Include built-in
generators for primitive types, strings, containers, and custom
generators via a `Gen<T>` compositional API. On failure, perform
automatic shrinking to find the minimal failing input. Integrate with
existing `EXPECT_*` and `ASSERT_*` macros.

### W3: Implement mutation testing framework

Add `--gtest_mutate` that instruments the code under test with
mutations (arithmetic operator swaps, condition negation, return value
changes) and runs the test suite against each mutation. Report on
surviving mutations (tests didn't catch the change). Requires source
instrumentation, mutation generation, result collection, and a
reporting system.

### W4: Add fuzzing integration framework

Implement `TEST_FUZZ(test, corpus_dir)` that combines Google Test with
fuzzing. The test function receives corpus entries, and the fuzzer
generates new inputs. Support libFuzzer and AFL++ backends. Integrate
crash reproduction with test case registration. Requires a fuzz
harness, corpus management, crash deduplication, and regression test
generation.

### W5: Implement code coverage visualization

Add `--gtest_coverage` that instruments test execution for per-test
coverage tracking. After all tests run, generate a report showing
which tests cover which lines. Identify untested code, redundant tests
(fully subsumed by other tests), and test-to-code mapping. Requires
compiler coverage instrumentation, per-test data collection, report
generation, and HTML output.

### W6: Add benchmark framework integrated with tests

Implement `TEST_BENCHMARK(name, iterations)` that runs micro-benchmarks
alongside unit tests. Support warm-up iterations, statistical analysis
(mean, median, std dev, percentiles), comparison between runs
(regression detection), and result export. Integrate with the test
runner's output and XML reporting. Adds timing infrastructure,
statistical analysis, comparison storage, and output formatting.

### W7: Implement test dependency graph and minimal execution

Add test dependency declarations: `TEST_DEPENDS(test, "other_test")`
meaning this test should only run if the dependency passes. Build a
DAG of test dependencies. Support `--gtest_minimal` that runs only
tests affected by code changes (requires coverage data from W5).
Adds dependency tracking, DAG resolution, skip propagation, and
integration with coverage data.

### W8: Add remote test execution support

Implement `--gtest_remote=host:port` that distributes test execution
across multiple machines. A coordinator splits the test suite across
workers, collects results, and aggregates the output. Support worker
health monitoring, test redistribution on worker failure, and result
streaming. Requires a coordinator server, worker client, test
partitioning, and network protocol.

### W9: Implement test hermiticity checker

Add a mode that detects non-hermetic tests: tests that depend on
global state, file system state, network, environment variables, or
execution order. Run tests in randomized order with sanitized
environments. Report tests that fail under isolation. Requires
environment sandboxing, state capture before/after each test,
filesystem virtualization, and a reporting system.

### W10: Add visual test failure diagnostics

Implement rich failure diagnostics with: visual diffs for string
comparisons (inline highlighting of differences), tree diffs for
container comparisons (show added/removed elements), screenshot
capture for UI tests, and HTML failure reports with expandable
details. Requires diff algorithms, HTML generation, screenshot
infrastructure, and integration with the test runner output system.
