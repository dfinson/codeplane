# assertj/assertj

| Field | Value |
|-------|-------|
| **URL** | https://github.com/assertj/assertj |
| **License** | Apache-2.0 |
| **Language** | Java |
| **Scale** | Large |
| **Category** | Fluent assertions for Java |
| **Set** | Cutoff |
| **Commit** | `5cc365bb41b583db9e91bf010616a711a5e21495` |

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

33 tasks (11 narrow, 11 medium, 11 wide).

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

### N2: Fix AbstractIterableAssert.usingComparatorForType not applying when element comparator is already set

In `assertj-core/src/main/java/org/assertj/core/api/AbstractIterableAssert.java`,
the `usingComparatorForType(Comparator, Class)` method only installs a
wrapping `ExtendedByTypesComparator` when `iterables.getComparator() == null`.
If the user previously called `usingElementComparator(existingCmp)` (making
`iterables.getComparator()` non-null), a subsequent
`usingComparatorForType(newCmp, MyType.class)` call updates `comparatorsByType`
but does NOT update the `iterables` strategy, so `newCmp` is silently
ignored during element comparison. Fix `usingComparatorForType` to also
update the element comparison strategy when an `ExtendedByTypesComparator`
is already in use, by rebuilding it with the updated `comparatorsByType` map.

### N3: Fix TextDescription.value throwing on percent signs when extra args are provided

In `assertj-core/src/main/java/org/assertj/core/description/TextDescription.java`,
the `value()` method calls `org.assertj.core.util.Strings.formatIfArgs(value, args)`.
When `args` is non-null and non-empty, `formatIfArgs` calls
`value.formatted(args)` directly. If the user's description string contains
a literal `%` character not intended as a format specifier (for example
`describedAs("100% done", extraContext)`), the `%` is interpreted as the
start of a format conversion, throwing `java.util.UnknownFormatConversionException`.
Fix `Strings.formatIfArgs` in `assertj-core/src/main/java/org/assertj/core/util/Strings.java`
so that when args are present it escapes `%` signs not followed by valid
format-conversion characters before calling `.formatted(args)`, consistent
with the no-args branch that already calls `escapePercentExceptWhenFollowedBy_n`.

### N4: Add TimeUnit-based isCloseTo overloads to AbstractDateAssert

In `assertj-core/src/main/java/org/assertj/core/api/AbstractDateAssert.java`,
`isCloseTo` only accepts a delta in raw milliseconds via
`isCloseTo(Date other, long deltaInMilliseconds)`. There is no overload
accepting a `java.util.concurrent.TimeUnit`, forcing callers to convert
manually (e.g., `TimeUnit.SECONDS.toMillis(5)`). Add
`isCloseTo(Date other, long delta, TimeUnit unit)` and
`isCloseTo(String dateAsString, long delta, TimeUnit unit)` to
`AbstractDateAssert` that compute `unit.toMillis(delta)` and delegate to
`Dates.assertIsCloseTo` in `internal/Dates.java`. Validate that `delta`
is non-negative in the new overloads, throwing `IllegalArgumentException`
with a descriptive message when it is not.

### N5: Add no-descriptor factory to VerboseCondition that uses StandardRepresentation

In `assertj-core/src/main/java/org/assertj/core/condition/VerboseCondition.java`,
the only factory method `verboseCondition(Predicate<T>, String, Function<T, String>)`
requires callers to supply an explicit `objectUnderTestDescriptor` function even
when a simple default description (the standard `toString`-style representation)
would suffice. Add a two-argument overload
`verboseCondition(Predicate<T> predicate, String description)` to
`VerboseCondition` that defaults to using
`StandardRepresentation.STANDARD_REPRESENTATION.toStringOf(actual)` as the
object descriptor. Modify `buildVerboseDescription` in `VerboseCondition.java`
to fall back to `StandardRepresentation` when no explicit descriptor was
provided at construction time. No changes outside `VerboseCondition.java` and
`presentation/StandardRepresentation.java` are required.

### N6: Fix Offset.strictOffset Javadoc examples using wrong factory method and Numbers.assertIsCloseTo wrong file reference

In `assertj-core/src/main/java/org/assertj/core/data/Offset.java`, the
`strictOffset` method's Javadoc code examples mistakenly call `offset()`
(non-strict factory) in all three sample assertions instead of
`strictOffset()`, making the documented failure cases incorrect. For
example, `assertThat(8.1).isCloseTo(8.0, offset(0.1))` would pass
(non-strict `<=`), not fail as the example claims. Additionally,
`internal/Comparables.java` is sometimes incorrectly referenced for
`assertIsCloseTo` — the actual implementation lives in
`internal/Numbers.java`. Fix the Javadoc examples in `Offset.java` to use
`strictOffset()` and add a clarifying comment in
`Numbers.assertIsCloseTo` explaining why the early-return `areEqual` guard
correctly passes for strict offsets when `diff == 0` (since `0 < any positive strictOffset`).

