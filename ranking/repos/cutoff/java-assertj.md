# assertj/assertj

| Field | Value |
|-------|-------|
| **URL** | https://github.com/assertj/assertj |
| **License** | Apache-2.0 |
| **Language** | Java |
| **Scale** | Large |
| **Category** | Fluent assertions for Java |
| **Set** | Cutoff |
| **Commit** | `9a79aeb6f27683917012432650d6af4fc0572189` |

## Why this repo

- **Well-structured**: Multi-module Maven project — `assertj-core` (the main
  library with 800+ source files across packages: `api` for fluent assertion
  classes, `internal` for comparison/validation logic, `error` for error
  message factories, `condition` for composable conditions, `configuration`
  for global settings, `data` for value types like `Index`, `Offset`,
  `Percentage`, `presentation` for representation formatting, `extractor`
  for property/field extraction, and `util` for shared helpers),
  `assertj-guava` (Guava-specific assertions), `assertj-tests`,
  `assertj-bom`, and `assertj-parent` (Maven parent POM).
- **Rich history**: 2.6K+ stars, long-lived project. The de facto fluent
  assertion library for Java, used by thousands of projects. Issues span
  recursive comparison edge cases, soft assertion thread safety, custom
  comparator handling, and error message formatting.
- **Permissive**: Apache-2.0 licensed.

## Structure overview

```
assertj/
├── pom.xml                                            # Root Maven POM (multi-module)
├── assertj-core/
│   └── src/main/java/org/assertj/core/
│       ├── api/
│       │   ├── Assertions.java                        # Static entry point — assertThat() overloads for all types
│       │   ├── BDDAssertions.java                     # BDD-style then() entry points
│       │   ├── AbstractAssert.java                    # Base class — describedAs, isEqualTo, isNotNull, satisfies, etc.
│       │   ├── AbstractStringAssert.java              # String assertions — contains, matches, startsWith, isBlank
│       │   ├── AbstractIterableAssert.java            # Iterable assertions — containsExactly, filteredOn, extracting
│       │   ├── AbstractMapAssert.java                 # Map assertions — containsEntry, hasSize, containsKey
│       │   ├── AbstractComparableAssert.java          # Comparable assertions — isGreaterThan, isBetween
│       │   ├── AbstractOptionalAssert.java            # Optional assertions — isPresent, isEmpty, hasValue
│       │   ├── AbstractFutureAssert.java              # Future/CompletableFuture assertions
│       │   ├── AbstractPathAssert.java                # Path/File assertions — exists, hasContent, isDirectory
│       │   ├── AbstractDateAssert.java                # Date assertions — isBefore, isAfter, isCloseTo
│       │   ├── AbstractThrowableAssert.java           # Throwable assertions — hasMessage, hasCause, isInstanceOf
│       │   ├── SoftAssertions.java                    # Collects multiple assertion failures before reporting
│       │   ├── Assumptions.java                       # JUnit assumption integration
│       │   ├── AssertFactory.java                     # Factory interface for custom assertion creation
│       │   ├── comparisonstrategy/                    # ComparisonStrategy, ComparatorBased, IterableElement strategies
│       │   ├── exception/                             # Path/file-specific exception assertions
│       │   ├── filter/                                # Filters, FilterOperator, InFilter, NotFilter, NotInFilter
│       │   ├── iterable/                              # Iterable-specific assertion helpers
│       │   ├── junit/                                 # JUnit 5 SoftAssertionsExtension
│       │   └── recursive/
│       │       ├── assertion/                         # RecursiveAssertionConfiguration for deep assertion
│       │       └── comparison/                        # RecursiveComparisonConfiguration, field-by-field comparison
│       ├── internal/
│       │   ├── Objects.java                           # Core comparison logic — assertEqual, assertNotEqual, etc.
│       │   ├── Iterables.java                         # Iterable comparison — containsExactly, containsOnly, etc.
│       │   ├── Strings.java                           # String comparison — contains, matches, normalizing whitespace
│       │   ├── Maps.java                              # Map comparison — containsEntry, containsKeys
│       │   ├── Comparables.java                       # Comparable comparison — assertGreaterThan, assertBetween
│       │   ├── Arrays.java                            # Array comparison helpers
│       │   ├── Dates.java                             # Date comparison helpers
│       │   ├── Paths.java                             # Path/File comparison helpers
│       │   ├── Failures.java                          # Failure factory — creates AssertionError with description
│       │   ├── DeepDifference.java                    # Deep recursive object diff computation
│       │   ├── TypeComparators.java                   # Per-type comparator map
│       │   ├── BinaryDiff.java                        # Binary file diff
│       │   ├── Conditions.java                        # Condition evaluation logic
│       │   └── Futures.java                           # CompletableFuture assertion logic
│       ├── error/
│       │   ├── BasicErrorMessageFactory.java          # Base error message formatting
│       │   ├── ShouldBeEqual.java                     # Error: "expected X but was Y"
│       │   ├── ShouldContain.java                     # Error: "expected to contain X"
│       │   ├── ShouldMatch.java                       # Error: "expected to match predicate"
│       │   ├── AssertionErrorCreator.java             # Creates AssertionError / MultipleFailuresError
│       │   ├── DescriptionFormatter.java              # Formats assertion description prefix
│       │   └── MessageFormatter.java                  # Formats error messages with representation
│       ├── condition/
│       │   ├── AllOf.java                             # Composite condition: all conditions must match
│       │   ├── AnyOf.java                             # Composite condition: any condition matches
│       │   ├── Not.java                               # Negated condition
│       │   ├── MappedCondition.java                   # Condition mapped via function
│       │   └── VerboseCondition.java                  # Condition with verbose description on failure
│       ├── configuration/
│       │   ├── Configuration.java                     # Global config — representation, comparison settings
│       │   ├── ConfigurationProvider.java             # SPI-based configuration loading
│       │   └── PreferredAssumptionException.java      # Configurable assumption exception type
│       ├── data/
│       │   ├── Index.java                             # Positional index for array/list assertions
│       │   ├── Offset.java                            # Numeric offset for closeTo assertions
│       │   ├── Percentage.java                        # Percentage value for closeTo assertions
│       │   ├── MapEntry.java                          # Type-safe map entry for containsEntry
│       │   └── TemporalUnitOffset.java                # Temporal offset for date/time assertions
│       ├── description/
│       │   └── Description.java                       # Assertion description abstraction
│       ├── extractor/
│       │   └── Extractors.java                        # Property/field extractor utilities
│       ├── presentation/
│       │   ├── Representation.java                    # Interface for custom value representation
│       │   ├── StandardRepresentation.java            # Default toString representation
│       │   └── PredicateDescription.java              # Description for predicate-based assertions
│       └── util/
│           ├── Lists.java                             # List utility methods
│           ├── Strings.java                           # String utility methods
│           ├── IterableUtil.java                      # Iterable utility methods
│           └── Preconditions.java                     # Argument validation utilities
├── assertj-guava/                                     # Guava-specific assertions (Multimap, Table, etc.)
├── assertj-tests/                                     # Integration and cross-module tests
├── assertj-bom/                                       # Bill of Materials POM
└── assertj-parent/                                    # Parent POM with shared configuration
```

