# catchorg/Catch2

| Field | Value |
|-------|-------|
| **URL** | https://github.com/catchorg/Catch2 |
| **License** | BSL-1.0 |
| **Language** | C++ |
| **Scale** | Medium |
| **Category** | C++ test framework |
| **Set** | eval |
| **Commit** | `0ad9824bc644fbc4c0c1226340a04f0ded7919de` |

## Why this repo

- **Multi-subsystem**: Test case registry, assertion macros/decomposer, matchers, generators, benchmarking, reporters (console/JUnit/JSON/TAP/XML), CLI parsing, output capture, sharding
- **Well-structured**: Clear separation between internal/, matchers/, reporters/, generators/, benchmark/, interfaces/
- **Rich history**: 19K+ stars, widely used C++ test framework with header-only heritage and modular v3 architecture

## Structure overview

```
src/catch2/
├── internal/            # Core engine: registry, runner, decomposer, CLI, text formatting
│   ├── catch_run_context.*        # Test case execution
│   ├── catch_test_case_registry_impl.*  # Test registration
│   ├── catch_decomposer.*         # Expression decomposition for assertions
│   ├── catch_section.*            # SECTION nesting
│   ├── catch_test_case_tracker.*  # Section/test tracking
│   ├── catch_xmlwriter.*          # XML output
│   ├── catch_jsonwriter.*         # JSON output
│   ├── catch_textflow.*           # Line-wrapping text formatter
│   ├── catch_sharding.*           # Test sharding
│   ├── catch_output_redirect.*    # stdout/stderr capture
│   └── catch_clara.*              # CLI argument parser
├── matchers/            # Matcher framework: string, float, container, predicate, range
├── reporters/           # Console, JUnit, JSON, TAP, SonarQube, compact, automake
├── generators/          # Data generators for property-based testing
├── benchmark/           # Micro-benchmarking engine with statistical analysis
├── interfaces/          # Abstract interfaces: reporter, listener, config, registry
```

## Scale indicators

- ~410 C++ source files (headers + implementations)
- ~74K lines of code
- Multiple reporter backends and assertion types
- Cross-cutting section tracking and test case registry

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `SECTION` nesting depth not reported in console reporter on failure

When an assertion fails inside a deeply nested `SECTION` in `internal/catch_section.cpp`, the console reporter in `reporters/catch_reporter_console.cpp` only prints the innermost section name. The full section path (parent > child > grandchild) should be displayed to help locate the failure.

### N2: Fix `Approx` matcher not handling subnormal floating-point values correctly

In `matchers/catch_matchers_floating_point.cpp`, the `WithinAbsMatcher` compares using absolute epsilon but does not account for subnormal float values where the default epsilon is larger than the values themselves, causing false positives for comparisons near zero.

### N3: Add `CATCH_CONFIG_DISABLE_BENCHMARKS` compile-time flag

The benchmark subsystem in `benchmark/catch_benchmark.hpp` is always compiled even when not used. Add a preprocessor flag in `internal/catch_compiler_capabilities.hpp` that disables benchmark compilation, reducing binary size for projects that only need test assertions.

### N4: Fix JSON reporter not escaping Unicode control characters

In `internal/catch_jsonwriter.cpp`, the JSON string escaping handles quotes and backslashes but does not escape Unicode control characters (U+0000–U+001F) as required by the JSON specification. Characters like `\t` and `\n` embedded in test names produce invalid JSON output.

### N5: Fix `StringRef` comparison not being `constexpr` when possible

In `internal/catch_stringref.hpp`, the `operator==` for `StringRef` performs a length check followed by `std::memcmp`, but neither is marked `constexpr`. Since C++17 allows `constexpr` string operations, the comparison should be `constexpr` for compile-time test name deduplication.

### N6: Add wildcard negation support in test spec tag expressions

The test spec parser in `internal/catch_test_spec_parser.cpp` supports `[tag]` inclusion and `~[tag]` exclusion, but does not support negated wildcard patterns like `~[slow*]` to exclude all tags matching a prefix. The `WildcardPattern` in `internal/catch_wildcard_pattern.hpp` only handles positive matches.

### N7: Fix `TextFlow::Column` not handling embedded ANSI escape codes in width calculation

