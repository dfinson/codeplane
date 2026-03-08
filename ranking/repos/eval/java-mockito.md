# mockito/mockito

| Field | Value |
|-------|-------|
| **URL** | https://github.com/mockito/mockito |
| **License** | MIT |
| **Language** | Java |
| **Scale** | Large |
| **Category** | Mocking framework |
| **Set** | eval |
| **Commit** | `080ab96725a418f5a27eb3112d8ac7347f38afd8` |

## Why this repo

- **Deep internals**: Bytecode generation (ByteBuddy), invocation interception, argument matching, stubbing, verification
- **Multi-layer architecture**: Public API → internal handler → invocation → stubbing → creation (proxy generation)
- **Complex state machine**: Mock lifecycle spanning creation, stubbing, invocation recording, verification, and cleanup

## Structure overview

```
mockito-core/src/main/java/org/mockito/
├── Mockito.java              # Main entry point: mock(), when(), verify(), spy()
├── BDDMockito.java           # BDD-style given/then API
├── ArgumentMatchers.java     # Built-in matchers: any(), eq(), argThat()
├── ArgumentCaptor.java       # Capture arguments for assertion
├── MockedStatic.java         # Static method mocking (scoped)
├── MockedConstruction.java   # Constructor mocking (scoped)
├── InOrder.java              # Ordered verification interface
├── MockSettings.java         # Mock configuration: name, default answer, serializable
├── internal/
│   ├── handler/              # MockHandlerImpl: invocation dispatch, answer resolution
│   ├── stubbing/             # InvocationContainerImpl, StubbedInvocationMatcher, answers/
│   ├── verification/         # Times, AtLeast, AtMost, InOrderWrapper, checkers/
│   ├── invocation/           # InterceptedInvocation, InvocationMatcher, InvocationsFinder
│   ├── creation/             # ByteBuddy mock generation, proxy, settings
│   ├── matchers/             # MatcherApplicationStrategy, argument matching internals
│   ├── configuration/        # Injection (field/constructor/property), plugins
│   ├── framework/            # DefaultMockitoFramework, lifecycle
│   ├── session/              # MockitoSession implementation
│   ├── debugging/            # Invocation logging, warning generation
│   ├── exceptions/           # Stack trace cleaning, error formatting
│   ├── listeners/            # Invocation listeners, verification listeners
│   ├── junit/                # JUnit 4/5 rule/extension integration
│   ├── progress/             # Verification progress tracking
│   └── util/                 # Reflection, collections, IO, concurrent helpers
├── stubbing/                 # Answer, OngoingStubbing interfaces
├── verification/             # VerificationMode, VerificationAfterDelay
├── listeners/                # InvocationListener, VerificationStartedListener
├── plugins/                  # MockMaker, StackTraceCleanerProvider SPI
├── quality/                  # Strictness enum
└── session/                  # MockitoSession interface
```

## Scale indicators

- ~481 Java source files in mockito-core/src/main
- ~44K lines of code
- ByteBuddy bytecode generation for mock proxies
- SPI plugin system for mock makers and stack trace cleaners

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `InOrder` verification not detecting extra invocations between verified calls

When using `inOrder.verify(mock).methodA()` followed by `inOrder.verify(mock).methodB()`, extra invocations of unrelated methods between A and B are silently ignored. `InOrderWrapper` only checks the relative ordering of verified calls but does not report unverified interleaved invocations when using strict mode.

### N2: Fix `ArgumentCaptor.getAllValues` returning shared mutable list

`ArgumentCaptor.getAllValues()` returns the internal list directly. Callers who modify the returned list (e.g., `clear()`) corrupt the captor's state, causing subsequent `getValue()` calls to throw `IndexOutOfBoundsException`.

### N3: Add `verify(mock, description("..."))` to include custom message in failure output

