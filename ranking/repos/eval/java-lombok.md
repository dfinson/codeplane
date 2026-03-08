# projectlombok/lombok

| Field | Value |
|-------|-------|
| **URL** | https://github.com/projectlombok/lombok |
| **License** | MIT |
| **Language** | Java |
| **Scale** | Large |
| **Category** | Boilerplate reduction (annotation processor / compiler plugin) |
| **Set** | eval |
| **Commit** | `c2babe33dd54e326ef3d4ef1a0fd74eb4c9ffbd9` |

## Why this repo

- **Dual compiler integration**: Separate handler implementations for javac and Eclipse/ECJ compiler pipelines
- **AST manipulation**: Direct Java AST construction (method generation, field insertion, import management)
- **Configuration system**: Cascading config files, annotation-level options, cross-cutting feature flags

## Structure overview

```
lombok/src/
├── core/lombok/
│   ├── *.java                    # Annotation definitions: @Getter, @Setter, @Builder, @Data, @Value, etc.
│   ├── ConfigurationKeys.java    # All lombok configuration keys
│   ├── core/
│   │   ├── AST.java              # Abstract syntax tree base
│   │   ├── LombokNode.java       # Node abstraction over compiler-specific AST
│   │   ├── AnnotationValues.java  # Annotation parameter extraction
│   │   ├── AnnotationProcessor.java # Entry point for javac annotation processing
│   │   ├── LombokConfiguration.java # Cascading config resolution
│   │   └── TypeLibrary.java       # Type name normalization
│   ├── javac/
│   │   ├── JavacAST.java          # javac-specific AST wrapper
│   │   ├── JavacNode.java         # javac-specific node
│   │   ├── JavacAnnotationHandler.java # Handler base class for javac
│   │   ├── JavacTransformer.java  # AST transformation pipeline
│   │   ├── HandlerLibrary.java    # Handler discovery and dispatch
│   │   └── handlers/
│   │       ├── HandleGetter.java      # @Getter code generation
│   │       ├── HandleSetter.java      # @Setter code generation
│   │       ├── HandleBuilder.java     # @Builder code generation
│   │       ├── HandleSuperBuilder.java # @SuperBuilder code generation
│   │       ├── HandleConstructor.java  # @NoArgsConstructor etc.
│   │       ├── HandleEqualsAndHashCode.java # @EqualsAndHashCode
│   │       ├── HandleToString.java     # @ToString
│   │       ├── HandleData.java         # @Data composite
│   │       ├── HandleValue.java        # @Value composite
│   │       ├── HandleWith.java         # @With wither methods
│   │       ├── HandleLog.java          # @Slf4j, @Log, etc.
│   │       ├── HandleSneakyThrows.java # @SneakyThrows
│   │       ├── HandleCleanup.java      # @Cleanup
│   │       ├── HandleNonNull.java      # @NonNull null checks
│   │       ├── HandleSynchronized.java # @Synchronized
│   │       ├── HandleLocked.java       # @Locked (ReentrantLock)
│   │       ├── HandleDelegate.java     # @Delegate
│   │       ├── HandleVal.java          # val/var type inference
│   │       ├── JavacHandlerUtil.java   # Shared AST building utilities
│   │       └── JavacSingularsRecipes.java # @Singular collection builders
│   └── eclipse/
│       ├── handlers/              # Eclipse/ECJ handler mirrors
│       └── EclipseAST.java        # Eclipse-specific AST wrapper
├── delombok/                      # Delombok: reverse-transform to plain Java
├── eclipseAgent/                  # Eclipse IDE agent for live processing
└── installer/                     # IDE plugin installer
```

## Scale indicators

- ~405 Java source files (excluding stubs)
- ~73K lines of code
- Dual javac + Eclipse handler implementations
- Configuration system with ~100 configuration keys
- Singularizer recipes for Guava and java.util collections

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `@Builder` not respecting `@Accessors(prefix)` on fields

When a class uses both `@Builder` and `@Accessors(prefix = "m")` on fields like `mName`, the builder generates `mName(String)` setter methods instead of stripping the prefix to produce `name(String)`. The `HandleBuilder` reads the field name directly without consulting `HandleAccessors` prefix resolution.

### N2: Fix `@ToString` including `static` fields when `@ToString.Include` is on a static field

