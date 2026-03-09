# apache/kafka

| Field | Value |
|-------|-------|
| **URL** | https://github.com/apache/kafka |
| **License** | Apache-2.0 |
| **Language** | Java |
| **Scale** | Large |
| **Category** | Distributed streaming |
| **Set** | Cutoff |
| **Commit** | `7bd979bb4ff842b90ecde4ae8eb6580544f303a9` |

## Why this repo

- **Industry-defining system**: Apache Kafka is the de facto standard
  for distributed event streaming, with a rich multi-module codebase
  covering client libraries (producer, consumer, admin), Kafka Streams
  DSL, Kafka Connect framework, KRaft consensus, group coordination,
  and server internals. The architecture spans distributed protocols,
  storage engines, serialization, and configuration management.
- **Rich history**: 28K+ stars, extensive contributor base. Covers
  distributed systems edge cases, protocol versioning, and backward
  compatibility across major releases.
- **Permissive**: Apache-2.0 license.
- **Scale anchor**: Large codebase (6000+ Java source files across
  30+ modules) anchoring the "Large" end of scale assessment.

## Structure overview

```
clients/src/main/java/org/apache/kafka/
├── clients/
│   ├── producer/         # KafkaProducer, ProducerRecord, Partitioner, callbacks
│   ├── consumer/         # Consumer interface, KafkaConsumer (deprecated name),
│   │                     #   ConsumerRecord, rebalance listeners, assignors
│   ├── admin/            # Admin client — topic/ACL/config management
│   ├── CommonClientConfigs.java  # Shared client configuration keys
│   ├── Metadata.java     # Cluster metadata management
│   └── NetworkClient.java # Network I/O layer
├── common/
│   ├── serialization/    # Serializer/Deserializer interfaces + built-in impls
│   ├── config/           # ConfigDef — declarative config definition framework
│   ├── metrics/          # Metrics collection (Sensor, MetricName, reporters)
│   ├── security/         # SASL/SSL authentication, ACL models
│   ├── record/           # Record batch format, compression, memory records
│   ├── protocol/         # Wire protocol types, API keys, request/response
│   ├── Cluster.java      # Cluster topology model
│   ├── Node.java         # Broker node representation
│   └── KafkaFuture.java  # Composable future for admin operations
streams/src/main/java/org/apache/kafka/streams/
├── KafkaStreams.java      # Top-level Streams application lifecycle
├── StreamsBuilder.java   # DSL entry point — stream/table/globalTable
├── StreamsConfig.java    # Streams configuration
├── kstream/              # DSL types — KStream, KTable, KGroupedStream,
│                         #   JoinWindows, Materialized, Consumed, Produced
├── processor/            # Processor API — ProcessorContext, StateStore, Punctuator
│   └── api/              # Processor supplier, record
├── state/                # State store interfaces and built-in implementations
server/src/main/java/org/apache/kafka/server/
├── BrokerLifecycleManager.java  # Broker registration and fencing
├── FetchSession.java            # Incremental fetch session management
├── ApiVersionManager.java       # API version negotiation
connect/
├── api/src/main/java/…/connect/  # Connect API — Connector, Task, transforms
│   ├── connector/        # Connector, ConnectorContext, ConnectRecord
│   ├── data/             # Schema, Struct, SchemaBuilder, type converters
│   ├── source/           # SourceConnector, SourceTask, offsets
│   └── sink/             # SinkConnector, SinkTask
├── runtime/              # Connect runtime — worker, herder, converters
├── transforms/           # Built-in SMTs (Single Message Transforms)
├── json/                 # JSON converter
metadata/                 # KRaft metadata management
raft/                     # KRaft consensus implementation
group-coordinator/        # Consumer group coordination
coordinator-common/       # Shared coordinator infrastructure
server-common/            # Server utilities — event queue, timeline data structures
```

## Scale indicators

