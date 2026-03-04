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

8 tasks (3 narrow, 3 medium, 2 wide) for the C++ testing framework.

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