`@ToString` skips static fields by default, but when a user explicitly annotates a static field with `@ToString.Include`, it is silently ignored rather than producing a warning. `HandleToString` filters static fields before checking for explicit includes.

### N3: Add `@Getter(lazy=true)` support for `volatile` fields

`@Getter(lazy=true)` generates a double-checked locking pattern with a private `AtomicReference` backing field. However, if the original field is already declared `volatile`, the generated code adds a redundant `AtomicReference` wrapper. Detect the `volatile` modifier and emit a warning that `lazy` and `volatile` are redundant.

### N4: Fix `@NonNull` check not generated for record constructor parameters

When `@NonNull` is used on a record component, `HandleNonNull` does not generate the null-check in the compact constructor because it only processes class/method-level declarations, not record components. The handler needs to detect record declarations and inject checks into the canonical constructor.

### N5: Fix `@EqualsAndHashCode` not including `transient` fields when explicitly listed in `@Include`

`@EqualsAndHashCode` skips `transient` fields by default. When a transient field is explicitly annotated with `@EqualsAndHashCode.Include`, it is still excluded because the transient filter runs before the include check in `HandleEqualsAndHashCode`.

### N6: Add configuration key to set default `@Builder` method name

`@Builder` defaults to `builder()` as the static method name. Projects that use a different convention (e.g., `newBuilder()`) must set `builderMethodName` on every annotation. Add a configuration key `lombok.builder.defaultBuilderMethodName` that `HandleBuilder` reads as the default. Document the new key in `doc/changelog.markdown` and add it to the configuration keys listing in `README.md`.

### N7: Fix `@Cleanup` not handling exceptions from the `close()` call

`@Cleanup` generates a try/finally that calls `close()` in the finally block. If `close()` throws, it suppresses the original exception from the try block. The generated code should use `addSuppressed` when both the try block and close throw.

### N8: Fix `@SneakyThrows` not working on lambda expressions

`@SneakyThrows` can be placed on methods and constructors but is silently ignored when placed on a lambda expression. `HandleSneakyThrows` should either emit a warning that lambdas are unsupported or generate the try/catch wrapping inside the lambda body.

### N9: Add `@ToString.Include(name="...")` to customize field name in output

`@ToString` uses the Java field name in the output string. Some codebases want display names different from field names (e.g., `userId` → `user_id`). Add a `name` attribute to `@ToString.Include` and update `HandleToString` to use it in the generated `toString()`.

### N10: Fix `@Setter` on a `boolean` field with `is` prefix generating wrong method name

For a field `boolean isActive`, `@Setter` generates `setIsActive(boolean)` instead of `setActive(boolean)`. `HandleSetter` does not strip the `is` prefix from boolean fields the way `HandleGetter` adds it. Align with JavaBeans convention by stripping `is` for the setter name.

### N11: Fix `doc/changelog.markdown` entries missing cross-references to issue tracker

The `doc/changelog.markdown` file lists changes per release but individual entries lack links to the corresponding GitHub issue or PR. Add hyperlinked issue references to existing changelog entries, establish a changelog entry format in `CONTRIBUTING` guidelines (referenced from `.github/copilot-instructions.md`), and update `doc/PlannedExtensions.txt` to indicate which planned features have corresponding tracking issues.

## Medium

### M1: Implement `@Builder.Default` for collection fields with empty collection initialization

`@Builder.Default` requires an initializer expression. For collection fields, users typically want `new ArrayList<>()` as the default, but the builder's `build()` method calls the all-args constructor which passes the builder's field (which is null if not set). Implement special-case default handling for List/Set/Map fields that initializes to empty collections when not explicitly set via the builder. Changes span `HandleBuilder` and `HandleBuilderDefault`.

### M2: Add `@With` support for `@SuperBuilder` generated classes

`@With` generates wither methods that create a new instance using the all-args constructor. But for `@SuperBuilder` classes in an inheritance hierarchy, there is no all-args constructor—only the builder. Generate wither methods that use the builder pattern instead. Changes span `HandleWith`, `HandleSuperBuilder`, and `JavacHandlerUtil`.

### M3: Implement `@Getter`/`@Setter` support for Java records