Verification failures print the expected/actual invocation counts but do not support custom descriptive messages. Add a `description(String)` method that can be combined with `times()`, `atLeast()`, etc., to prepend user-provided context to the failure message. Document the new API in `doc/release-notes/official.md`.

### N4: Fix `MockedStatic` not restoring original behavior on `close()` when nested

When two `MockedStatic` instances for the same class are nested (inner opened before outer closes), closing the inner one restores the pre-mock behavior instead of restoring the outer mock's stubbing. The thread-local mock stack in `MockedSingletonImpl` does not properly handle nesting.

### N5: Fix `doNothing().when(spy).method()` not working for final methods on spies

When creating a spy of a class with a final method, `doNothing().when(spy).finalMethod()` still calls the real method because ByteBuddy's subclass mock maker cannot override final methods. The error message should explicitly state that final methods cannot be stubbed on spies created via subclass.

### N6: Add `Mockito.clearAllMocks()` to reset all mocks in the current thread

`Mockito.reset(mock)` requires a reference to each mock. Add a `clearAllMocks()` method that resets all mocks created in the current thread/session: clear stubbings, invocation records, and verification state. Track mocks via `MockitoFramework`.

### N7: Fix `UnnecessaryStubbingException` not reporting the line where the stubbing was declared

When strictness is `STRICT_STUBS` and a stubbing is unused, the exception message mentions the method that was stubbed but does not include the source file and line number where `when()` was called. The `StubbedInvocationMatcher` stores the invocation location but `UnusedStubbingReporting` does not include it.

### N8: Fix `ArgumentMatchers.argThat` causing `NullPointerException` for primitive parameters

`argThat(matcher)` returns null as the default value for the matcher placeholder. When the method parameter is a primitive type (e.g., `int`), the null is unboxed and throws `NullPointerException`. The `MatcherApplicationStrategy` should return the primitive default (0, false, etc.) instead.

### N9: Add `Answers.RETURNS_EMPTY_OPTIONALS` default answer

The existing `RETURNS_EMPTY_COLLECTIONS` answer returns empty lists/maps/sets but returns null for `Optional` return types. Add `RETURNS_EMPTY_OPTIONALS` that returns `Optional.empty()` for methods returning `Optional<T>`.

### N10: Fix `verifyNoInteractions` false positive when mock was used only for stubbing setup

`verifyNoInteractions(mock)` correctly fails when the mock was invoked in the test, but when the mock was only used during `when(mock.method()).thenReturn(...)` setup and never invoked in the actual test code, it still passes. The `when()` call itself records an invocation that should be excluded from the "no interactions" check.

### N11: Fix `doc/release-notes/official.md` not documenting deprecated API removals across major versions

The `doc/release-notes/official.md` file lists new features per release but omits entries for APIs removed after deprecation (e.g., removed `Matchers` class, legacy `MockitoJUnitRunner`). Add a "Removed" section per major version in `doc/release-notes/official.md`, cross-reference the original deprecation notices, and update `.github/ISSUE_TEMPLATE.md` with a migration assistance request template for users affected by removals.

## Medium

### M1: Implement argument capture for consecutive stubbing calls

When `when(mock.method(captor.capture())).thenReturn(a, b, c)` is used, the captor only captures the setup invocation, not the actual test invocations. Implement proper argument capture that records each real invocation's arguments independently of the stubbing declaration. Changes span `ArgumentCaptor`, `InvocationMatcher`, and `StubbedInvocationMatcher`.

### M2: Add mock verification timeout with polling

Implement `verify(mock, timeout(500).times(3)).method()` that polls for the expected invocation count within the timeout period. Useful for async code where the mock is invoked from another thread. Changes span `VerificationOverTimeImpl`, `VerificationModeFactory`, and add a polling mechanism in the verification checkers.

### M3: Implement deep stubs for generic return types