- 6,000+ Java source files across 30+ Gradle modules
- Multi-level hierarchy: clients, streams, connect, server, raft, metadata
- Extensive protocol versioning (100+ API keys)
- Complex build system (Gradle multi-project)

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Add ProducerRecord.Builder for fluent construction

`ProducerRecord` in `clients/producer/ProducerRecord.java` uses a
constructor with up to 6 parameters (topic, partition, timestamp, key,
value, headers), making construction error-prone. Add a static
`ProducerRecord.builder(topic, value)` that returns a `Builder` with
`.partition()`, `.key()`, `.timestamp()`, and `.headers()` methods,
validating that `topic` is non-null.

### N2: Add partitioner invocation tracking to MockProducer

`MockProducer` in `clients/producer/MockProducer.java` provides a
test double for `KafkaProducer` but does not track how many times the
configured `Partitioner` is actually invoked. The private `partition()`
helper correctly skips the partitioner when `record.partition() != null`,
but there is no way for test code to assert partitioner interaction counts.
Add an `int` field `partitionerCallCount` to `MockProducer`, increment it
inside the `partition()` helper each time the partitioner is consulted
(i.e., when `record.partition() == null` and a non-null `Partitioner` is
set), and expose it via a public `partitionerCallCount()` accessor method.
Also add a `resetPartitionerCallCount()` method so tests can reset the
counter between successive assertions in the same test instance.

### N3: Add ConsumerRecord.hasKey() and hasValue() convenience methods

`ConsumerRecord` in `clients/consumer/ConsumerRecord.java` stores
`key` and `value` as nullable fields, but there are no convenience
methods to check for null without retrieving the value. Add
`hasKey()` and `hasValue()` methods returning `boolean` that check
for non-null key and value respectively, reducing null-check
boilerplate in consumer loops.

### N4: Fix ConfigDef.validate() not detecting circular dependent config chains

The `ConfigDef` framework in `common/config/ConfigDef.java` allows
declaring config dependencies via `dependents()`. The private
`validate(String, Map, Map)` and `parseForValidate()` methods process
dependents recursively, but neither maintains a visited set to detect
cycles. When config A lists B as a dependent and B lists A as a
dependent, calling `validate(props)` causes infinite recursion ending
in `StackOverflowError`. Add cycle detection in `validateAll()` using
a `Set<String>` of in-progress keys, throwing `ConfigException` with
a descriptive message (listing the cycle path) when a cycle is
detected.

### N5: Add Header.toString() with value decoding

The `Header` interface in `common/header/Header.java` stores values as
`byte[]`. The existing `RecordHeader.toString()` in
`common/header/internals/RecordHeader.java` renders the value using
`Arrays.toString(value())`, which produces unreadable decimal byte
sequences (e.g., `[72, 101, 108, 108, 111]` for `Hello`) rather than
human-readable text. Override `toString()` in `RecordHeader` to
attempt decoding the value as UTF-8 (with fallback to hex representation
for non-UTF-8 bytes) for readable log output.

### N6: Fix RoundRobinPartitioner not handling topic additions

The `RoundRobinPartitioner` in `clients/producer/RoundRobinPartitioner.java`
uses a `ConcurrentMap<String, AtomicInteger>` to track per-topic
counters, but when a new topic is added at runtime (via metadata
update), the first partition assignment always starts at 0. Initialize
new topic counters with `ThreadLocalRandom.current().nextInt()` to
distribute initial load evenly across partitions.

### N7: Fix Serdes.serdeFrom() not handling primitive type class literals

The `Serdes.serdeFrom(Class<T> type)` method in
`common/serialization/Serdes.java` (line ~157) returns the built-in
`Serde<T>` for boxed wrapper types (`Integer.class`, `Long.class`,
`Double.class`, etc.), but throws `IllegalArgumentException` for Java
primitive type class literals (`int.class`, `long.class`,
`double.class`, `float.class`, `short.class`, `boolean.class`).
Generic reflective code that obtains class literals from field types
may produce primitive classes, causing unexpected failures. Extend
`serdeFrom(Class<T>)` to map each primitive class to its corresponding
boxed-type serde (`int.class` → `Integer()`, `long.class` → `Long()`,
`double.class` → `Double()`, `float.class` → `Float()`,
`short.class` → `Short()`, `boolean.class` → `Boolean()`).