Records are immutable by design and have component accessor methods. However, `@Getter` on a record should optionally add `@Override` with custom access levels, and `@Setter` on a record should emit a clear error message rather than silently failing. Changes span `HandleGetter`, `HandleSetter`, and `JavacHandlerUtil` (record detection).

### M4: Add Checker Framework `@Pure` and `@SideEffectFree` annotations to generated methods

Lombok generates `@Pure` annotations for getters when Checker Framework integration is enabled, but does not annotate `toString()`, `equals()`, or `hashCode()` with `@SideEffectFree`. Update `HandleToString`, `HandleEqualsAndHashCode`, and the Checker Framework configuration to add appropriate purity annotations.

### M5: Implement `@Builder` validation hooks

Add a `@Builder.Validate` annotation for a method that is called in `build()` before returning the constructed object. The validate method receives no arguments and can throw exceptions. Support validation on both `@Builder` and `@SuperBuilder`. Changes span `HandleBuilder`, `HandleSuperBuilder`, and annotation definitions.

### M6: Add `@Data` support for sealed class hierarchies

`@Data` generates `equals`, `hashCode`, `toString`, getters, setters, and required-args constructor. When applied to a sealed class with permitted subclasses, the generated `equals` should use `canEqual` to prevent symmetry violations across the hierarchy. Changes span `HandleData`, `HandleEqualsAndHashCode` (canEqual for sealed), and `HandleConstructor`.

### M7: Implement delombok output formatting configuration

The delombok tool generates plain Java but uses its own code formatting. Add configuration for indentation style (tabs/spaces, width), brace placement, and blank line insertion. Changes span `delombok/` (formatting output), configuration key registration, and `LombokConfiguration` parsing. Update `build.xml` with a delombok formatting integration test target and document formatting options in `doc/publishing.txt`.

### M8: Add `@Log` support for custom logger factories

`@Log` supports specific logging frameworks (SLF4J, Log4j, JUL, etc.), but not custom logger factories. Add a `@CustomLog` annotation that reads the logger factory class and method from lombok.config. Changes span `HandleLog`, `ConfigurationKeys`, and annotation definitions.

### M9: Implement `@Singular` support for Guava `ImmutableTable`

`@Builder` with `@Singular` supports List, Set, Map, and Guava ImmutableList/ImmutableSet/ImmutableMap. Add support for Guava `ImmutableTable` with `put(R, C, V)` singular method. Changes span `JavacSingularsRecipes`, a new `JavacGuavaTableSingularizer`, and the Eclipse handler equivalent.

### M10: Add `@EqualsAndHashCode` caching for immutable classes

For `@Value` (immutable) classes, the hash code never changes after construction. Add a `cacheHashCode` option to `@EqualsAndHashCode` that computes the hash code once and stores it. Generate a private `int $hashCodeCache` field and lazy-compute on first `hashCode()` call. Changes span `HandleEqualsAndHashCode` and `HandleValue`.

### M11: Modernize build system from Ant to Gradle

Migrate the primary build from `build.xml` and `buildScripts/*.ant.xml` to Gradle with Kotlin DSL. Convert the Ant targets (compile, test, javadoc, eclipse-p2, maven publish) to Gradle tasks. Update `.github/workflows/ant.yml` to use the new Gradle build, rename the workflow file to `build.yml`, configure the Gradle wrapper, and update `README.md` with the new build instructions. Preserve backward compatibility by keeping `build.xml` as a thin wrapper that delegates to Gradle during the transition period. Changes span `build.xml`, `buildScripts/`, `.github/workflows/`, `README.md`, and new `build.gradle.kts` and `settings.gradle.kts` files.

## Wide

### W1: Implement `@Memoize` annotation for method result caching

Add `@Memoize` that generates a caching wrapper around a method: compute the result once and return the cached value on subsequent calls. Support configurable cache key (arguments), thread safety mode (none/synchronized/concurrent), and TTL-based expiration. Changes span a new `HandleMemoize` handler (javac + eclipse), annotation definition, `ConfigurationKeys`, and `JavacHandlerUtil` (cache field generation).

### W2: Add comprehensive null-safety framework