## Scale indicators

- ~5 modules, ~821 Java source files in assertj-core alone
- ~159,000 lines of library code
- Maven multi-module structure with parent POM
- Dependencies: minimal (JUnit 5 `opentest4j` for `MultipleFailuresError`)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix SoftAssertions not collecting errors from recursive comparison assertions

In `assertj-core/src/main/java/org/assertj/core/api/SoftAssertions.java`,
when using `assertThat(obj).usingRecursiveComparison().isEqualTo(expected)`
inside a `SoftAssertions` block, the recursive comparison failure is thrown
immediately instead of being collected. The `AbstractAssert.isEqualTo` path
for recursive comparison bypasses the soft assertion proxy error collection
in `AbstractSoftAssertions`. Fix the recursive comparison flow in
`AbstractAssert` to route failures through `SoftAssertions`'s
`AssertionErrorCollector` instead of throwing directly via `Failures`.

### N2: Fix AbstractIterableAssert.extracting losing custom comparator after extraction

In `assertj-core/src/main/java/org/assertj/core/api/AbstractIterableAssert.java`,
calling `usingComparatorForType(cmp, MyType.class)` followed by
`extracting(MyType::getField)` creates a new `ListAssert` that does not
propagate the registered type comparators from `TypeComparators`. The
extracted assertion uses default `equals` comparison. Fix `extracting` to
propagate the parent assertion's `TypeComparators` map to the new
`ListAssert` returned.

### N3: Fix DescriptionFormatter not escaping percent signs in user-provided descriptions

In `assertj-core/src/main/java/org/assertj/core/error/DescriptionFormatter.java`,
the `format` method uses `String.format` internally. When users provide
assertion descriptions containing literal `%` characters via `describedAs("100% done")`,
the formatter throws `java.util.UnknownFormatConversionException`. Fix
`format` to escape `%` characters in the description text before passing
to `String.format`.