### N8: Fix KafkaFuture.thenApply not preserving exception context

`KafkaFuture.thenApply()` in `common/KafkaFuture.java` wraps
the mapping function but does not preserve the original exception's
stack trace when the function itself throws. The resulting
`ExecutionException` loses the cause chain. Wrap function exceptions
in a new exception that preserves both the mapping failure and any
original cause.

### N9: Add StreamsConfig.validate() for topology-aware config checking

`StreamsConfig` in `streams/StreamsConfig.java` validates individual
config values but does not cross-check against the topology (e.g.,
`num.standby.replicas` > 0 requires state stores, `processing.guarantee`
= `exactly_once_v2` requires specific broker versions). Add a
`validate(Topology)` method that checks config values against topology
requirements and returns a list of warnings.

### N10: Fix JoinWindows factory methods not rejecting zero-duration windows

`JoinWindows.ofTimeDifferenceAndGrace()` and
`ofTimeDifferenceWithNoGrace()` in `streams/kstream/JoinWindows.java`
delegate duration validation to `validateMillisecondDuration()`, which
only rejects `null` and overflow — it does not reject non-positive
values. The constructor guard (`beforeMs + afterMs < 0`) catches
strictly-negative timeDifference but allows `Duration.ZERO`, producing
a zero-width window where only records with exactly identical timestamps
join, which silently yields no results in practice. Add an explicit
`IllegalArgumentException` for `timeDifference` of zero or negative
milliseconds in both non-deprecated factory methods and in the
deprecated `of(Duration)` method to make the contract consistent and
discoverable.

## Medium

### M1: Implement dead-letter queue support for consumer deserialization failures

When `KafkaConsumer.poll()` encounters a deserialization error, it
throws `SerializationException`, which halts processing. Implement a
configurable `DeserializationExceptionHandler` that routes failed
records to a dead-letter topic with headers containing the original
topic, partition, offset, and exception message. Requires changes to
the consumer fetcher, a new handler interface in `common/serialization/`,
and configuration in `ConsumerConfig`.

### M2: Add record header propagation to Kafka Streams

Records flowing through Kafka Streams DSL operations (`map`,
`filter`, `groupByKey`) do not automatically propagate headers from
input to output records. Implement configurable header propagation
policies: `ALL` (forward all), `NONE`, and `SELECTED` (by key
pattern). Requires changes to the streams processor context, the
record forwarding pipeline in `processor/`, and configuration in
`StreamsConfig`.

### M3: Add per-operation retry configuration to Admin client

The `KafkaAdminClient` in `clients/admin/KafkaAdminClient.java` uses
global exponential backoff for all operations, configured via
`RETRY_BACKOFF_MS_CONFIG` and `RETRY_BACKOFF_MAX_MS_CONFIG` in
`AdminClientConfig`. Different admin operations have different
failure characteristics — `createTopics` warrants more retries than
`describeConfigs`, and `deleteTopics` should not be blindly retried.
Add per-operation retry configuration with a key pattern
`admin.retry.<operation>.max.attempts` (e.g.,
`admin.retry.createTopics.max.attempts`) in `AdminClientConfig`, and
update `KafkaAdminClient`'s call construction to apply per-operation
retry counts and retryable exception classification when present,
falling back to the global `retries` setting. Touches
`AdminClientConfig`, the `Call` inner class, and the `KafkaFuture`
timeout mechanism.

### M4: Add schema evolution support to JSON converter