`RETURNS_DEEP_STUBS` fails when the stubbed method returns a generic type like `List<Foo>` because the generic type information is erased at runtime. Use the method's generic signature to resolve the actual type parameter and create an appropriately typed deep stub. Changes span `ReturnsDeepStubs`, `GenericMetadataSupport`, and mock creation settings.

### M4: Add BDD-style argument capture with `then()` assertions

Implement `BDDMockito.then(mock).should().method(captor.capture())` that combines verification and capture in one fluent call. Currently, BDD-style verification and argument capture require separate calls. Changes span `BDDMockito`, `BDDOngoingStubbing`, and verification mode integration.

### M5: Implement mock serialization with Kryo support

Mocks configured with `withSettings().serializable()` use Java's built-in serialization, which is slow and fails for classes without no-arg constructors. Add Kryo serialization support via `withSettings().serializable(SerializableMode.KRYO)`. Changes span `MockSettings`, mock creation in ByteBuddy, and serialization handling in `InvocationContainerImpl`. Add the Kryo dependency to `build.gradle.kts` and update `README.md` with serialization mode documentation.

### M6: Add invocation listener with filtering support

`MockSettings.invocationListeners()` notifies for every invocation. Add filtering so listeners can specify which methods they care about via method name patterns or annotations. Changes span `InvocationNotifierHandler`, `MockSettings`, `InvocationListener` interface, and the listener notification pipeline.

### M7: Implement ordered verification across multiple mocks with gap detection

Enhance `InOrder` to support a `strict()` mode where any invocation on any of the in-order mocks that was not explicitly verified causes a failure. Currently, `InOrder` only checks relative ordering. Changes span `InOrderImpl`, `InOrderWrapper`, `InOrderContextImpl`, and verification checkers.

### M8: Add annotation-driven mock injection for constructor parameters

`@InjectMocks` supports field and setter injection but not constructor injection when the constructor has parameters that match mock types. Implement constructor-parameter matching by type and name. Changes span `configuration/injection/`, `ConstructorInjection`, `PropertyAndSetterInjection`, and the injection scanner.

### M9: Implement stubbing inheritance for spy hierarchies

When a spy of a subclass is created, stubs declared on a parent class spy are not inherited. Implement stubbing inheritance so that `when(parentSpy.method()).thenReturn(x)` is visible when verifying `childSpy.method()`. Changes span `InvocationContainerImpl`, `MockHandlerImpl`, and mock creation settings.

### M10: Add `MockedConstruction` support for capturing constructor arguments

`MockedConstruction` intercepts constructor calls but does not provide access to the constructor arguments. Add `MockedConstruction.Context.arguments()` that returns the argument list for each construction. Changes span `MockedConstructionImpl`, ByteBuddy instrumentation in `creation/bytebuddy/`, and the `MockedConstruction.Context` interface.

### M11: Improve build and CI configuration for cross-JDK testing

Extend `.github/workflows/ci.yml` to test against JDK 11, 17, and 21 using a matrix strategy. Update `build.gradle.kts` to configure cross-compilation targets and source/target compatibility. Add JDK compatibility documentation to `.github/CONTRIBUTING.md`, configure Dependabot schedule in `.github/dependabot.yml` for Gradle dependency updates, and update `gradle.properties` with reproducible build settings.

## Wide

### W1: Implement mock generation using Java proxies as alternative to ByteBuddy

Add a lightweight `ProxyMockMaker` that uses `java.lang.reflect.Proxy` for interface mocking without ByteBuddy. Support it as a plugin via the `MockMaker` SPI. Changes span a new `creation/proxy/` package, `MockMaker` plugin registration, `MockHandlerImpl` (dispatch), `InvocationContainerImpl`, and configuration/plugins.

### W2: Add comprehensive static analysis for common Mockito mistakes

Implement a compile-time annotation processor that detects common mistakes: stubbing without verification, verifying unstubbed methods, mismatched argument matchers, leaked `MockedStatic` instances. Changes span a new `processor/` module, annotation definitions, exception message improvements, and integration with the JUnit extension.

