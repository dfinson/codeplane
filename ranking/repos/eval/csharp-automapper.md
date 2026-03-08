# AutoMapper/AutoMapper

| Field | Value |
|-------|-------|
| **URL** | https://github.com/AutoMapper/AutoMapper |
| **License** | MIT |
| **Language** | C# |
| **Scale** | Medium |
| **Category** | Object-to-object mapper |
| **Set** | eval |
| **Commit** | `fc8cb3f3d6aafe35b77697fcd67639f7ae42fb70` |

## Why this repo

- **Expression-tree mapping pipeline**: Compiles mapping plans to expression trees at configuration time, separate validation, execution, and projection phases
- **Well-structured**: Clear separation between Configuration/, Execution/, Mappers/, QueryableExtensions/, Internal/, Licensing/
- **Rich feature surface**: Profiles, conventions, annotations, reverse maps, path maps, flattening, value transformers, IQueryable projections

## Structure overview

```
src/AutoMapper/
├── Configuration/           # MapperConfiguration, Profile, MappingExpression, Conventions, Annotations/
├── Execution/               # ExpressionBuilder, TypeMapPlanBuilder, ObjectFactory, ProxyGenerator
├── Mappers/                 # Built-in mappers: Collection, Assignable, Enum, Dynamic, Convert, Nullable, Dictionary, String
├── QueryableExtensions/     # ProjectionBuilder, NullsafeQueryRewriter, ProjectionMappers/
├── Internal/                # ReflectionHelper, TypeDetails, TypeExtensions, LockingConcurrentDictionary, PrimitiveHelper
├── Licensing/               # License, LicenseValidator, Edition, BuildInfo
├── Mapper.cs                # IMapper entry point
├── TypeMap.cs               # Runtime type map with member maps
├── ProfileMap.cs            # Resolved profile holding type maps
├── PropertyMap.cs           # Single property mapping
├── MemberMap.cs             # Abstract member mapping base
├── PathMap.cs               # Nested path mapping (ForPath)
├── ConstructorMap.cs        # Constructor parameter mapping
├── ResolutionContext.cs     # Per-map invocation context
├── Features.cs              # Feature flags (e.g. IGlobalFeature)
└── ServiceCollectionExtensions.cs  # DI registration
```

## Scale indicators

- ~80 C# source files in core library
- ~10K lines of code
- 500+ source files including tests and benchmarks
- Multiple mapper strategies (collection, enum, dynamic, string-dictionary, constructor, conversion)
- Expression-tree compilation pipeline and IQueryable projection system

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `ReverseMap` not honoring `ForPath` mappings

When a `TypeMapConfiguration` declares `ForPath(dest => dest.Address.City, opt => opt.MapFrom(src => src.City))` and then calls `ReverseMap()`, the reverse map does not generate the correct source expression for the nested path. The `PathMap` entries are skipped when `MappingExpression.ReverseMapExpression` iterates `PathMaps` in `TypeMapConfiguration`. Update `docs/source/` with corrected `ForPath` + `ReverseMap` usage examples.

### N2: Fix `NullsafeQueryRewriter` dropping method-call arguments for parameterised projections

In `QueryableExtensions/NullsafeQueryRewriter.cs`, when the projection tree contains a `MethodCallExpression` whose arguments themselves contain member accesses on nullable navigations, the rewriter wraps the receiver but does not recursively visit the argument sub-expressions, producing an invalid expression tree that throws at query execution.

### N3: Fix `ConfigurationValidator` not reporting unmapped `PathMap` members

`ConfigurationValidator.Validate` checks `PropertyMaps` for unresolved mappings but skips `PathMaps`. When a `ForPath` target references a destination member that does not exist, `AssertConfigurationIsValid()` passes without error instead of reporting the invalid path.

### N4: Fix `CollectionMapper` not preserving existing destination list when `UseDestinationValue` is set

In `Mappers/CollectionMapper.cs`, `MapCollection` clears and repopulates the destination collection even when the member map has `UseExistingValue = true`. The mapper should merge items into the existing collection rather than replacing it.

### N5: Add `[MapAtRuntime]` annotation support for constructor parameters