The JSON converter in `connect/json/` serializes Connect schemas
but does not handle schema evolution (adding fields, removing fields,
changing types). Implement forward/backward/full compatibility
checking for JSON schemas, with configurable compatibility mode and
clear error messages for incompatible schema changes. Touches the
JSON converter (`connect/json/src/main/java/…/connect/json/JsonConverter.java`),
`connect/api/src/main/java/org/apache/kafka/connect/data/Schema.java`,
and a new compatibility checker module in `connect/json/`.

### M5: Implement configurable record timestamp policies for Streams

Kafka Streams uses `TimestampExtractor` to assign timestamps but
does not support per-operation timestamp policies (e.g., use event
time for joins but wall-clock time for windowed aggregations). Add
a `Consumed.withTimestampPolicy(TimestampPolicy)` method that
configures per-source-node timestamp extraction with fallback to the
global extractor. Touches `StreamsBuilder`, `Consumed`, and the
processor topology builder.

### M6: Add Connect task progress reporting

Connect tasks run indefinitely but provide no structured progress
reporting beyond metrics. Implement a `TaskProgressReporter` interface
that tasks can use to report: records processed, bytes transferred,
current position (e.g., file offset, database cursor), and
estimated completion for bounded tasks. Expose progress via the
Connect REST API. Touches `connect/api/`, `connect/runtime/`, and
the REST endpoints.

### M7: Implement topic-level producer batching configuration

`KafkaProducer` uses global `batch.size` and `linger.ms` settings,
but different topics may have different latency/throughput
requirements. Add `ProducerConfig.withTopicOverrides(topic, overrides)`
that allows per-topic batch size and linger configuration. Requires
changes to the record accumulator's batching logic, `ProducerConfig`,
and the sender thread's drain logic.

### M8: Add KStream.peek() with error handling

`KStream.peek()` in `streams/kstream/KStream.java` accepts a
`ForeachAction` that can throw unchecked exceptions, which kills
the stream thread. Add a `peekOrHandle(action, errorHandler)` variant
that catches exceptions from the action, routes them to the error
handler with the record context, and continues processing. Requires
changes to `KStream`, the `ForeachProcessor`, and the processor
node builder.

### M9: Implement config encryption for sensitive values

Configuration values like passwords and API keys are stored in
plaintext in `server.properties` and Connect worker configs. Add a
`ConfigProvider` that encrypts sensitive config values at rest using
a master key, with support for AES-256-GCM encryption, key rotation,
and integration with the existing `ConfigProvider` SPI. Touches
`common/config/`, the config provider framework, and documentation.
Also update `config/server.properties` to add commented-out example
configuration for the encryption provider, and update
`docs/security/` documentation with a "Config Encryption" guide.

### M10: Add Streams topology optimization hints

The Streams DSL builder performs topology optimization (merge
repartition topics, reuse state stores) but provides no visibility
into what optimizations were applied or missed. Add
`StreamsBuilder.describeOptimizations()` that returns a report of
applied and available optimizations, with suggestions for improving
the topology structure. Touches `StreamsBuilder`, the topology
optimizer, and a new `OptimizationReport` class.

## Wide

### W1: Implement exactly-once semantics for Connect source connectors

Kafka Connect source connectors currently provide at-least-once
delivery. Implement exactly-once semantics by coordinating source
offset commits with producer transactions. Requires changes to the
Connect worker's offset management, the source task execution loop,
transaction fence management for zombie tasks, the herder for
task assignment, and the REST API for EOS configuration. Changes span
`connect/runtime/`, `connect/api/`, coordinator communication, and
the producer integration layer.

### W2: Add multi-cluster topic mirroring with conflict resolution

Implement a built-in cross-cluster replication framework (beyond
MirrorMaker 2) that supports bidirectional replication with
conflict resolution policies (last-writer-wins, merge,
application-defined). Requires changes to the replication protocol,
a new conflict resolution interface, cluster metadata exchange,
offset translation, consumer group mirroring, and ACL
synchronization. Changes span a new `mirror/` module, `clients/`,
`server/`, and configuration.

### W3: Implement a SQL query layer over Kafka topics