### N7: Fix MappedCondition.toString not including the mapping function description

In `assertj-core/src/main/java/org/assertj/core/condition/MappedCondition.java`,
`MappedCondition` uses the no-arg `Condition()` constructor, which sets the
description to the simple class name `"MappedCondition"`. The `mappingDescription`
and inner `condition` description are only incorporated into `toString()` after
`matches()` is called, because the description is overwritten via
`describedAs(buildMappingDescription(...))` inside `matches`. When the
condition is inspected (e.g., logged or included in a compound condition) before
`matches()` has been called, `toString()` returns the uninformative class name.
Override `toString()` in `MappedCondition` to eagerly compose a meaningful
description from `mappingDescription` and the inner condition's description
without requiring `matches()` to be called first.

### N8: Fix DeepDifference treating two empty Optional values as different

In `assertj-core/src/main/java/org/assertj/core/internal/DeepDifference.java`,
the `deepEquals` method's recursive traversal does not have special
handling for `Optional.empty()`. Two `Optional.empty()` instances are
compared by field-level reflection, which may find internal
implementation differences across JDK versions. Fix `deepEquals` to
check for `Optional` and compare by `Optional.equals` semantics before
falling through to field-level comparison.

### N9: Fix AbstractCharSequenceAssert.containsIgnoringCase missing varargs overload

In `assertj-core/src/main/java/org/assertj/core/api/AbstractCharSequenceAssert.java`,
`containsIgnoringCase(CharSequence sequence)` accepts only a single value,
creating an asymmetry with `doesNotContainIgnoringCase(CharSequence... values)`,
which already accepts multiple values. There is no
`containsIgnoringCase(CharSequence... sequences)` varargs overload, forcing users
who need to assert that multiple substrings are present (ignoring case) to chain
multiple single-value calls instead of one fluent call. Add
`containsIgnoringCase(CharSequence... sequences)` to
`AbstractCharSequenceAssert` that calls
`Strings.assertContainsIgnoringCase(AssertionInfo, CharSequence, CharSequence...)`
in `internal/Strings.java`, adding that multi-value internal method alongside
the existing single-value `assertContainsIgnoringCase(AssertionInfo, CharSequence, CharSequence)`.
The existing single-value overload must remain unchanged.

### N10: Fix AbstractMapAssert.containsExactlyInAnyOrderEntriesOf not reporting value mismatches for same keys

In `assertj-core/src/main/java/org/assertj/core/api/AbstractMapAssert.java`,
`containsExactlyInAnyOrderEntriesOf(Map)` delegates to
`containsOnly(toEntries(map))` which calls `Maps.assertContainsOnly` in
`internal/Maps.java`. When the actual and expected maps share a key but
differ on its value (e.g., actual has `{a=1}` and expected has `{a=2}`),
`assertContainsOnly` reports the expected entry `<a=2>` as "not found" and
actual entry `<a=1>` as "not expected" rather than a targeted value
mismatch. Fix `Maps.assertContainsOnly` to detect entries whose keys match
but whose values differ and report them as value mismatches separately from
genuinely missing or unexpected entries.

## Medium

### M1: Implement JSON-path assertions for AbstractStringAssert

Add `jsonPath(String path)` and `jsonPathValue(String path, Object expected)`
methods to `AbstractStringAssert` that parse the string as JSON and
evaluate a JSONPath expression. Requires a new `AbstractJsonAssert` class
in `api/`, JSONPath evaluation logic in `internal/Json.java` using the
library's existing comparison infrastructure, error message factories
in `error/ShouldHaveJsonPath.java` and `error/ShouldHaveJsonPathValue.java`,
and integration into `AbstractStringAssert` for fluent chaining. Also
add the JSONPath library dependency (`com.jayway.jsonpath:json-path`)
to `assertj-core/pom.xml` as an `<optional>true</optional>` dependency
and update `assertj-bom/pom.xml` to include it in the BOM.

### M2: Add temporal assertion support for java.time.MonthDay

Implement `AbstractMonthDayAssert` for `java.time.MonthDay` with
assertions: `isBefore(MonthDay)`, `isAfter(MonthDay)`, `isBetween(MonthDay,
MonthDay)`, `hasMonth(Month)`, `hasDayOfMonth(int)`, and `isIn(MonthDay...)`.
Requires a new `MonthDayAssert.java` and `AbstractMonthDayAssert.java` in
`api/`, a `MonthDays.java` comparison helper in `internal/`, error message
factories in `error/` (e.g., `ShouldBeBeforeMonthDay.java`), an
`assertThat(MonthDay)` entry point in `Assertions.java` and
`BDDAssertions.java`, and `SoftAssertions` support via
`WithAssertions.java`.

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

