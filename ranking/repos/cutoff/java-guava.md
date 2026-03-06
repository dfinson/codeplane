# google/guava

| Field | Value |
|-------|-------|
| **URL** | https://github.com/google/guava |
| **License** | Apache-2.0 |
| **Language** | Java |
| **Scale** | Large |
| **Category** | Core utility library |
| **Set** | Cutoff |

## Why this repo

- **Well-structured**: Organized under `guava/src/com/google/common/`
  with clear per-domain packages — `collect/` (immutable collections,
  multimaps, tables), `base/` (preconditions, strings, predicates),
  `cache/` (loading caches), `io/` (byte/char streams), `hash/`
  (hashing, Bloom filters), `util/concurrent/` (listenable futures, rate
  limiters), `primitives/`, `math/`, `net/`, and `eventbus/`. Each
  package has a focused API surface with clear internal layering.
- **Rich history**: 50K+ stars, 10K+ commits. One of the most widely
  used Java libraries. Issues cover correctness, performance, and API
  design across dozens of utility domains.
- **Permissive**: Apache-2.0 license.

## Structure overview

```
guava/src/com/google/common/
├── collect/               # ImmutableList, ImmutableMap, ImmutableSet, Multimap,
│                          #   Table, BiMap, Multiset, Iterables, Lists, Sets, Maps
├── base/                  # Preconditions, Optional, Strings, Joiner, Splitter,
│                          #   Predicate, Function, Supplier, MoreObjects
├── cache/                 # CacheBuilder, LoadingCache, CacheLoader, CacheStats,
│                          #   LocalCache (internal), RemovalNotification
├── io/                    # ByteStreams, CharStreams, Files, Resources,
│                          #   ByteSource, ByteSink, CharSource, CharSink
├── hash/                  # Hashing, HashFunction, Hasher, HashCode, BloomFilter,
│                          #   Funnel, Funnels
├── util/concurrent/       # ListenableFuture, Futures, MoreExecutors,
│                          #   RateLimiter, Service, ServiceManager
├── primitives/            # Ints, Longs, Doubles, Bytes, Shorts, Floats, Chars,
│                          #   UnsignedInteger, UnsignedLong
├── math/                  # IntMath, LongMath, DoubleMath, BigIntegerMath,
│                          #   Stats, Quantiles
├── net/                   # InternetDomainName, InetAddresses, HostAndPort,
│                          #   MediaType, UrlEscapers, HttpHeaders
├── eventbus/              # EventBus, AsyncEventBus, Subscribe, DeadEvent,
│                          #   SubscriberExceptionHandler
├── graph/                 # Graph, ValueGraph, Network, MutableGraph,
│                          #   GraphBuilder, Traverser
├── escape/                # Escaper, CharEscaperBuilder, HtmlEscapers,
│                          #   XmlEscapers
├── reflect/               # TypeToken, Invokable, ClassPath, TypeResolver
├── annotations/           # Beta, VisibleForTesting, GwtCompatible
└── html/                  # HtmlEscapers (moved from escape/)
```

## Scale indicators

- ~800 Java source files under guava/src/
- ~300K lines of code
- Deep package hierarchy (3–5 levels)
- Zero external dependencies (JDK only)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix ImmutableList.copyOf discarding null-check for single-element iterables

When `ImmutableList.copyOf()` receives an `Iterable` with exactly one
element, it short-circuits to `ImmutableList.of(element)` but skips the
explicit `checkNotNull()` that the multi-element path performs. A single
`null` element silently creates a broken immutable list instead of
throwing `NullPointerException`. Fix the single-element fast path in
`ImmutableList.copyOf()` to validate the element before wrapping.

### N2: Fix Joiner.on() not rejecting null separator

`Joiner.on(String)` does not validate that the separator is non-null.
Passing a `null` separator produces a `NullPointerException` deep in
`StringBuilder.append()` during join, with an unhelpful stack trace.
Fix `Joiner.on()` to call `Preconditions.checkNotNull()` on the
separator in the factory method so the error points to the caller.

### N3: Fix CacheBuilder.maximumSize not validating negative values

`CacheBuilder.maximumSize(long)` does not reject negative values. A
call like `CacheBuilder.newBuilder().maximumSize(-1)` silently creates
a cache that immediately evicts all entries rather than throwing
`IllegalArgumentException`. Fix `maximumSize()` to validate the
argument with `checkArgument(size >= 0)`.

### N4: Fix Splitter.splitToList returning mutable list