Add a SQL interface (`SELECT * FROM topic WHERE key = 'x' AND
timestamp > '2024-01-01'`) that compiles SQL queries into Kafka
Streams topologies, executes them against live topics or materialized
views, and returns results via a JDBC-compatible interface. Requires
a SQL parser, query planner, Streams-based executor, result
materialization, JDBC driver, and a query server. Changes span new
`sql/` modules, `streams/`, `clients/`, and REST API endpoints.

### W4: Add end-to-end encryption for record payloads

Implement transparent record-level encryption where producers encrypt
payloads with per-topic keys and consumers decrypt transparently. Key
management should integrate with external KMS (AWS KMS, HashiCorp
Vault) via a `KeyProvider` SPI. Requires changes to the producer
interceptor chain, consumer interceptor chain, a new `crypto/` module,
key rotation support, header-based key metadata, and serialization
integration. Changes span `clients/producer/`, `clients/consumer/`,
`common/`, and a new `security/crypto/` package.

### W5: Implement a Kafka Streams testing framework with time control

Extend `TopologyTestDriver` with: deterministic time advancement
(tick-by-tick), multi-partition simulation, state store snapshots and
assertions, record-by-record processing with checkpoint/restore,
and integration test mode with embedded broker. Include assertion
DSL for output verification. Changes span `streams/test-utils/`,
`TopologyTestDriver`, a new assertion module, embedded broker
integration, and documentation.

### W6: Add observability integration across all components

Implement unified observability covering: OpenTelemetry trace
propagation through produce-consume chains (trace context in headers),
structured logging with correlation IDs, metric export to
Prometheus/OTLP, and health check endpoints for all components.
Requires changes to the producer, consumer, streams processor, Connect
runtime, and server. Changes span `common/metrics/`, `clients/`,
`streams/`, `connect/runtime/`, `server/`, and new OpenTelemetry
integration modules.

### W7: Implement tiered storage for topic partitions

Add a tiered storage layer that moves older log segments from local
disk to remote storage (S3, GCS, HDFS) while maintaining transparent
read access. Requires a `RemoteLogManager` with pluggable storage
backends, segment upload/download coordination, remote log index
maintenance, fetch integration for remote reads, retention policy
enforcement on remote storage, and broker leader/follower
coordination. Changes span `storage/`, `server/`, `server-common/`,
log management, and configuration.

### W8: Add schema registry integration to the client libraries

Implement built-in schema registry support in the producer and
consumer serializers: automatic schema registration on produce,
schema compatibility checking, schema caching, and deserialization
with schema evolution (Avro, Protobuf, JSON Schema). Requires a
schema registry client, schema-aware serializers/deserializers, cache
management, compatibility mode configuration, and Connect integration.
Changes span `common/serialization/`, `clients/`, `connect/json/`,
a new `schema-registry-client/` module, and configuration.

### W9: Implement consumer group protocol v2 with server-side assignment

Redesign the consumer group protocol to move partition assignment to
the server side (group coordinator), eliminating the need for client-
side assignor implementations. Requires changes to the group
coordinator's assignment algorithm, the consumer heartbeat protocol,
the JoinGroup/SyncGroup request handling, client-side simplification,
backward-compatible protocol negotiation, and Streams integration.
Changes span `group-coordinator/`, `clients/consumer/`, `server/`,
protocol definitions, and `streams/`.

### W10: Add multi-tenancy with resource quotas and namespace isolation

Implement multi-tenancy primitives: topic namespaces with isolation
guarantees, per-tenant resource quotas (throughput, storage, connection
count), tenant-aware ACLs, quota enforcement in the request handler,
tenant-scoped metrics, and an admin API for tenant management.
Requires changes to topic naming, the quota manager, ACL evaluator,
request processing pipeline, metrics collection, and the admin client.
Changes span `server/`, `server-common/`, `clients/admin/`, `common/`,
and configuration.

### N11: Consolidate per-file ImportControl suppressions into import-control.xml allow rules