### M5: Add block-level representation configuration to SoftAssertions

`AbstractSoftAssertions` has no mechanism for setting a default
`Representation` for all assertions created within a soft assertions block.
Users must either call the global `AbstractAssert.setCustomRepresentation()`
(which affects every thread) or chain `.withRepresentation(r)` on every
individual assertion. Add `withRepresentation(Representation)` to
`AbstractSoftAssertions` that configures the representation for all
proxy-generated assertion instances within the block. Requires changes to
`AbstractSoftAssertions.java` to store the representation and pass it to
newly created proxies, `SoftProxies.java` to apply the configured
representation when creating proxy instances via `WritableAssertionInfo`,
`WritableAssertionInfo.java` for representation propagation, and
`SoftAssertionsProvider.java` to expose the new method through the
interface. The existing global `AbstractAssert.setCustomRepresentation()`
must remain unchanged.

### M6: Add Consumer-based message assertion to AbstractThrowableAssert

`AbstractThrowableAssert` and `ThrowableAssertAlternative` have no method
that passes the exception message to an arbitrary `Consumer<String>` for
flexible message assertions, forcing users to call `.hasMessage(exact)`,
`.hasMessageContaining(sub)`, or to manually extract the message.
Add `hasMessageSatisfying(Consumer<String> messageRequirements)` to
`AbstractThrowableAssert` that passes the actual exception message (or
fails if the message is null) to the consumer. Add the corresponding
`withMessageSatisfying(Consumer<String> messageRequirements)` to
`ThrowableAssertAlternative`. Requires changes to
`AbstractThrowableAssert.java`, `ThrowableAssertAlternative.java`,
`internal/Throwables.java` for the null-message guard, and a new
`error/ShouldHaveMessageSatisfyingRequirements.java` factory for the
null-message failure case.

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

### N11: Fix assertj-bom/pom.xml not managing the Guava transitive dependency version

The `assertj-bom/pom.xml` manages `assertj-core` and `assertj-guava` versions
but does not manage their shared transitive dependency `com.google.guava:guava`.
Projects that import the AssertJ BOM and also declare a direct Guava dependency
may see version resolution conflicts because the BOM provides no guidance on the
Guava version. Add `com.google.guava:guava` at version `33.5.0-jre` (matching
the version declared in `assertj-guava/pom.xml`) to the `<dependencyManagement>`
section of `assertj-bom/pom.xml`. Also add an XML comment block before the
`<dependencyManagement>` element explaining the criteria for including a
dependency in the BOM.

### M11: Improve binary compatibility checking and add release documentation

The `.github/workflows/binary-compatibility.yml` workflow exists and the
`assertj-parent/pom.xml` `japicmp-maven-plugin` configuration already has
`<onlyModified>true</onlyModified>`, but it is missing `<excludes>` patterns
for internal implementation packages. Classes in `org.assertj.core.internal.*`
are not part of the public API surface but are currently included in
compatibility reports, producing false-positive failures when internal details
change. Add an `<excludes>` configuration block to the `japicmp-maven-plugin`
in `assertj-parent/pom.xml` to exclude `org.assertj.core.internal.*` and any
other non-public packages from binary compatibility checks. Also add a comment
in the `binary-compatibility.yml` workflow explaining the exclusion rationale.
Finally, add a `RELEASING.md` document at the repository root describing the
release process, version-bump procedure, and compatibility verification steps.

### W11: Overhaul Maven build, CI workflows, migration scripts, and documentation

Comprehensively update all non-code project files for the AssertJ 4.x
release. Update the root `pom.xml` to add `<requireMavenVersion>` (minimum
3.8.8) and `<requireJavaVersion>` (minimum 17) rules to the existing
`maven-enforcer-plugin` configuration so users get clear errors when their
toolchain is incompatible. Update `.github/workflows/main.yml` to add an LTS
JDK matrix job running Java 17 and 21 (in addition to the existing early-access
matrix for 26/27/loom/leyden/valhalla), enabling verification on long-term
support releases. Update the migration scripts in `scripts/`
(`convert-junit-assertions-to-assertj.sh`,
`convert-junit5-assertions-to-assertj.sh`) to add support for JUnit
5.10+ assertion patterns (e.g., `assertInstanceOf`, multi-headed `assertAll`
lambdas) and add a new `convert-hamcrest-to-assertj.sh` script that converts
the most common Hamcrest `assertThat(x, matcher)` patterns to AssertJ
equivalents. Update `README.md` to add a migration guide from AssertJ 3.x to
4.x covering module renames, removed methods, and new features. Update
`CONTRIBUTING.md` with the updated build process, test naming conventions,
and PR review checklist.