`Splitter.splitToList(CharSequence)` returns a mutable `ArrayList`
despite its Javadoc promising an immutable list. Callers who rely on
immutability may experience subtle bugs if the returned list is
modified. Fix the method to wrap the result with
`Collections.unmodifiableList()` or return an `ImmutableList`.

### N5: Fix BloomFilter.mightContain throwing ArithmeticException on zero-capacity filter

Creating a `BloomFilter` with `expectedInsertions=0` and then calling
`mightContain()` throws an `ArithmeticException` due to division by
zero in the hash-bucket calculation. Fix the optimal-bucket computation
in `BloomFilter.create()` to treat zero expected insertions as 1,
producing a minimal-capacity filter.

### N6: Fix Strings.padEnd not handling negative minLength

`Strings.padEnd(String, int, char)` does not check for negative
`minLength`. Passing a negative value silently returns the original
string, which masks caller bugs. Fix the method to throw
`IllegalArgumentException` when `minLength < 0`, consistent with
`Strings.padStart()` behavior.

### N7: Fix InternetDomainName.isValid accepting trailing whitespace

`InternetDomainName.isValid(String)` returns `true` for strings with
trailing whitespace like `"example.com "`. The subsequent
`InternetDomainName.from()` then throws an exception, making `isValid`
unreliable as a guard. Fix the validation in `InternetDomainName` to
trim or reject whitespace in the `isValid()` check.

### N8: Fix RateLimiter.create(0) not throwing on zero permits-per-second

`RateLimiter.create(0.0)` creates a rate limiter that blocks forever
on the first `acquire()` call instead of throwing
`IllegalArgumentException`. Fix `RateLimiter.create()` to validate that
the permits-per-second argument is positive.

### N9: Fix TypeToken.getRawType losing generic info for wildcard types

When `TypeToken.of(wildcardType).getRawType()` is called with a
wildcard type like `? extends Comparable`, the method returns
`Object.class` instead of `Comparable.class`. Fix `getRawType()` in
`TypeToken` to resolve the upper bound of wildcard types before
extracting the raw class.

### N10: Fix EventBus.post swallowing exceptions from @Subscribe methods silently

When a `@Subscribe` method throws an unchecked exception, the default
`EventBus` logs it via `SubscriberExceptionHandler` but provides no
access to the failed event or subscriber method identity. Fix the
default handler in `EventBus` to include the subscriber method name
and event class in the logged message for debuggability.

## Medium

### M1: Add Cache.policy() runtime introspection API

Add a `CachePolicy` value object and `Cache.policy()` method that
exposes the eviction configuration of a built cache at runtime:
maximum size, expire-after-write duration, expire-after-access
duration, refresh-after-write duration, and whether statistics
recording is enabled. Currently callers must track `CacheBuilder`
settings externally. Changes span `Cache.java` for the new interface
method, a new `CachePolicy.java` value object, `CacheBuilder.java` for
capturing policy metadata, and `LocalCache.java` for implementing the
`policy()` method from the internal segment state.

### M2: Implement CacheBuilder.writer for synchronous write-through hooks

Add a `CacheWriter<K, V>` interface with `write(K key, V value)` and
`delete(K key, V value, RemovalCause cause)` callbacks invoked
synchronously during cache mutations. `CacheBuilder.writer(CacheWriter)`
configures the writer. The writer's `delete()` is called on explicit
`invalidate()`, size-eviction, and expiration-eviction, while `write()`
is called on `put()` and `LoadingCache.get()` loads. Changes span a new
`CacheWriter.java` interface, `CacheBuilder.java` for configuration
and validation, `LocalCache.java` for invoking the writer hooks in
segment put and remove paths, and `RemovalCause.java` for cause
context.

### M3: Add topological sort to the graph package

Implement `Graphs.topologicalOrder(Graph<N>)` returning an
`ImmutableList<N>` of nodes in topological order for directed acyclic
graphs, throwing `IllegalArgumentException` on cyclic input (detected
via the existing `Graphs.hasCycle()` method). The implementation must
work with `Graph`, `ValueGraph`, and `Network` via the
`SuccessorsFunction` abstraction, using Kahn's algorithm for O(V+E)
performance. Changes span `Graphs.java` for the new public API
method, internal helper classes for in-degree tracking, and
`Traverser.java` for shared iteration infrastructure.

### M4: Add EventBus subscriber introspection and priority ordering