In `internal/catch_textflow.cpp`, the `Column` text wrapper calculates line width by counting characters, but ANSI color codes (e.g., `\033[31m`) are counted as visible characters, causing lines to wrap too early when the console reporter uses colored output.

### N8: Fix `GENERATE` macro not deterministic across platforms with same seed

In `generators/catch_generators.cpp`, the `GeneratorTracker` uses `std::mt19937` but the generator index tracking varies across platforms due to different `SECTION` evaluation orders, causing `GENERATE(random(...))` to produce different sequences on Linux vs macOS with the same `--rng-seed`.

### N9: Add elapsed time display to TAP reporter output

The TAP reporter in `reporters/catch_reporter_tap.cpp` outputs `ok`/`not ok` lines but does not include execution time for individual test cases. Add `# time=<ms>` directives after each test line, following the TAP 13 specification for timing metadata.

### N10: Fix `catch_sharding.hpp` not distributing tests evenly when count is not divisible by shard count

In `internal/catch_sharding.hpp`, the `createShard` function uses modulo-based distribution that can produce shards with significantly different test counts when the total is not evenly divisible. The last shard may get up to `shard_count - 1` fewer tests than others.

## Medium

### M1: Implement parametrized test case registration

Add `TEST_CASE_P(name, generator)` macro that registers a test case once but runs it for each value produced by a generator. This requires modifying `internal/catch_test_case_registry_impl.cpp` to support parameterized registration, adding parameter display in test names, and ensuring each parameter run is reported independently in all reporters.

### M2: Add `REQUIRE_THROWS_MATCHES` with exception matcher chaining

Implement matcher composition for exception assertions: `REQUIRE_THROWS_MATCHES(expr, ExType, HasMessage("x") && HasCode(42))`. Requires extending the exception matchers in `matchers/catch_matchers_exception.hpp`, adding `HasCode` matcher, and modifying the decomposer in `internal/catch_decomposer.hpp` to support matcher conjunction on exception types.

### M3: Implement test dependency declarations with ordering

Add `TEST_CASE("b", "[depends:a]")` syntax that declares test "b" depends on test "a" completing successfully. Requires modifying the tag parser in `internal/catch_test_spec_parser.cpp`, the test case registry ordering in `internal/catch_test_case_registry_impl.cpp`, and the run context in `internal/catch_run_context.cpp` to skip dependents when their prerequisite fails.

### M4: Add structured key-value output to benchmark results

Extend `benchmark/detail/catch_benchmark_stats.hpp` to support custom key-value metrics alongside timing data. Users should be able to call `BENCHMARK_METRIC("throughput_mbps", value)` within a benchmark, and all reporters should output these custom metrics alongside the standard mean/stddev/outliers.

### M5: Implement parallel section execution within a test case

Add `PARALLEL_SECTION("name")` that allows independent sections within a test case to execute concurrently using `std::thread`. Requires extending the section tracker in `internal/catch_test_case_tracker.cpp`, adding synchronization for assertion reporting in `internal/catch_run_context.cpp`, and ensuring reporters handle interleaved section results correctly.

### M6: Add JUnit reporter support for test suite properties and system-out

Extend `reporters/catch_reporter_junit.cpp` to emit `<properties>` elements from user-defined metadata and capture `<system-out>` / `<system-err>` per test case using the output redirect in `internal/catch_output_redirect.cpp`. Requires adding a metadata API accessible from test cases and integrating it with the JUnit XML writer.

### M7: Implement generator shrinking for property-based testing

Add shrinking support to generators in `generators/catch_generators.hpp`: when a `GENERATE`-based test fails, automatically find a minimal failing input by shrinking the generated value. Requires adding a `Shrinkable<T>` wrapper, a shrink loop in the generator tracker, and integration with the console reporter to display both original and shrunk values.

### M8: Add test retry on failure with configurable attempt limit

Implement `--retry-failed <N>` CLI option that re-runs failed tests up to N times before declaring them as failures. Requires modifying the CLI parser in `internal/catch_commandline.cpp`, the run context loop in `internal/catch_run_context.cpp`, and all reporters to annotate retried tests with attempt numbers and only report the final status.

### M9: Implement custom reporter plugin loading at runtime

Add `--load-reporter <path>` CLI option to dynamically load reporter shared libraries. Requires adding a plugin loader in `internal/`, extending the reporter registry in `internal/catch_reporter_registry.cpp` to support dynamic registration, defining a stable C ABI for reporter plugins, and modifying the CLI parser.

