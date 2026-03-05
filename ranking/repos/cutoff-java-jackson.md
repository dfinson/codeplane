# FasterXML/jackson-databind

| Field | Value |
|-------|-------|
| **URL** | https://github.com/FasterXML/jackson-databind |
| **License** | Apache-2.0 |
| **Language** | Java |
| **Scale** | Large |
| **Category** | JSON data-binding |
| **Set** | Cutoff |

## Why this repo

- **Well-structured**: Organized under
  `src/main/java/com/fasterxml/jackson/databind/` with clear
  per-concern packages — `deser/` (deserializers and context),
  `ser/` (serializers and provider), `type/` (Java type resolution),
  `introspect/` (class/annotation introspection), `annotation/`
  (Jackson annotations), `cfg/` (configuration and features),
  `module/` (Module SPI for extensions), `node/` (JsonNode tree model),
  `exc/` (exceptions), and `jsontype/` (polymorphic type handling).
  The central `ObjectMapper` orchestrates all subsystems.
- **Rich history**: 3K+ stars, 7K+ commits. The de-facto standard for
  Java JSON processing. Issues cover deserialization edge cases, type
  resolution, polymorphism, and performance.
- **Permissive**: Apache-2.0 license.

## Structure overview

```
src/main/java/com/fasterxml/jackson/databind/
├── ObjectMapper.java           # Central API: readValue(), writeValue(),
│                               #   convertValue(), treeToValue()
├── ObjectReader.java           # Immutable reader with per-call config
├── ObjectWriter.java           # Immutable writer with per-call config
├── DeserializationContext.java # Runtime context during deserialization
├── SerializerProvider.java     # Runtime context during serialization
├── deser/                      # BeanDeserializer, StdDeserializer,
│                               #   CollectionDeserializer, MapDeserializer,
│                               #   DeserializerCache, ValueInstantiator
├── ser/                        # BeanSerializer, StdSerializer,
│                               #   CollectionSerializer, MapSerializer,
│                               #   SerializerCache, PropertyWriter
├── type/                       # JavaType, TypeFactory, TypeBindings,
│                               #   CollectionType, MapType, ArrayType
├── introspect/                 # BeanDescription, AnnotatedClass,
│                               #   AnnotatedMethod, AnnotatedField,
│                               #   JacksonAnnotationIntrospector
├── annotation/                 # JsonProperty, JsonCreator, JsonIgnore,
│                               #   JsonTypeInfo, JsonSubTypes
├── cfg/                        # MapperConfig, DeserializationConfig,
│                               #   SerializationConfig, MapperFeature,
│                               #   DeserializationFeature, SerializationFeature
├── module/                     # Module, SimpleModule, SetupContext
├── node/                       # ObjectNode, ArrayNode, TextNode,
│                               #   NumericNode, BooleanNode, NullNode
├── jsontype/                   # TypeResolverBuilder, TypeIdResolver,
│                               #   AsPropertyTypeDeserializer
├── exc/                        # MismatchedInputException,
│                               #   InvalidDefinitionException,
│                               #   UnrecognizedPropertyException
└── util/                       # StdConverter, Named, ClassUtil,
                                #   TokenBuffer, LRUMap
```

## Scale indicators

- ~600 Java source files under src/main/java/
- ~150K lines of code
- Deep package hierarchy (4–5 levels)
- Dependencies: jackson-core, jackson-annotations

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix ObjectMapper.readValue not propagating DeserializationFeature overrides to nested contexts

When calling `ObjectMapper.readValue()` with a reader that has
`DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES` disabled, nested
`DeserializationContext` instances created for inner objects revert to
the mapper-level default. Fix the context construction in
`DeserializationContext._createInstance()` to carry forward per-read
feature overrides.

### N2: Fix TypeFactory.constructType losing generic parameters for recursive types

`TypeFactory.constructType()` fails to resolve generic parameters when
a type references itself (e.g., `Comparable<Foo>` where `Foo implements
Comparable<Foo>`). The resolution enters infinite recursion and falls
back to `Object`. Fix the type resolution loop in `TypeFactory` to
detect self-referential bindings and short-circuit with the concrete
type.

### N3: Fix ObjectNode.equals not comparing field order for ordered nodes

`ObjectNode.equals()` compares entries as unordered sets, but when
constructed via `ObjectMapper` with `SORT_PROPERTIES_ALPHABETICALLY`
enabled, users expect order-sensitive comparison. Fix `ObjectNode
.equals()` to respect the `JsonNodeFeature.WRITE_PROPERTIES_SORTED`
flag when comparing nodes.