Implement `EventBus.getRegisteredSubscribers()` that returns a
structured view of all registered subscriber methods grouped by event
type. Add a `@SubscriberPriority(int)` annotation to control dispatch
order for multiple subscribers of the same event type (lower values
execute first). Changes span `EventBus.java` for the introspection
API, `SubscriberRegistry.java` for priority-sorted subscriber
maintenance, `Subscriber.java` for priority extraction from the
annotation, and a new `SubscriberPriority.java` annotation class in
the `eventbus/` package.

### M5: Add RateLimiter warmup introspection and reset support

Implement `RateLimiter.getAvailablePermits()` returning the estimated
number of permits currently available without blocking,
`RateLimiter.reset()` to clear accumulated permits and restart the
warmup period from cold state, and `RateLimiter.isWarmingUp()` to
query whether the limiter is still in its warmup phase. These methods
enable monitoring dashboards and operational tooling to inspect rate
limiter state at runtime. Changes span `RateLimiter.java` for the new
public API methods, `SmoothRateLimiter.java` for warmup state
introspection and reset logic, `SmoothRateLimiter.SmoothWarmingUp` for
warmup phase detection, and `SmoothRateLimiter.SmoothBursty` for
bursty reset behavior.

### M6: Add bounded-size strong interner with LRU eviction

Implement `Interners.newBoundedStrongInterner(int maxSize)` that
provides strong-reference interning with a configurable maximum number
of interned instances. When the bound is reached, the least-recently-
used interned value is evicted. The current `newStrongInterner()` grows
unboundedly and can cause memory exhaustion for large domains. Changes
span `Interners.java` for the new factory method, a new
`BoundedStrongInterner.java` implementation using an LRU eviction
data structure, and `MapMaker.java` for the underlying concurrent map
configuration.

### M7: Add ServiceManager health-check endpoint support

Extend `ServiceManager` to expose a structured health-check report:
`ServiceManager.healthReport()` returning service name, state, startup
duration, last failure cause, and uptime for each managed service. Add
`ServiceManager.awaitHealthy(Duration, HealthCriteria)` that accepts
custom health criteria. Changes touch `ServiceManager`, `Service`,
`AbstractService`, and the service state machine in
`util/concurrent/`.

### M8: Implement CharMatcher composition optimizations

Optimize `CharMatcher.or()`, `and()`, and `negate()` chains by
collapsing nested compositions into flat bitset-based matchers when
all operands are BMP characters. Add `CharMatcher.forPredicate()` for
interop with `java.util.function.IntPredicate`. Changes span
`CharMatcher`, its internal `FastMatcher` subclasses, and the
`base/` package's `Predicate` adapter.

### M9: Add weighted keys support to BloomFilter

Extend `BloomFilter` to support weighted insertions where high-priority
items receive more hash bits for lower false-positive rates. Add
`BloomFilter.create(Funnel, long, double, WeightFunction)` and update
`mightContain()` to respect the weight distribution. Changes span
`BloomFilter`, `BloomFilterStrategies`, the `Funnel` interface, and
the hash-function internals in `hash/`.

### M10: Add counting BloomFilter variant with element removal support

Implement `CountingBloomFilter<T>` backed by a counter array instead
of a bit array, supporting `remove(T)` in addition to `put(T)` and
`mightContain(T)`. Each hash position maintains a 4-bit counter that
increments on `put` and decrements on `remove`, with overflow
detection that saturates at the maximum counter value to prevent
wrap-around corruption. Include `CountingBloomFilter.create(Funnel,
long, double)` factory mirroring the standard `BloomFilter` API.
Changes span a new `CountingBloomFilter.java` class in `hash/`, a new
`CountingBloomFilterStrategies.java` for counter-based hash layouts,
`BloomFilterStrategies.java` for shared bit-manipulation
infrastructure, and the `Funnel` interface for shared funnel
compatibility.

## Wide

### W1: Implement a Guava-to-Java-8 migration module

Create a `com.google.common.migration` package that provides adapters
and automated migration helpers: `com.google.common.base.Optional` ↔
`java.util.Optional`, `Function`/`Predicate`/`Supplier` ↔ `java.util
.function` equivalents, `FluentIterable` → `Stream`, `ListenableFuture`
→ `CompletableFuture`, and `CacheBuilder` → `Caffeine` adapter. Include
static analysis annotations marking deprecated Guava APIs with their
Java 8+ replacements. Changes span `base/`, `collect/`, `cache/`,
`util/concurrent/`, and a new `migration/` package.

### W2: Add reactive streams support to util.concurrent

