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

### N1: Fix `EXPECT_THAT` with `ContainerEq` not showing element diff for sets

When using `EXPECT_THAT(actual_set, ContainerEq(expected_set))`, the
failure message shows "which has these unexpected elements: {3}" but
does not show which expected elements are missing. For set-like
containers (where element order is irrelevant), show both "unexpected"
and "missing" elements. The matchers currently only handle vector diffs.

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

### N6: Add `EXPECT_THAT` matcher for `std::variant` types

`EXPECT_THAT` has no built-in matcher for `std::variant`. Add
`VariantWith<T>(inner_matcher)` that checks the variant holds type T
and the value matches the inner matcher.

### N7: Fix death test child process output truncated on Windows

On Windows, death test output captured from the child process is
truncated at 4096 bytes. The pipe buffer size is hardcoded. Increase
the pipe buffer and implement chunked reading.

### N8: Add `SCOPED_TRACE` with automatic variable capture

`SCOPED_TRACE(message)` requires a manual message string. Add
`SCOPED_TRACE_AUTO()` that automatically captures the current source
location and local variable values in the trace output.

### N9: Fix `--gtest_random_seed` not affecting typed test ordering

When using `--gtest_shuffle` with `--gtest_random_seed`, typed test
suites (`TYPED_TEST_SUITE`) are not shuffled. The shuffle logic skips
type-parameterized test suites. Fix the shuffler to include all test
suite types.

### N10: Fix mock function matcher error messages showing raw type names

When a GMock matcher fails, the error message shows mangled C++ type
names (e.g., `St6vectorIiSaIiEE`) instead of human-readable names.
Apply `abi::__cxa_demangle` on platforms that support it.

## Medium

### M1: Implement test fixture parameterization with named cases

Add named parameterized test cases:
`INSTANTIATE_TEST_SUITE_P(Cases, MyTest, ValuesIn({{"empty", {}}, {"single", {1}}, {"many", {1,2,3}}}))`
where each test case has a human-readable name that appears in test
output instead of the default `0`, `1`, `2` indices. Support custom
name generators that produce descriptive names from parameter values.

### M2: Add async test support with timeout

Implement support for testing async operations. Add `EXPECT_EVENTUALLY`
that polls a condition with configurable interval and timeout:
`EXPECT_EVENTUALLY(IsReady(future), timeout_ms(5000))`. Add
`ASSERT_COMPLETES_IN(duration, statement)` that fails if the statement
takes longer than the specified duration. Support integration with
common async patterns (futures, callbacks, condition variables).

### M3: Implement mock call sequence expectations

Add `InSequence` improvements for GMock: support partial ordering
(some calls must be ordered relative to each other but others can
interleave), add `After(other_expectation)` for DAG-style ordering,
and provide a visual sequence diagram in the failure output showing
the expected vs actual call order. The current `InSequence` only
supports total ordering within a group.

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

### M9: Add custom test reporter interface

Implement a `TestReporter` interface that receives structured test
events (suite start, test start, assertion result, test end, suite end)
and produces output. Include built-in reporters: console (default),
JUnit XML (existing), JSON, TAP, and TeamCity. Support multiple
simultaneous reporters.

### M10: Implement test fixture inheritance with scope control

Add fixture scope control: `TEST_F_SCOPED(Fixture, test, Scope::SUITE)`
runs the fixture's SetUp/TearDown once per suite rather than per test.
Support per-test (default), per-suite, and per-binary scopes. Add a
shared state accessor for suite-scoped fixtures.

## Wide

### W1: Add built-in code coverage integration

Implement `--gtest_coverage` that instruments test execution for code
coverage without external tools. Use compiler-provided coverage
instrumentation (`__gcov_flush`, `__llvm_profile_write_file`). After
all tests complete, generate a coverage report showing line and branch
coverage per source file. Support per-test-case coverage (which test
covers which code) for identifying gaps. Add coverage diff mode for
CI that shows coverage changes.

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