### W3: Implement mock recording and playback for integration tests

Add the ability to record all mock interactions to a file and replay them in subsequent test runs. Support JSON serialization of invocations and responses. Changes span `InvocationContainerImpl` (recording), `MockHandlerImpl` (playback mode), a new `recording/` module, session management, and serialization of argument matchers.

### W4: Add Kotlin coroutine support for suspend function mocking

Implement support for mocking Kotlin suspend functions. Handle `Continuation` parameters transparently, support `coEvery`/`coVerify` style API, and integrate with Kotlin coroutine test dispatchers. Changes span `MockHandlerImpl` (continuation handling), `InvocationMatcher` (argument filtering), `StubbedInvocationMatcher`, ByteBuddy generation, and a new `kotlin/` module.

### W5: Implement thread-safe mock framework for parallel test execution

Add thread-isolated stubbing and verification state so mocks can be safely shared across parallel test threads. Implement per-thread invocation recording, thread-safe `InvocationContainerImpl`, and parallel-safe `MockitoSession`. Changes span `InvocationContainerImpl`, `MockHandlerImpl`, `MockitoSession`, `InOrderImpl`, verification checkers, and stubbing state.

### W6: Add fluent assertion API for mock interactions

Implement AssertJ-style fluent assertions for mock verification: `assertThat(mock).wasCalledWith("arg").exactly(3).times().inOrder()`. Support chained assertions, soft assertions, and detailed diff output for argument mismatches. Changes span a new `assertions/` module, verification modes, argument matching, exception formatting, and integration with the stubbing API.

### W7: Implement configurable strictness profiles with custom rules

Add named strictness profiles beyond `LENIENT`/`WARN`/`STRICT_STUBS`. Allow custom rules: max mock count per test, required verification for all stubbings, forbidden answer types. Changes span `Strictness`, `MockitoSession`, `StrictnessSelector`, `UnusedStubbingReporting`, `MockitoFramework`, and configuration/plugins.

### W8: Add mock dependency graph visualization

Implement a dependency graph builder that tracks which mocks interact with which other mocks during test execution. Generate DOT/Mermaid diagrams showing invocation flow. Changes span `InvocationNotifierHandler` (tracking), `MockHandlerImpl` (cross-mock detection), `MockitoFramework` (graph building), session reporting, and a new `visualization/` module.

### W9: Implement automatic mock configuration from interfaces

Add `@AutoMock` annotation that generates mock configurations from interface contracts: return type defaults, parameter constraints, exception specifications. Support interface hierarchies and generic interfaces. Changes span a new annotation type, annotation processing, `MockSettings` generation, `Answers` customization, `MockHandlerImpl`, and ByteBuddy creation.

### W10: Add migration tool from EasyMock/PowerMock to Mockito

Implement a source-code transformation tool that migrates EasyMock `expect()`/`replay()` and PowerMock `@PrepareForTest` patterns to Mockito equivalents. Changes span a new `migration/` module, AST parsing utilities, pattern matching for EasyMock/PowerMock APIs, code generation for Mockito equivalents, and CLI tooling.

### W11: Create comprehensive contributor documentation and design doc system

Establish a structured project documentation system: expand `doc/design-docs/` with design doc templates and existing architectural decisions (mock creation pipeline, stubbing resolution, ByteBuddy integration strategy); rewrite `.github/CONTRIBUTING.md` with build setup, test execution, and PR review guidelines; update `.github/ISSUE_TEMPLATE.md` with separate templates for bug reports, feature requests, and design proposals; add a `SECURITY.md` responsible disclosure process; update `README.md` with architecture overview and links to design docs; and create `.github/PULL_REQUEST_TEMPLATE.md` with a changelog and documentation checklist. Changes span `doc/design-docs/`, `.github/CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `SECURITY.md`, and `README.md`.