Implement `Publisher` and `Subscriber` adapters for
`ListenableFuture`, `Service`, and `ServiceManager`: stream of service
state transitions, future-to-publisher conversion, publisher-to-future
conversion, and backpressure handling. Add
`MoreExecutors.reactiveExecutor()` for scheduling. Changes span
`util/concurrent/`, new reactive adapter classes, `Service`,
`ServiceManager`, `ListenableFuture`, and `Futures`.

### W3: Implement persistent (immutable) collection variants

Add `PersistentList`, `PersistentMap`, `PersistentSet` backed by
hash-array mapped tries (HAMTs) offering O(log32 N) updates that
share structure with previous versions. Include `TransientList`/
`TransientMap` for batched mutations. Integrate with `ImmutableList
.toPersistent()` bridge methods. Changes span new classes in
`collect/`, integration with the existing immutable collection
hierarchy, and the iterator framework.

### W4: Implement comprehensive graph algorithm library

Extend the `graph/` package with shortest-path (Dijkstra, Bellman-Ford),
minimum spanning tree (Kruskal, Prim), topological sort, strongly
connected components (Tarjan), max-flow (Edmonds-Karp), and bipartite
matching. Each algorithm must work with `Graph`, `ValueGraph`, and
`Network` generically. Changes span `graph/`, new algorithm classes,
result types, and integration with `Traverser`.

### W5: Add serialization framework for Guava types

Implement a pluggable serialization framework so all Guava collection
types, `Optional`, `Table`, `RangeSet`, `Multimap`, `TypeToken`, and
graph types can be serialized to JSON, Protocol Buffers, or custom
formats. Add `GuavaSerializers.json()`, `GuavaSerializers.proto()`, and
a `SerializerModule` SPI. Changes span `collect/`, `base/`, `graph/`,
`hash/`, `reflect/`, and a new `serial/` package.

### W6: Implement a cache monitoring and management system

Add JMX and micrometer integration to `CacheBuilder`-built caches:
expose hit/miss/eviction/load metrics as MBeans and Micrometer meters,
add `Cache.policy()` for runtime inspection of eviction policies, and
implement `Cache.asMap().listeners()` for entry-level event
notification. Add a cache inspector that can dump cache contents and
statistics to structured output. Changes span `cache/`, `util/concurrent/`
for thread-pool metrics, and a new `cache/management/` sub-package.

### W7: Add defensive-copy and immutability-enforcement annotations

Implement `@Immutable`, `@DeepImmutable`, `@ReturnsCopy`, and
`@AcceptsOnly(immutable=true)` annotations with a compile-time
annotation processor that validates contracts. The processor should
detect mutable fields in `@Immutable` classes, missing defensive copies
on `@ReturnsCopy` methods, and mutable parameters on `@AcceptsOnly`
methods. Changes span `annotations/`, a new `annotations/processor/`
package, and retroactive annotation of core Guava types in `collect/`,
`base/`, and `cache/`.

### W8: Implement a type-safe heterogeneous container framework

Add `TypeSafeMap`, `TypeSafeMultimap`, and `TypeSafeTable` that use
`TypeToken` keys for type-safe heterogeneous storage, replacing the
`Class`-based pattern from Effective Java. Support bounded wildcards,
parameterized types, and serialization. Include `TypeSafeCache` backed
by `CacheBuilder`. Changes span `collect/`, `reflect/TypeToken`,
`cache/`, and new container classes.

### W9: Implement a functional data transformation pipeline

Add `Pipeline<I, O>` in `base/` that composes functions, predicates,
and side-effects into replayable, inspectable transformation chains.
Support branching (`Pipeline.branch(predicate, ifTrue, ifFalse)`),
error handling (`Pipeline.recover()`), async stages backed by
`ListenableFuture`, and metric instrumentation. Integrate with
`FluentIterable.transform()` and `Streams.pipeline()`. Changes span
`base/`, `collect/`, `util/concurrent/`, and `hash/` for content-based
deduplication stages.

### W10: Add multi-release JAR support with JDK-optimized implementations

Restructure Guava to produce a multi-release JAR with JDK 11, 17, and
21 optimized implementations: `VarHandle`-based `Striped` locks,
`MemorySegment`-backed `ByteSource`, virtual-thread-aware
`MoreExecutors`, `SequencedCollection`-backed immutable collections,
and `ScopedValue`-based context propagation replacing `ThreadLocal`
in `EventBus`. Changes span `util/concurrent/`, `io/`, `collect/`,
`eventbus/`, and build configuration.