Extend `@NonNull` into a full null-safety system: `@Nullable`, `@NonNullApi` (package-level default), `@NonNullFields` (class-level default). Generate null checks at API boundaries, including method parameters, return values, and field assignments. Changes span `HandleNonNull`, new annotations, `JavacHandlerUtil` (null check insertion), `HandleGetter`/`HandleSetter` (null contract propagation), `ConfigurationKeys`, and Eclipse handlers.

### W3: Implement `@Mapper` for type-safe object mapping

Add `@Mapper` that generates mapping methods between two types based on field name matching. Support custom field mappings, nested object mapping, collection mapping, and type conversion. Changes span a new `HandleMapper` handler (javac + eclipse), annotation definitions, `JavacHandlerUtil` (mapping code generation), type resolution utilities, and `ConfigurationKeys`.

### W4: Add incremental compilation support for javac

Lombok currently reprocesses all files on each compilation. Implement incremental processing by tracking which generated code depends on which source files. Cache handler output and invalidate only affected files. Changes span `AnnotationProcessor`, `JavacTransformer`, `HandlerLibrary`, a new `incremental/` package for dependency tracking, and configuration.

### W5: Implement `@Observe` annotation for observable properties

Add `@Observe` that generates property-change notification code: a `PropertyChangeSupport` field, `addPropertyChangeListener`/`removePropertyChangeListener` methods, and modified setters that fire change events. Changes span a new `HandleObserve` handler (javac + eclipse), annotation definitions, `HandleSetter` integration, `JavacHandlerUtil`, and `ConfigurationKeys`.

### W6: Add Java module system (JPMS) full compatibility layer

Lombok's agent-based approach conflicts with JPMS's strict encapsulation. Implement a pure annotation-processor mode that works without `--add-opens` flags. Changes span `AnnotationProcessor` (processor-only mode), `JavacTransformer` (JPMS-safe AST access), `HandlerLibrary` (fallback mechanisms), agent launcher, and installer (module configuration).

### W7: Implement `@Retry` annotation for method retry logic

Add `@Retry` that wraps method bodies in retry logic with configurable max attempts, backoff strategy (fixed/exponential), retryable exception types, and circuit breaker integration. Changes span a new `HandleRetry` handler (javac + eclipse), annotation definition with attributes, `JavacHandlerUtil` (try/catch generation with loop), `ConfigurationKeys`, and exception type resolution.

### W8: Add real-time IDE feedback for lombok-generated code

Implement a language-server-protocol (LSP) extension that shows generated code inline in IDEs beyond Eclipse. Provide hover info for lombok annotations showing generated methods, go-to-definition support for generated members, and completion suggestions. Changes span `eclipseAgent/` (hover provider), a new `lsp/` module, `HandlerLibrary` (code preview), delombok (on-the-fly), and installer (LSP registration).

### W9: Implement cross-annotation dependency validation

Add compile-time validation that detects conflicting annotation combinations: `@Data` + explicit `@Getter` with different access, `@Builder` + `@Value` on mutable fields, `@Synchronized` + `@Locked` on the same method. Changes span `HandlerLibrary` (validation phase), each handler (conflict detection), new annotation-level error reporting, `JavacHandlerUtil`, and `ConfigurationKeys` (suppression).

### W10: Add configurable code generation templates

Allow users to customize generated code via template files: toString format strings, equals comparison strategies, builder method naming patterns, constructor parameter ordering. Changes span `ConfigurationKeys` (template paths), `LombokConfiguration` (template loading), a new `templates/` package for template parsing, `HandleToString` / `HandleEqualsAndHashCode` / `HandleBuilder` / `HandleConstructor` (template-driven generation), and delombok.

### W11: Create comprehensive developer guide and extension authoring documentation

Build a structured developer documentation system from existing scattered files: consolidate `doc/changelog.markdown`, `doc/PlannedExtensions.txt`, `doc/experiences.txt`, and `doc/git-workflow.txt` into a cohesive developer guide; create an extension authoring tutorial explaining how to add new annotation handlers for both javac and Eclipse pipelines; document the build system in `buildScripts/` with architecture diagrams; update `README.md` with links to all documentation; add an `AUTHORS` file maintenance guide; and create a `CONTRIBUTING.md` at the repository root. Changes span `doc/`, `buildScripts/`, `README.md`, `AUTHORS`, `SECURITY.md`, and `.github/workflows/`.