### N4: Fix @JsonCreator static factory method ignored when constructor also present

When a class has both a `@JsonCreator` annotated static factory method
and a `@JsonCreator` annotated constructor, the introspection logic in
`BasicBeanDescription` silently picks the constructor and ignores the
factory method without warning. Fix the creator detection in
`introspect/` to throw `InvalidDefinitionException` on ambiguous
`@JsonCreator` annotations.

### N5: Fix DeserializationContext not including field path in MismatchedInputException

When deserialization fails on a deeply nested field, the thrown
`MismatchedInputException` only reports the immediate field name
without the full JSON path (e.g., just `"age"` instead of
`"person.address.age"`). Fix the path tracking in
`DeserializationContext` to accumulate the full property path using
the `JsonStreamContext` stack.

### N6: Fix SerializerProvider not caching serializers for types with custom views

Serializers resolved with `@JsonView` active are incorrectly cached
and reused for requests without a view, causing fields to be omitted.
Fix the cache key computation in `SerializerCache` to include the
active view class in the lookup key.

### N7: Fix MapSerializer not respecting @JsonPropertyOrder on map entries

When serializing a `Map` with `@JsonPropertyOrder` on the containing
bean, map entries are emitted in iteration order rather than the
specified property order. Fix `MapSerializer.serializeFields()` to
sort entries when the enclosing bean has property-order metadata
available through the `SerializerProvider`.

### N8: Fix TypeIdResolver.idFromValue receiving proxy instead of actual object

When serializing objects behind JDK proxies (e.g., lazy-loaded JPA
entities), `TypeIdResolver.idFromValue()` receives the proxy object,
and `getClass()` returns the proxy class rather than the actual entity
class. Fix the polymorphic type serialization path to unwrap common
proxy types before calling `idFromValue()`.

### N9: Fix @JsonUnwrapped not working with @JsonCreator constructors

Deserialization of beans with both `@JsonUnwrapped` properties and a
`@JsonCreator` constructor throws `InvalidDefinitionException`. The
`BeanDeserializer` setup does not merge unwrapped properties into the
creator parameter resolution. Fix the property merging logic in
`deser/BeanDeserializer` to include unwrapped properties when
building creator-based deserializers.

### N10: Fix ObjectReader.withValueToUpdate not applying to collection types

`ObjectReader.withValueToUpdate(existingList)` ignores the existing
collection and constructs a new list instead of appending to it. The
`CollectionDeserializer` does not check for the update value in the
deserialization context. Fix `CollectionDeserializer.deserialize()` to
detect and populate the existing collection when a value-to-update is
set.

## Medium

### M1: Implement constructor parameter name discovery without @JsonProperty

Add automatic constructor parameter name resolution using the Java 8+
`-parameters` compiler flag or local-variable debug info. When
`@JsonProperty` is absent, use `java.lang.reflect.Parameter.getName()`
to match JSON keys to constructor parameters. Requires changes to
`introspect/AnnotatedConstructor`, `BasicBeanDescription` creator
detection, `BeanDeserializerFactory`, and `DeserializationConfig` for a
feature flag to enable/disable the behavior.

### M2: Add JSON Schema generation from Jackson-annotated classes

Implement `ObjectMapper.generateJsonSchema(Class<?>)` that produces a
JSON Schema (draft 2020-12) from bean metadata: required fields from
`@JsonProperty(required=true)`, type mappings, `@JsonPropertyDescription`
for descriptions, `@JsonFormat` for format hints, and polymorphic type
schemas from `@JsonTypeInfo`. Changes span a new `schema/` sub-package,
`introspect/BeanDescription` for metadata extraction, `type/TypeFactory`
for type mapping, and `ObjectMapper` for the entry point method.

### M3: Implement streaming large-collection serialization with backpressure

Add `ObjectWriter.writeValuesAsArray(OutputStream, Iterator)` that
serializes large collections without buffering the entire collection in
memory. Support backpressure by pausing iteration when the output buffer
is full. Requires changes to `ObjectWriter`, `ser/SequenceWriter`,
`SerializerProvider` for streaming context, and `cfg/SerializationConfig`
for buffer-size configuration.

### M4: Add support for Java 16+ record types

Implement automatic deserialization of Java records: detect record
classes in `BeanDescription`, extract canonical constructor parameters,
map JSON properties to record components without annotations, and
support `@JsonProperty` overrides on record components. Changes span
`introspect/BasicBeanDescription`, `introspect/AnnotatedClass`,
`deser/BeanDeserializerFactory`, `deser/ValueInstantiator`, and
`type/TypeFactory` for record type detection.