### N4: Fix AbstractDateAssert.isCloseTo not respecting custom date comparator

In `assertj-core/src/main/java/org/assertj/core/api/AbstractDateAssert.java`,
the `isCloseTo(Date, long, TimeUnit)` method delegates to
`Dates.assertIsCloseTo` in `internal/Dates.java`, which computes the
difference using `Date.getTime()` arithmetic directly. When a custom
comparator is registered via `usingComparator`, it is ignored for the
closeness check. Fix `assertIsCloseTo` in `Dates.java` to use the
registered comparator's comparison semantics when determining temporal
proximity.

### N5: Fix VerboseCondition not including the mapped value in the failure description

In `assertj-core/src/main/java/org/assertj/core/condition/VerboseCondition.java`,
when the wrapped condition fails, the error message shows only the
condition description but not the actual value that was tested. The
`describedAs` method on the inner condition is not updated with the
actual value's representation. Fix `VerboseCondition.matches` to include
the `StandardRepresentation` of the tested value in the failure
description.

### N6: Fix Offset.strictOffset not failing when values are exactly equal

In `assertj-core/src/main/java/org/assertj/core/data/Offset.java`, the
`strictOffset` factory method creates an offset that should fail when the
difference is exactly zero (strict inequality). However, the comparison
in `internal/Comparables.java` method `assertIsCloseTo` uses `<=` for
strict offsets instead of `<`. Fix the comparison operator used when
the offset is strict to properly reject zero-difference values.

### N7: Fix MappedCondition.toString not including the mapping function description

In `assertj-core/src/main/java/org/assertj/core/condition/MappedCondition.java`,
calling `toString()` on a `MappedCondition` returns only the inner
condition's description, omitting the mapping function name passed to
the constructor. When this condition is used in `has(mappedCondition)`,
the error message does not indicate what mapping was applied. Fix
`toString` and `descriptionText` to include the mapping description
provided at construction time.

### N8: Fix DeepDifference treating two empty Optional values as different

In `assertj-core/src/main/java/org/assertj/core/internal/DeepDifference.java`,
the `deepEquals` method's recursive traversal does not have special
handling for `Optional.empty()`. Two `Optional.empty()` instances are
compared by field-level reflection, which may find internal
implementation differences across JDK versions. Fix `deepEquals` to
check for `Optional` and compare by `Optional.equals` semantics before
falling through to field-level comparison.

### N9: Fix AbstractStringAssert.containsPattern not supporting Pattern flags

In `assertj-core/src/main/java/org/assertj/core/api/AbstractStringAssert.java`,
`containsPattern(String regex)` compiles the pattern without flags. When
users need case-insensitive matching, they must use inline flags
`(?i)` in the pattern string. The overloaded `containsPattern(Pattern)`
variant is missing. Fix by adding a `containsPattern(java.util.regex.Pattern)`
overload that accepts a pre-compiled pattern with flags, delegating to
`Strings.assertContainsPattern` in `internal/Strings.java`.

### N10: Fix AbstractMapAssert.containsExactlyInAnyOrderEntriesOf not detecting duplicate keys

In `assertj-core/src/main/java/org/assertj/core/api/AbstractMapAssert.java`,
`containsExactlyInAnyOrderEntriesOf(Map)` delegates to `Maps.assertContainsExactlyInAnyOrder`
in `internal/Maps.java`. If the expected map and actual map both contain
the same key with different values, and the actual map has additional
entries, the error message reports "missing entries" but not the value
mismatch. Fix `assertContainsExactlyInAnyOrder` to separately report
entries with matching keys but differing values.

## Medium

### M1: Implement JSON-path assertions for AbstractStringAssert

Add `jsonPath(String path)` and `jsonPathValue(String path, Object expected)`
methods to `AbstractStringAssert` that parse the string as JSON and
evaluate a JSONPath expression. Requires a new `AbstractJsonAssert` class
in `api/`, JSONPath evaluation logic in `internal/Json.java` using the
library's existing comparison infrastructure, error message factories
in `error/ShouldHaveJsonPath.java` and `error/ShouldHaveJsonPathValue.java`,
and integration into `AbstractStringAssert` for fluent chaining.

### M2: Add temporal assertion support for java.time.YearMonth

Implement `AbstractYearMonthAssert` with assertions: `isBefore`,
`isAfter`, `isBetween`, `hasYear`, `hasMonth`, and `isIn`. Requires
a new assert class in `api/`, a `YearMonths.java` comparison helper
in `internal/`, error message factories in `error/`, an
`assertThat(YearMonth)` entry point in `Assertions.java` and
`BDDAssertions.java`, and `SoftAssertions` support.