The `Annotations/MapAtRuntimeAttribute.cs` is defined and works for property members, but `ConstructorMap.ApplyAnnotations` in `ConstructorMap.cs` does not scan constructor parameter attributes, so runtime-resolved constructor parameters cannot be declared via the attribute.

### N6: Fix `EnumToEnumMapper` not handling `[Flags]` composite values

`Mappers/EnumToEnumMapper.cs` only maps single enum values through `Enum.ToObject`. When the source value is a bitwise combination of flags, the mapper produces an invalid destination value because it does not decompose and re-compose flag bits for enums whose underlying names differ.

### N7: Fix `LockingConcurrentDictionary` potential deadlock under recursive `GetOrAdd`

In `Internal/LockingConcurrentDictionary.cs`, `GetOrAdd` acquires a lock per bucket-hash. If the value factory itself triggers another `GetOrAdd` on the same dictionary with a key that hashes to the same bucket, the thread deadlocks on the same `Lock` instance.

### N8: Fix `ProxyGenerator` not forwarding interface default method implementations

`Execution/ProxyGenerator.cs` generates proxy types that implement interfaces but only creates stub properties. When the destination interface has default method implementations (C# 8+), calling those methods on the proxy throws `NotImplementedException` because the generated type does not inherit the interface defaults.

### N9: Fix `ObjectFactory` ignoring parameterless private constructors

`Execution/ObjectFactory.cs` uses `ConstructorInfo` lookups filtered to public constructors. If the only parameterless constructor is private or internal, `ObjectFactory` falls through to `FormatterServices.GetUninitializedObject`, bypassing any logic in the private constructor. It should respect `BindingFlags.NonPublic` when `CreateUsingServiceLocatorOrDefault` is invoked with `allowPrivate: true`.

### N10: Fix `TypeMapPlanBuilder` not emitting null-check for value-type source with `Nullable<T>` wrapper

In `Execution/TypeMapPlanBuilder.cs`, when building the map expression for `Nullable<T> → T`, the builder does not insert a `HasValue` guard. If the source is `null`, the compiled delegate throws `InvalidOperationException` instead of returning `default(T)` or applying the configured null substitution.

### N11: Fix `ISSUE_TEMPLATE.md` missing fields for performance regression reports

The `ISSUE_TEMPLATE.md` at the repository root only has sections for bug description, reproduction steps, and expected behavior. Add a performance regression template with fields for mapping configuration, benchmark results (before/after), profiling output, and AutoMapper version. Also update `CONTRIBUTING.md` with guidance on submitting performance-focused PRs and add a benchmarking section referencing the existing benchmark project.

## Medium

### M1: Add inheritance-chain mapping with automatic base-type map inclusion

When mapping `DerivedSrc → DerivedDest`, AutoMapper must currently have an explicit map for the pair. Implement automatic inclusion of base-type maps so that `CreateMap<BaseSource, BaseDest>()` automatically applies to derived pairs when the destination type hierarchy mirrors the source. Changes span `MapperConfiguration.Seal`, `TypeMapConfiguration`, `ProfileMap.BuildTypeMap`, and `TypeMapPlanBuilder`.

### M2: Implement bidirectional collection synchronization for `ReverseMap`

When `ReverseMap` is used with collection members (e.g., `List<ChildDto> → List<Child>`), the current reverse mapping creates new destination collection elements instead of matching by key and updating in place. Implement key-based matching using a configurable `EqualityComparison` on `MappingExpression`, applied during `CollectionMapper` execution and wired through `ReverseMapExpression`.

### M3: Add mapping diagnostic trace for debugging complex type maps

Implement a `MapperConfiguration.BuildExecutionPlan(typeof(Src), typeof(Dest))` diagnostic that returns a human-readable tree of the compiled mapping plan: source member → destination member, value resolvers applied, type converters, conditions, and null substitutions. Changes span `TypeMapPlanBuilder`, `ExpressionBuilder`, `ProfileMap`, and a new `DiagnosticPlan` model. Update `README.md` with diagnostic trace usage examples and add a troubleshooting guide to `docs/source/`.

### M4: Implement conditional profile activation based on runtime context

Add `Profile.When(Func<ResolutionContext, bool> predicate)` that activates or deactivates an entire profile's type maps at mapping time. Requires changes to `ProfileMap`, `MapperConfiguration.GetTypeMap`, `ResolutionContext`, and the plan builder to insert conditional branching expressions.

### M5: Add `ProjectTo<T>` support for `GroupBy` and aggregate projections

`QueryableExtensions/ProjectionBuilder.cs` currently only handles flat `Select` projections. Implement support for projecting grouped results where destination properties map to aggregate functions (`Count`, `Sum`, `Average`). Changes span `ProjectionBuilder`, add `AggregateProjectionMapper` in `ProjectionMappers/`, and extend `MappingExpression` with `.ForAggregate()`.

### M6: Implement mapping plan caching with invalidation for mutable configurations

`MapperConfiguration` rebuilds all type maps on `Seal()`. Add lazy per-`TypePair` plan caching in `ProfileMap` using the existing `LockingConcurrentDictionary`, with cache invalidation when `Features` change or an `IObjectMapper` is added. Changes span `ProfileMap`, `TypeMap`, `TypeMapPlanBuilder`, and `LockingConcurrentDictionary`.

### M7: Add annotation-driven value converter discovery

Extend the annotation system to support `[ValueConverter(typeof(MyConverter))]` on destination properties for automatic converter wiring. Requires changes to `Configuration/Annotations/ValueConverterAttribute.cs`, `TypeMapConfiguration.ApplyAnnotations`, `MappingExpression`, and `PropertyMap` to resolve and apply converters from attributes during configuration.

### M8: Implement `Map<TDest>(object source)` with runtime source-type resolution and caching

The current `Mapper.Map<TDest>(object source)` does a runtime type lookup every call. Implement a fast-path cache in `Mapper` using `TypePair` keyed on `source.GetType() → typeof(TDest)`, with fallback to the existing `FindTypeMapFor` resolution. Changes span `Mapper.cs`, `ResolutionContext`, and `ProfileMap.GetTypeMap`.

### M9: Add mapping interceptors (before-map / after-map) at the profile level

`MappingExpression` supports `BeforeMap` and `AfterMap` per type map. Implement profile-level interceptors that apply to all maps within a profile. Changes span `Profile`, `ProfileMap`, `TypeMapPlanBuilder` (to insert interceptor expressions), and `MappingExpression`.

### M10: Implement source-member auditing to detect unused source properties

Add `MapperConfiguration.FindUnmappedSourceMembers(typeof(Src), typeof(Dest))` that returns source properties not consumed by any `PropertyMap`, `PathMap`, or `ConstructorMap`. Changes span `TypeMap` (to track consumed source members), `ConfigurationValidator`, and expose via `MapperConfiguration`.

### M11: Add ReadTheDocs documentation build pipeline

Configure a documentation build pipeline using the existing `docs/` Sphinx project and `.readthedocs.yml`. Update `docs/requirements.txt` with pinned Sphinx dependencies, add API reference auto-generation from XML doc comments in `docs/source/`, configure versioned documentation builds tied to release tags in `.github/workflows/ci.yml`, and update `README.md` with a documentation badge and links to the hosted documentation site. Changes span `docs/`, `.readthedocs.yml`, `README.md`, `Directory.Build.props` (XML doc generation), and `.github/workflows/`.

## Wide

### W1: Implement compiled mapping code generation with source-generator support

Replace the runtime expression-tree compilation pipeline with an optional Roslyn source generator that emits mapping methods at build time. Requires a new `AutoMapper.SourceGeneration` project, modifications to `TypeMapPlanBuilder` to output C# syntax trees instead of expression trees, changes to `MapperConfiguration` to load generated mappers, and DI registration updates in `ServiceCollectionExtensions`.

### W2: Add polymorphic mapping with discriminator-based type resolution

Implement `CreateMap<Base, BaseDto>().IncludeAllDerived().DiscriminateBy(src => src.Type)` that automatically selects the correct derived type map based on a discriminator value. Changes span `TypeMapConfiguration`, `ProfileMap` type-map lookup, `MapperConfiguration.Seal` (to build discriminator tables), `TypeMapPlanBuilder` (to emit switch expressions), and `ResolutionContext`.

### W3: Implement full IQueryable projection pipeline with Include/ThenInclude support

Extend the `QueryableExtensions` to emit EF Core `Include`/`ThenInclude` calls based on mapped navigation properties. Requires changes to `ProjectionBuilder` (to detect navigation properties and emit include chains), `NullsafeQueryRewriter` (to preserve include expressions), `ProfileMap` (to expose navigation metadata), and new `IncludeProjectionMapper` in `ProjectionMappers/`.

### W4: Add multi-target mapping with fan-out and fan-in support

Implement `CreateMap<Source, (TargetA, TargetB)>()` that maps a single source to multiple destination objects, and `CreateMap<(SourceA, SourceB), Target>()` for combining multiple sources. Changes span `TypeMapConfiguration`, `MappingExpression`, `TypeMap`, `Mapper.Map`, `TypeMapPlanBuilder` expression generation, and `ResolutionContext` to carry tuple-typed destinations.

### W5: Implement mapping versioning and migration for schema evolution

Add version-aware mapping where `CreateMap<Src, Dest>().Version(2)` can coexist with prior versions. `Mapper.Map(source, version: 1)` selects the correct type map. Requires changes to `MapperConfiguration`, `ProfileMap`, `TypeMap` (to store version), `ResolutionContext` (to carry version), `TypeMapPlanBuilder`, and `Mapper`.

### W6: Add real-time mapping performance profiling and diagnostics

Implement an `IMapperDiagnostics` interface exposing per-type-map execution counts, average duration, expression-tree compilation time, and cache hit rates. Requires instrumentation in `Mapper.Map`, `ResolutionContext`, `TypeMapPlanBuilder` compilation, `LockingConcurrentDictionary` cache operations, and a new `Diagnostics/` module with `MapperDiagnosticsCollector`.

### W7: Implement async mapping pipeline for I/O-bound value resolvers

Add `MapAsync<TDest>(source)` that supports `IAsyncValueResolver` and `IAsyncTypeConverter`. Requires async versions of `Mapper`, `ResolutionContext`, `TypeMapPlanBuilder` (to emit async/await expression trees), `MappingExpression` (for `ResolveUsing<IAsyncValueResolver>`), and `CollectionMapper` (for parallel async element mapping).

### W8: Add cross-profile circular reference handling with identity map

Implement an identity map in `ResolutionContext` that tracks already-mapped source objects across all type maps to handle circular references. Requires changes to `ResolutionContext` (identity map dictionary), `Mapper.Map` (lookup before mapping), `TypeMapPlanBuilder` (emit identity map checks), `ProfileMap` (configure circular reference depth limit), and `CollectionMapper` (check map for collection elements).

### W9: Implement convention-based mapping with pluggable naming strategies

Extend `Conventions.cs` to support fully pluggable naming strategies beyond the built-in `PascalCase`/`LowerUnderscoreCase`. Add an `INamingConvention` pipeline where multiple naming conventions are composed (prefix stripping, suffix mapping, acronym expansion). Changes span `Conventions`, `INamingConvention`, `ProfileMap.MatchMembers`, `TypeMapConfiguration`, `MemberConfigurationExpression`, and `ConfigurationValidator`.

### W10: Add distributed mapping configuration with remote profile loading

Implement `MapperConfiguration.LoadProfile(Uri endpoint)` that fetches serialized profile definitions from a remote service, deserializes type maps, and merges them into the active configuration. Changes span `MapperConfiguration`, `Profile` (serialization/deserialization), `TypeMapConfiguration`, `ProfileMap`, `ServiceCollectionExtensions` (hot-reload registration), and a new `Remote/` module.

### W11: Implement comprehensive API documentation with versioned doc site

Overhaul the project's documentation: build a versioned doc site from `docs/source/` covering all mapping features (profiles, conventions, annotations, projections, value resolvers, type converters); add a migration guide from earlier AutoMapper versions; update `CONTRIBUTING.md` with documentation contribution guidelines; configure `docs/Makefile` for local doc builds; add `Directory.Build.props` settings for XML documentation generation across all projects; update `.readthedocs.yml` with build matrix for multiple framework targets; and link the doc site from `README.md` and `nuget.config` package metadata. Changes span `docs/`, `CONTRIBUTING.md`, `README.md`, `ISSUE_TEMPLATE.md`, `Directory.Build.props`, `.readthedocs.yml`, and `.github/workflows/`.