### M5: Implement ObjectMapper.builder() immutable configuration pattern

Add `ObjectMapper.builder()` with a fluent API for constructing
fully-configured immutable mappers: `enable(Feature)`, `disable(Feature)`,
`addModule(Module)`, `setVisibility()`, `setPropertyNamingStrategy()`,
and `build()`. The builder must validate configuration consistency at
build time. Changes touch `ObjectMapper`, `cfg/MapperConfig`,
`cfg/BaseSettings`, `module/Module`, and `cfg/MapperBuilder`.

### M6: Add polymorphic deserialization with deduction-based type resolution

Implement `JsonTypeInfo.Id.DEDUCTION` that inspects incoming JSON
properties to deduce the concrete type without an explicit type
discriminator field. For each subtype, compute a unique property
signature and match incoming properties against it. Changes span
`jsontype/TypeResolverBuilder`, a new `jsontype/impl/DeductionTypeDeserializer`,
`introspect/BeanDescription` for property signatures, and
`deser/BeanDeserializerFactory` for candidate registration.

### M7: Implement contextual serializer/deserializer support for container types

Add `ContextualSerializer` and `ContextualDeserializer` support for
`List`, `Map`, and `Set` types, so annotations on container fields
(e.g., `@JsonFormat` on a `List<Date>` field) are propagated to
element serializers. Changes span `deser/CollectionDeserializer`,
`deser/MapDeserializer`, `ser/CollectionSerializer`,
`ser/MapSerializer`, and `SerializerProvider`/`DeserializationContext`
for contextual resolution chaining.

### M8: Add CSV-style flat-mapping for nested objects

Implement `@JsonFlatten(separator=".")` that flattens nested object
properties into dot-separated keys during serialization (e.g.,
`address.city`) and reconstructs nested objects during deserialization.
Changes span a new `@JsonFlatten` annotation in `annotation/`, property
resolution in `ser/BeanPropertyWriter`, `deser/BeanDeserializer` for
unflatten logic, and `introspect/BeanDescription` for delimiter-aware
property discovery.

### M9: Implement typed exception deserialization with error-body mapping

Add `ObjectReader.onFailure(Class<ExcType>, Function<ExcData, ExcType>)`
that maps structured JSON error bodies to typed exceptions during
deserialization failures. Support multiple error shapes keyed by HTTP
status or error-code field. Changes span `ObjectReader`,
`DeserializationContext` error handling, `exc/MismatchedInputException`,
and a new `deser/ErrorBodyDeserializer`.

### M10: Add Mix-in annotation support for third-party classes

Implement `ObjectMapper.addMixIn(target, mixinSource)` that applies
annotations from a mixin interface to a target class without modifying
it. Support field-level, method-level, and constructor-level mixins.
The introspection layer must merge mixin annotations with the target's
own annotations, with mixin taking precedence. Changes span
`introspect/JacksonAnnotationIntrospector`, `introspect/AnnotatedClass`,
`introspect/MixInResolver`, `cfg/MapperConfig`, and `ObjectMapper`.

## Wide

### W1: Implement a modular type system with pluggable type resolvers

Redesign the type resolution subsystem to support pluggable type
resolvers: `TypeResolverPlugin` SPI for custom type mapping (e.g.,
Kotlin data classes, Scala case classes, Lombok builders), compose
multiple resolvers with priority ordering, and cache resolved types
across mappers. Changes span `type/TypeFactory`, `type/TypeBindings`,
`introspect/AnnotatedClass`, `deser/BeanDeserializerFactory`,
`ser/BeanSerializerFactory`, `module/Module`, and a new
`type/spi/` sub-package.

### W2: Add async deserialization pipeline with non-blocking I/O

Implement `ObjectMapper.readValueAsync(AsyncInputStream)` returning
`CompletableFuture<T>` that deserializes JSON from non-blocking
streams. Support chunked input, streaming array elements via
`Flow.Publisher<T>`, and cancellation. Changes span `ObjectMapper`,
`ObjectReader`, `DeserializationContext` for async state management,
`deser/BeanDeserializer` for resumable parsing, `type/` for reactive
type detection, and a new `async/` sub-package.

### W3: Implement full JSON Merge Patch (RFC 7396) and JSON Patch (RFC 6902) support

Add `ObjectMapper.mergePatch(target, patch)` for RFC 7396 and
`ObjectMapper.applyPatch(target, patchOps)` for RFC 6902 (add, remove,
replace, move, copy, test). Support patching at both `JsonNode` level
and typed-bean level with automatic deserialization/reserialization.
Changes span `ObjectMapper`, `node/ObjectNode`, `node/ArrayNode`,
a new `patch/` sub-package with operation types, `DeserializationContext`
for partial updates, and `ser/` for diff generation.