### M3: Implement assertion chaining with transforming extractors

Add `extracting(Function, AssertFactory)` to `AbstractObjectAssert`
that extracts a value and returns a typed assertion for the extracted
value, preserving the assertion chain's description. Requires changes
to `AbstractObjectAssert.java`, `AbstractAssert.java` for description
propagation, a new `TransformingAssertFactory` in `api/`, integration
with `SoftAssertions` proxy generation, and error message updates in
`error/`.

### M4: Add Condition combinators with short-circuit evaluation

Implement `AllOfEager` and `AnyOfEager` condition variants in
`condition/` that evaluate all sub-conditions even after the first
failure/success, collecting all results for the error message. Requires
`AllOfEager.java` and `AnyOfEager.java` in `condition/`, integration
with `Conditions.java` in `internal/`, a `conditionResults` field on
the error factories in `error/ElementsShouldBe.java` and
`error/ElementsShouldHave.java`, and static factory methods in
`Assertions.java`.

### M5: Implement custom representation per assertion instance

Add `withRepresentation(Representation)` to `AbstractAssert` that
overrides the global representation for a single assertion chain.
Requires changes to `AbstractAssert.java` to store an instance-level
`Representation`, propagation through `Failures.java` and
`MessageFormatter.java` in `error/`, `DescriptionFormatter.java`
updates, recursive comparison support in `RecursiveComparisonConfiguration`,
and `SoftAssertions` proxy support.

### M6: Add fluent exception cause chain assertions

Implement `assertThatExceptionOfType(Class).isThrownBy(ThrowingCallable).havingRootCause()`
that navigates to the root cause and returns a new
`AbstractThrowableAssert` for it. Requires changes to
`AbstractThrowableAssert.java` for `havingRootCause()`, a
`RootCause.java` helper in `internal/`, error messages in
`error/ShouldHaveRootCause.java`, and integration with
`ThrowableAssertAlternative` in `api/`.

### M7: Implement iterable assertion windowing for sliding comparisons

Add `window(int size)` to `AbstractIterableAssert` that transforms
the iterable into a list of overlapping sub-lists for sliding window
assertions. Requires changes to `AbstractIterableAssert.java`,
a `Windows.java` utility in `internal/` for sliding window extraction,
`AbstractListAssert` integration for window result, error message
factories for window-specific failures, and support in `SoftAssertions`.

### M8: Add AbstractPathAssert support for comparing directory trees

Implement `hasDirectoryContentEqualTo(Path other)` on `AbstractPathAssert`
that recursively compares two directory trees by file names and
contents. Requires `Paths.java` additions in `internal/` for recursive
tree comparison, `BinaryDiff` integration for content comparison,
error messages in `error/ShouldHaveDirectoryContent.java` listing
missing/extra/differing files, and a new entry method in
`AbstractPathAssert.java`.

### M9: Implement assertion scope with custom failure collector

Add `AssertionScope.open(Consumer<List<AssertionError>>)` that creates
a scoped assertion context where all failures are collected and passed
to a custom consumer. Requires a new `AssertionScope` class in `api/`,
a thread-local collector stack in `AssertionErrorCollector`, integration
with `Failures.java` to check for active scopes, and cleanup logic
using `AutoCloseable`.

### M10: Add numeric assertion support for statistical measures

Implement `hasStandardDeviation(double, Offset)` and `hasMean(double, Offset)`
on `AbstractIterableAssert<Number>` for asserting statistical properties
of numeric collections. Requires `Statistics.java` in `internal/` for
mean/stddev computation, error messages in `error/ShouldHaveMean.java`
and `error/ShouldHaveStandardDeviation.java`, type-safe overloads in
`AbstractIterableAssert`, and `Offset`/`Percentage` support in the
comparison.

## Wide

### W1: Implement a custom assertion generator from annotated domain classes

Add an annotation processor that generates type-safe assertion classes
from domain classes annotated with `@GenerateAssertion`. Produces
`XxxAssert` classes with `hasFieldName()` methods for each getter.
Changes span a new `assertj-generator` module with
`GenerateAssertion.java` annotation, `AssertionAnnotationProcessor.java`,
Velocity/JavaPoet templates for code generation, Maven plugin
configuration, integration tests, and root POM module declaration.

### W2: Implement database result set assertions