### M10: Add source location tracking for matchers in failure messages

When a matcher assertion fails, the error message shows the expression but not where the matcher was defined. Extend `matchers/catch_matchers.hpp` to capture `std::source_location` at matcher construction, and modify the assertion handler in `internal/catch_assertion_handler.cpp` to include the matcher definition site in the failure output.

## Wide

### W1: Implement snapshot testing framework

Add `CHECK_SNAPSHOT(name, value)` macro that compares serialized values against stored reference files, with `--update-snapshots` flag to regenerate. Requires a new `snapshots/` subsystem for file I/O and diffing, integration with the assertion handler, modifications to all reporters for snapshot diff display, a CLI option in `internal/catch_commandline.cpp`, and a snapshot serialization protocol supporting custom types.

### W2: Add test coverage integration and reporting

Implement built-in coverage tracking that records which `SECTION` and `TEST_CASE` blocks are executed per source file. Add `--coverage-report <path>` CLI option producing an HTML or JSON report. Requires compiler-assisted instrumentation hooks in `internal/catch_run_context.cpp`, a coverage data store, a new coverage reporter in `reporters/`, and integration with the test case registry for mapping tests to source locations.

### W3: Implement distributed test execution with result aggregation

Add `--distribute <worker-count>` mode that splits tests across child processes, executes them in parallel, and aggregates results. Requires a coordinator in `internal/` that uses the sharding logic from `catch_sharding.hpp`, IPC for result collection, modifications to all reporters for merged output, handling of crashed workers, and a unified exit code based on aggregated results.

### W4: Add mocking framework integrated with Catch2 assertions

Implement `MOCK_METHOD(Class, method, ReturnType(Args...))` that creates mock objects verified through Catch2's assertion system. Requires a mock registry parallel to the test registry, expectation recording and verification in `internal/`, automatic mock verification at section/test end via the tracker, integration with matchers for argument matching, and failure reporting through the existing decomposer.

### W5: Implement test execution profiling and bottleneck detection

Add `--profile` mode that tracks timing for each test case, section, assertion, and generator step. Produce a flame-graph-compatible output and a summary of slowest tests. Requires timing instrumentation in `internal/catch_run_context.cpp`, section tracker, generator tracker, a profiling data model, a new profile reporter in `reporters/`, and CLI integration.

### W6: Add data-driven testing with external data sources

Implement `TEST_CASE_DATA("name", "data.csv")` and `TEST_CASE_DATA("name", "data.json")` that load test parameters from external files. Requires CSV/JSON parsers (extending `catch_jsonwriter` for reading), a data-driven test registrar in `internal/`, parameter injection into test functions, per-row reporting in all reporters, and `--data-dir` CLI option for base path configuration.

### W7: Implement fuzzing mode with corpus management

Add `FUZZ_TEST_CASE("name", Type)` that generates random inputs and tracks inputs that trigger new code paths or assertion failures. Requires a corpus manager for storing interesting inputs, mutation strategies in `generators/`, integration with sanitizers for crash detection, a fuzzing runner in `internal/`, progress reporting, and `--fuzz-duration` / `--fuzz-corpus-dir` CLI options.

### W8: Add hierarchical test organization with fixture inheritance

Implement `TEST_SUITE("suite")` blocks that group test cases with shared fixtures and nested sub-suites. Requires a suite registry parallel to test cases in `internal/`, fixture lifecycle management (setup/teardown per suite), modifications to all reporters for hierarchical output, tag inheritance from parent suites, and `--list-suites` CLI option.

### W9: Implement approval testing workflow

Add `APPROVE(value)` macro that on first run saves the output as approved baseline, and on subsequent runs compares against the approved file. Include `--approve` flag to accept current output, diff display in reporters, binary file support, and custom normalizers. Requires a new approval subsystem, file management, integration with the assertion handler, modifications to console/JSON/JUnit reporters for diff output, and CLI extensions.

### W10: Add cross-compilation test execution support

Implement `--crosscompile-run <command>` that executes tests on a remote target or emulator, collects results via structured output, and presents them locally. Requires splitting the runner into compile-side and execute-side components in `internal/`, a structured result protocol (extending JSON reporter), result deserialization and re-emission through local reporters, timeout handling for remote execution, and CLI integration for target configuration.