### W4: Implement annotation-driven data validation during deserialization

Integrate Bean Validation (JSR 380) annotations (`@NotNull`, `@Size`,
`@Pattern`, `@Min`, `@Max`) into the deserialization pipeline so
validation errors are collected and reported as structured
`ConstraintViolationException` before returning the deserialized
object. Support nested validation, cross-field validation groups, and
custom validators. Changes span `deser/BeanDeserializer`,
`deser/SettableBeanProperty`, `DeserializationContext`,
`introspect/BeanDescription`, `cfg/DeserializationConfig`, and a new
`validation/` sub-package.

### W5: Add schema evolution and versioned deserialization support

Implement `@JsonVersioned(version=2)` and a `SchemaEvolutionModule`
that supports deserializing older JSON formats: field renames
(`@JsonAlias`-based migration), type changes (converters), removed
fields (ignored with warnings), added fields (defaults), and structural
changes (flatten/nest transforms). Changes span a new `evolution/`
sub-package, `deser/BeanDeserializerFactory` for version-aware
instantiation, `introspect/` for version metadata, `annotation/` for
new annotations, `module/` for the evolution module, and `cfg/` for
version configuration.

### W6: Implement a reflectionless code-generated serialization mode

Add `jackson-databind-codegen` annotation processor that generates
`JsonSerializer` and `JsonDeserializer` implementations at compile
time, eliminating runtime reflection. The processor must handle
`@JsonProperty`, `@JsonCreator`, `@JsonTypeInfo`, `@JsonUnwrapped`,
and custom serializers. At runtime, a `CodegenModule` registers
generated serializers with priority over reflective ones. Changes
span a new annotation processor module, `module/SimpleModule` for
registration, `ser/BeanSerializerFactory` and
`deser/BeanDeserializerFactory` for fallback, and `introspect/` for
metadata extraction at compile time.

### W7: Add comprehensive ObjectMapper diagnostics and debugging tools

Implement `ObjectMapper.diagnose(Class<?>)` that produces a structured
report: resolved serializer/deserializer chain, property mappings,
type bindings, active features affecting the type, applicable mixins,
and potential issues (ambiguous creators, conflicting annotations).
Add `ObjectMapper.explain(json, Class<?>)` that traces the
deserialization path step by step. Changes span `ObjectMapper`,
`ser/SerializerCache`, `deser/DeserializerCache`,
`introspect/BeanDescription`, `type/TypeFactory`, `cfg/MapperConfig`,
and a new `diagnostic/` sub-package.

### W8: Implement multi-format ObjectMapper supporting JSON, YAML, CBOR, and XML

Extend `ObjectMapper` to support format negotiation: auto-detect input
format from content, route to the appropriate `JsonFactory`, and apply
format-specific configuration. Add `ObjectMapper.forFormat(Format)` and
`ObjectMapper.autoDetect()`. Ensure all serializers and deserializers
work uniformly across formats. Changes span `ObjectMapper`,
`ObjectReader`, `ObjectWriter`, `cfg/MapperConfig` for format-specific
features, `ser/SerializerProvider`, `deser/DeserializationContext`,
and a new `format/` sub-package.

### W9: Implement lazy deserialization with proxy-backed object graphs

Add `@JsonLazy` annotation that deserializes marked fields on first
access via JDK dynamic proxies. Support configurable prefetch depth,
cycle detection in lazy graphs, serialization of partially-loaded
graphs, and cache eviction for memory management. Changes span
`deser/BeanDeserializer`, `deser/SettableBeanProperty`,
`introspect/BeanDescription`, `DeserializationContext`,
`ser/BeanSerializer` for lazy-aware serialization, `annotation/`
for the new annotation, and a new `lazy/` sub-package.

### W10: Add first-class Kotlin support module

Implement a `KotlinModule` that handles: data class deserialization
with default parameter values, `null` safety enforcement matching
Kotlin's type system (`String` vs `String?`), `value class` (inline
class) unwrapping, sealed class polymorphism via `@JsonTypeInfo`
inference, `companion object` factory detection, and coroutine
`suspend` function serialization. Changes span a new
`module/kotlin/` sub-package, `introspect/` for Kotlin metadata
reading, `deser/ValueInstantiator` for default-value constructors,
`type/TypeFactory` for Kotlin type mapping, and `ser/` for value
class handling.