Add `assertj-db` module for JDBC `ResultSet` assertions: `hasRows(int)`,
`hasColumns(String...)`, `row(int).hasValue(String, Object)`, and
`containsExactlyRows(...)`. Changes span a new module with
`ResultSetAssert.java`, `RowAssert.java`, `AbstractResultSetAssert.java`
in `api/`, `ResultSets.java` in `internal/`, error factories,
`DataSource`-based connection management, soft assertion support, and
Maven module configuration.

### W3: Implement Kotlin-idiomatic assertion extensions

Create `assertj-kotlin` module providing Kotlin extension functions:
`T.shouldBe()`, infix assertions, reified `assertThat<T>()`, coroutine
support for `CompletableDeferred` assertions, and `SoftAssertions` DSL
via Kotlin's type-safe builders. Changes span a new Kotlin module with
`KotlinAssertions.kt`, extension functions for all core types, Kotlin
coroutine integration, Gradle/Maven dual build support, and
cross-module tests.

### W4: Implement property-based testing integration

Add `assertj-property` module that combines AssertJ assertions with
property-based testing: `forAll(gen).assertThat { ... }` with
shrinking support on failure. Changes span a new module with
`PropertyAssertions.java`, `Generator.java` for value generation,
`Shrinker.java` for counterexample minimization, `ForAll.java`
fluent API, integration with `SoftAssertions` for multi-property
checks, JUnit 5 extension, and reporting with seed reproduction.

### W5: Implement snapshot/approval testing for complex objects

Add `assertj-snapshot` module providing `assertThat(obj).matchesSnapshot()`
that serializes objects to JSON/YAML and compares against stored
snapshots. Changes span a new module with `SnapshotAssert.java`,
`SnapshotManager.java` for file I/O, JSON serialization using
`Representation`, diff visualization, snapshot update mode via
system property, recursive comparison integration, directory
structure for snapshot storage, and Maven module configuration.

### W6: Implement async/reactive assertion support for Project Reactor and RxJava

Add `assertj-reactive` module with `StepVerifier`-style assertions:
`assertThat(Mono/Flux).emitsExactly(...)`, `completesWith(...)`,
`errorsWithMessage(...)`. Changes span a new module with
`FluxAssert.java`, `MonoAssert.java`, `ObservableAssert.java`,
internal schedulers for test subscription, timeout handling,
error message factories, backpressure assertion support, and
integration tests with virtual time.

### W7: Implement assertion diff visualization with ANSI and HTML output

Add a `DiffRenderer` system that generates side-by-side or unified
diffs for failed assertions with syntax highlighting. Changes span
`presentation/DiffRenderer.java`, `presentation/AnsiDiffFormatter.java`,
`presentation/HtmlDiffFormatter.java`, integration into
`MessageFormatter.java` for automatic diff attachment,
`Configuration.java` for diff mode selection, `DeepDifference.java`
for structural diff tree generation, and `RecursiveComparisonConfiguration`
for field-level diff rendering.

### W8: Implement OpenAPI schema validation assertions

Add `assertj-openapi` module for validating API responses against
OpenAPI specifications: `assertThat(response).conformsToSchema(spec, path)`.
Changes span a new module with `OpenApiAssert.java`,
`SchemaValidator.java` for JSON Schema validation, `SpecParser.java`
for OpenAPI 3.x parsing, error factories for schema violations with
path information, integration with `AbstractStringAssert` for JSON
responses, and Maven module configuration.

### W9: Implement fluent assertions for graph data structures

Add assertions for graph/tree structures:
`assertThat(graph).hasPath(from, to)`, `isAcyclic()`, `hasEdge()`,
`connectedComponents().hasSize(n)`. Changes span new assertion classes
`GraphAssert.java`, `TreeAssert.java` in `api/`, graph traversal
algorithms in `internal/Graphs.java` (BFS, DFS, cycle detection,
connected components), tree-specific logic in `internal/Trees.java`,
error factories for graph-specific failures, and `Assertions.java`
entry points.

### W10: Implement assertion migration tool from Hamcrest to AssertJ

Build a source-code transformation tool that converts Hamcrest
`assertThat(x, is(y))` patterns to AssertJ `assertThat(x).isEqualTo(y)`.
Changes span a new `assertj-migration` module with a JavaParser-based
AST transformer, pattern matching for common Hamcrest matchers
(`is`, `equalTo`, `hasSize`, `containsString`, `nullValue`, `not`),
import rewriting, Maven/Gradle plugin for bulk migration, dry-run
mode with diff output, and integration tests with sample Hamcrest
test files.