Several source files in the `clients` and `server` modules suppress the
`ImportControl` checkstyle check via individual per-file entries in
`checkstyle/suppressions.xml` — specifically `ApiVersionsResponse.java`,
`BrokerRegistrationRequestTest.java`, `MetadataRequestTest.java`, and
`JaasTestUtils.java` — because they import packages (`org.apache.kafka.common.message.*`
generated protocol classes, or `kafka.security.*` from `core`) not yet
listed as allowed in their respective subpackage rules in
`checkstyle/import-control.xml`. Consolidate these suppressions by:
(1) adding `<allow pkg="org.apache.kafka.common.message" />` entries to
the `requests` subpackage section and the relevant test-only sections in
`checkstyle/import-control.xml`, and (2) removing the now-redundant
individual `ImportControl` suppression entries for those files from
`checkstyle/suppressions.xml`.

### M11: Add Gradle local build-cache activation and developer ergonomics improvements

`gradle.properties` does not set `org.gradle.caching=true`, so Gradle's
local build cache — already structurally configured with `enabled = true`
in `settings.gradle`'s `buildCache` block — is never activated for local
developer builds. (CI jobs already pass `--build-cache` explicitly through
`.github/actions/run-gradle/action.yml` and the validate step in
`.github/workflows/build.yml`, so CI is unaffected.) For local runs,
every `./gradlew` invocation performs full task re-execution instead of
restoring cached outputs. Additionally, `maxTestForks` in `build.gradle`
defaults to `Runtime.runtime.availableProcessors()` with no upper bound,
which can cause memory exhaustion on high-core-count developer machines
when combined with the `maxHeapSize = "3g"` per-JVM cap. Improve the
developer build experience by: (1) adding `org.gradle.caching=true` to
`gradle.properties`; (2) adding `org.gradle.workers.max=8` to cap the
Gradle worker pool for local builds; (3) changing the `maxTestForks`
default in `build.gradle` to `Math.min(Runtime.runtime.availableProcessors(), 8)`
so tests do not over-subscribe RAM on large machines; (4) updating the
`settings.gradle` `buildCache.local` block with a `directory` property
pointing to `"${rootDir}/.gradle/build-cache"` so the local cache can
be targeted by a `**/build-cache/**` `.gitignore` entry; and (5) adding
that `.gitignore` exclusion to avoid accidentally committing cache files.

### W11: Overhaul Gradle build, checkstyle rules, CI workflows, and documentation

Comprehensively update all non-code project files for the Kafka
4.x release. Restructure `build.gradle` to migrate remaining
`apply plugin:` calls to the `plugins {}` DSL for all plugin
applications, update `spotless` configuration to enforce consistent
Java formatting across all 30+ modules, and add `errorprone` static
analysis. Update `checkstyle/checkstyle.xml` to upgrade to Checkstyle
10.x rules, add `MissingJavadocMethod` checks for public APIs, and
update `import-control*.xml` files to reflect the KRaft module
reorganization. Update `gradle/dependencies.gradle` to use a
Gradle version catalog (`gradle/libs.versions.toml`) instead of the
current `ext` variables approach. Update `.github/workflows/ci.yml`
to add JDK 17/21 matrix testing, update `.github/workflows/build.yml`
to add a job that validates the official Docker image build using the
existing `docker_build_and_test.yml` workflow, and extend
`.github/workflows/generate-reports.yml` for automated test report
publication. Update `config/server.properties` and
`config/controller.properties` to add KRaft-mode configuration
examples with inline documentation comments. Update
`docs/operations/` documentation with a "Configuration Reference"
page auto-generated from `ConfigDef` declarations.
Update `CONTRIBUTING.md` to add sections on the KRaft development
workflow, integration test requirements, and the protocol
versioning process. Update `README.md` to refresh the architecture
overview, add a quick-start guide for KRaft mode, and update the
build instructions. Add `Vagrantfile` updates for the development
VM to include JDK 21 and Docker for integration testing.
