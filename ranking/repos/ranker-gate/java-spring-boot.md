# spring-projects/spring-boot

| Field | Value |
|-------|-------|
| **URL** | https://github.com/spring-projects/spring-boot |
| **License** | Apache-2.0 |
| **Language** | Java |
| **Scale** | Large (multi-team project) |
| **Category** | Application framework |
| **Set** | ranker-gate |
| **Commit** | `fd18d6ba968dbce31a793edaf62a39ae0b5ba718` |

## Why this repo

- **No single developer knows it all**: Auto-configuration engine (1000+
  auto-config classes), actuator (health, metrics, endpoints), embedded
  server support (Tomcat, Jetty, Undertow), Spring Data integration, security
  auto-config, test framework, CLI, Docker image building, devtools — each
  a substantial subsystem with deep internals.
- **Well-structured**: Clear Gradle multi-module layout with `spring-boot-project/`
  containing well-named subprojects. Auto-configuration classes follow strict
  naming conventions. Comprehensive Javadoc throughout.
- **Rich history**: 40K+ commits, hundreds of contributors, regular releases.
  Extremely varied PR patterns (bug fixes, new auto-configs, actuator
  endpoints, test improvements).
- **Permissive**: Apache-2.0.

## Structure overview

```
├── core/
│   ├── spring-boot/                         # Core classes
│   │   └── src/main/java/org/springframework/boot/
│   │       ├── SpringApplication.java       # Application bootstrap
│   │       ├── context/                     # ApplicationContext setup, property binding
│   │       ├── env/                         # Environment, property sources
│   │       ├── logging/                     # Logging system abstraction (Logback, Log4j2)
│   │       └── diagnostics/                 # Failure analysis
│   ├── spring-boot-autoconfigure/           # Auto-configuration engine + conditions
│   │   └── src/main/java/.../autoconfigure/
│   │       └── condition/                   # @ConditionalOn* annotations
│   ├── spring-boot-test/                    # Test framework
│   ├── spring-boot-test-autoconfigure/      # Test auto-configuration slices
│   ├── spring-boot-docker-compose/          # Docker Compose integration
│   └── spring-boot-properties-migrator/     # Property migration reporting
├── module/
│   ├── spring-boot-actuator/                # Actuator endpoints (info, beans, logging)
│   ├── spring-boot-actuator-autoconfigure/  # Actuator auto-configuration
│   ├── spring-boot-health/                  # Health indicators and contributors
│   ├── spring-boot-micrometer-metrics/      # Micrometer metrics + MetricsEndpoint
│   ├── spring-boot-tomcat/                  # Embedded Tomcat support
│   ├── spring-boot-jetty/                   # Embedded Jetty support
│   ├── spring-boot-web-server/              # WebServerFactory abstraction
│   ├── spring-boot-webtestclient/           # WebTestClient auto-configuration
│   ├── spring-boot-devtools/                # Hot reload, LiveReload
│   ├── spring-boot-kafka/                   # Kafka auto-configuration
│   ├── spring-boot-r2dbc/                   # R2DBC auto-configuration
│   └── ...                                  # 100+ technology modules
```

## Scale indicators

- ~5,000 Java source files
- ~500K+ lines of code
- Deep module hierarchies (5+ levels)
- Cross-cutting auto-configuration, condition evaluation, property binding

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix actuator health endpoint returning 503 for partial failures

When a health indicator reports DOWN but is configured with
`management.health.*.show-details=always`, the overall health
endpoint returns HTTP 503 even when the DOWN indicator is in a
non-critical group. Fix the health status aggregation to distinguish
between critical and non-critical health groups when determining
the HTTP status code.

### N2: Add `@ConditionalOnExactBean` condition

The existing `@ConditionalOnBean` matches on type assignability,
which causes problems when multiple bean definitions satisfy the
type check. Add `@ConditionalOnExactBean` that matches only when a
bean of the exact specified type (not subtypes) is present in the
context.

### N3: Fix Docker Compose integration not detecting custom compose file names

The `spring-boot-docker-compose` module only looks for `docker-compose.yml`
and `compose.yml` in the project root. If the compose file has a custom
name (set via `COMPOSE_FILE` environment variable or `.env` file), the
integration fails to find it. Fix the compose file discovery to respect
`COMPOSE_FILE` and `.env`.

### N4: Fix property binding failure for records with single-arg constructors

When binding configuration properties to Java records using
`@ConfigurationProperties`, the binder incorrectly treats a record
with a single component as a scalar value rather than a composite.
This causes `BindException` when the record component is itself a
complex type. Fix the `JavaBeanBinder` and `ValueObjectBinder` to
correctly detect record types regardless of component count.

### N5: Fix Logback auto-configuration ignoring custom appender patterns in native image

When running as a GraalVM native image, the Logback auto-configuration
ignores custom `logging.pattern.console` and `logging.pattern.file`
properties because the pattern resolver is initialized before the
environment is fully populated. Fix the initialization ordering in
`LoggingApplicationListener` so that custom patterns are available
when `LogbackLoggingSystem` configures its appenders.

### N6: Fix `@SpringBootTest` random port not propagated to `WebTestClient`

When using `@SpringBootTest(webEnvironment = RANDOM_PORT)` with a
reactive application, the `WebTestClient` bean is sometimes created
with port 0 instead of the actual assigned port because the
`ReactiveWebServerApplicationContext` hasn't finished initialization.
Fix the lazy port resolution in `WebTestClientAutoConfiguration` and
`SpringBootWebTestClientBuilderCustomizer` in the
`spring-boot-webtestclient` module.

### N7: Fix `@ConditionalOnProperty` not supporting relaxed binding for enum values

`@ConditionalOnProperty(havingValue = "ALWAYS")` fails to match
when the actual property value is `always` or `Always`. The condition
evaluator uses exact string comparison instead of the relaxed binding
rules used elsewhere. Fix `OnPropertyCondition` to apply case-insensitive
matching for enum-like property values.

### N8: Fix actuator metrics endpoint OOM with high-cardinality tags

The `/actuator/metrics/{metricName}` endpoint loads all tag values
into memory when listing available tags. For metrics with high-cardinality
tags (e.g., URI paths), this causes `OutOfMemoryError`. Fix the
`MetricsEndpoint` to apply pagination or streaming when enumerating
tag values and add a configurable cardinality limit.

### N9: Fix embedded Jetty graceful shutdown not draining WebSocket connections

The graceful shutdown implementation for embedded Jetty correctly
waits for in-flight HTTP requests but does not track active WebSocket
connections. WebSocket sessions are terminated immediately when the
shutdown signal arrives. Fix the `GracefulShutdown` class in
`module/spring-boot-jetty` and its integration with `JettyWebServer`
to register a shutdown handler that delays completion until active
WebSocket sessions close or the timeout expires.

### N10: Fix `spring-boot-devtools` restart not clearing ThreadLocal state

When devtools triggers a restart, thread-local variables from the
previous classloader generation are not cleared because the restart
only replaces the application classloader, not the threads themselves.
This causes class cast exceptions and stale security contexts. Fix
RestartClassLoader to identify and clear known thread-local holders
on restart.

## Medium

### M1: Implement feature flags with autoconfiguration

Add a `spring-boot-starter-feature-flags` module that provides feature
flag support. Include an in-memory flag store (default) with a SPI for
external providers (LaunchDarkly, Unleash). Add `@ConditionalOnFeature`
for conditional bean registration, `@FeatureFlag` injectable annotation,
and an actuator endpoint for listing/toggling flags. Support percentage
rollouts and user-targeted flags.

### M2: Add auto-configuration for HTTP client retry policies

Add auto-configuration for configurable retry policies in the
`spring-boot-http-client` module. The module already provides
`HttpClientSettings` and request factory builders
(`ReactorClientHttpRequestFactoryBuilder`, `JdkHttpClientBuilder`,
`JettyClientHttpRequestFactoryBuilder`) but has no retry support.
Add properties under `spring.http.client.retry.*` for max-attempts,
backoff-delay, retryable status codes, and timeout-per-attempt.
Create a `RetryableClientHttpRequestFactoryDecorator` that wraps
configured factories with retry logic. Register Micrometer metrics
for retry count and timeout events.

### M3: Implement startup time analysis actuator

Add an actuator endpoint that breaks down application startup time
by phase: context preparation, bean definition scanning, auto-config
evaluation, bean creation (sorted by duration), embedded server
startup, and runner execution. The endpoint should accept a threshold
parameter to filter beans that took longer than N ms to create.
Add a `StartupTimeline` API for programmatic access.

### M4: Add GraalVM native image build report

Implement a build-time report for GraalVM native image compilation
that shows: reflection configuration entries (generated vs manual),
class reachability analysis results, serialization registrations,
resource bundles included, and estimated image size breakdown by
package. Output as JSON and HTML. Integrate into the
`spring-boot-maven-plugin` and `spring-boot-gradle-plugin`.

### M5: Add Quartz scheduler metrics and health auto-configuration

Add auto-configuration to the `spring-boot-quartz` module that
exports Quartz scheduler metrics to Micrometer. The module already
has `QuartzEndpoint`, `QuartzProperties`, and `QuartzAutoConfiguration`
but no metrics integration. Create a `QuartzMeterBinder` that reads
from the Quartz `Scheduler` API and reports active job count, trigger
fire counts, misfire counts, job execution duration histograms, and
thread pool utilization. Add a health indicator that reports DOWN
when the scheduler is in standby or has been shut down. Include
auto-configuration class `QuartzMetricsAutoConfiguration` conditional
on Micrometer and Quartz being on the classpath.

### M6: Implement configuration property migration actuator

Add an actuator endpoint that detects uses of deprecated configuration
properties in the running application and reports migration guidance.
Scan bound property sources against `additional-spring-configuration-metadata.json`
deprecation entries. Show for each deprecated property: the current
value, the replacement property name, the version it was deprecated,
and whether a value has already been set for the replacement. Add
suggestions for `application.properties` rewrites.

### M7: Add auto-configuration for connection pool health diagnostics

Add auto-configuration that exposes connection pool health for all
configured data sources (HikariCP, Tomcat DBCP, DBCP2). Create health
indicators that report active/idle/pending connections, pool
utilization percentage, and connection wait time. Add pool-specific
metrics to the Micrometer registry. Detect connection leaks by
tracking unreturned connections past a configurable threshold and
logging stack traces of the borrowing thread.

### M8: Implement `@SpringBootTest` slice for Kafka

Add a `@KafkaTest` test slice that auto-configures an embedded Kafka
broker, producer and consumer factories, `KafkaTemplate`, and
`@KafkaListener` infrastructure without loading the full application
context. Support custom topic provisioning via `@KafkaTest(topics=...)`.
Include a `KafkaTestUtils` helper for consuming and asserting messages.
Register appropriate auto-configuration exclusions and filters.

### M9: Add Pulsar dead letter topic auto-configuration with monitoring

Add auto-configuration to the `spring-boot-pulsar` module for dead
letter topic (DLT) handling. The module already has
`PulsarAutoConfiguration`, `PulsarProperties`, and
`PulsarContainerFactoryCustomizer` but no DLT support. Add
properties under `spring.pulsar.consumer.dead-letter-policy.*` for
max-redeliver-count, dead-letter-topic name pattern, and retry-letter
topic. Create auto-configuration that applies `DeadLetterPolicy`
to Pulsar consumer builders. Add a health indicator that monitors
DLT message backlog depth and an actuator endpoint listing DLT
message counts per subscription.

### M10: Add WebSocket session tracking and metrics auto-configuration

Add auto-configuration to the `spring-boot-websocket` module for
WebSocket session tracking and metrics. The module already has
`WebSocketMessagingAutoConfiguration` but no observability support.
Create a `WebSocketMetricsAutoConfiguration` that registers a
`WebSocketHandlerDecorator` tracking active session count, message
rates (inbound/outbound), message sizes, and session duration
histograms via Micrometer. Add an actuator endpoint that lists
active WebSocket sessions with their remote address, connection
time, and message counts. Include a health indicator that reports
DOWN when active sessions exceed a configurable threshold.

## Wide

### W1: Implement multi-module application support

Add support for composing a Spring Boot application from multiple
independently deployable modules. Each module can contribute
auto-configurations, web endpoints, and actuator endpoints. Support
dynamic module loading and unloading at runtime without application
restart. Add module lifecycle management, inter-module dependency
declaration, and module-scoped configuration properties.

### W2: Add comprehensive observability auto-configuration

Unify tracing (Micrometer Tracing), metrics (Micrometer), and logging
(SLF4J/Logback) under a single observability auto-configuration.
Auto-instrument all Spring components: web requests, database queries
(JDBC, R2DBC), cache operations, messaging (Kafka, RabbitMQ),
scheduled tasks, and HTTP client calls. Correlate logs with trace
IDs automatically. Add an observability actuator that shows the
current state of all observations.

### W3: Migrate test framework to support parallel test execution

Refactor `spring-boot-test` and `@SpringBootTest` to support running
tests in parallel. The current test context caching assumes serial
execution and shared application contexts. Implement context isolation
per test thread, parallel-safe mock bean injection, per-test database
state (using test containers or schema-per-thread), and parallel-safe
`@DynamicPropertySource`. Add a test execution listener that detects
and reports context leaks across threads.

### W4: Implement configuration property documentation generator

Build a system that generates comprehensive configuration documentation
from all auto-configuration modules. Scan `@ConfigurationProperties`
classes across all modules, extract type information, default values,
deprecation notices, and Javadoc descriptions. Produce outputs in
AsciiDoc, Markdown, and a searchable HTML page. Cross-reference each
property to the auto-configuration class and condition that activates
it. Integrate as a Gradle task and Maven goal. Support incremental
generation detecting only changed modules.

### W5: Add Spring Boot application dependency graph visualizer

Implement a system that analyzes bean definitions, auto-configuration
conditions, and property bindings to produce an interactive dependency
graph. Show which auto-configuration classes are active vs excluded
and why (listing matched and unmatched conditions). Visualize bean
dependency chains and highlight circular references. Provide an
actuator endpoint returning the graph as JSON and a web UI rendered
via a separate starter. Support exporting to DOT format for external
graph tools.

### W6: Implement zero-downtime configuration reload

Add support for reloading externalized configuration without
restarting the application. Watch `application.properties`, config
server sources, and Kubernetes ConfigMap mounts for changes. When
changes are detected, re-bind `@ConfigurationProperties` beans,
refresh conditional beans affected by changed properties, and
publish `ConfigurationChangedEvent`. Add an actuator endpoint for
manual reload. Implement safety checks that validate new values
before applying and roll back on binding errors.

### W7: Implement cross-cutting retry and circuit breaker auto-configuration

Add auto-configuration that integrates Resilience4j with Spring Boot
across all connection-oriented subsystems. Auto-wrap `RestTemplate`,
`WebClient`, JDBC `DataSource`, R2DBC `ConnectionFactory`, Redis
`LettuceConnectionFactory`, and Kafka producer/consumer with retry
and circuit breaker decorators. Expose per-integration configuration
under `spring.resilience4j.*`. Add actuator endpoints showing circuit
breaker state, retry counts, and failure rates. Include health
indicators that report OPEN circuit breakers as DOWN.

### W8: Add multi-tenancy support across data and web layers

Implement multi-tenancy auto-configuration spanning data isolation,
web request routing, and security context. Support schema-per-tenant
and database-per-tenant strategies for JPA, JDBC, and R2DBC. Route
tenant identification from HTTP headers, JWT claims, or subdomain.
Add tenant-aware cache namespacing, message queue partitioning, and
scheduled task scoping. Provide a `TenantContext` API, an actuator
endpoint listing active tenants, and a test utility for specifying
tenant in `@SpringBootTest`.

### W9: Overhaul embedded server abstraction for HTTP/3 and QUIC

Extend the embedded server abstraction (`WebServerFactory` hierarchy)
to support HTTP/3 over QUIC. Add HTTP/3 support for embedded Tomcat
(via a QUIC connector), Jetty (via `http3-server`), and Netty (via
`quiche` or `s2n-quic` bindings). Introduce configuration properties
under `server.http3.*` for enabling QUIC, setting alt-svc headers,
configuring UDP port, and TLS 1.3-only certificate material. Update
`WebTestClient` and `TestRestTemplate` to optionally use HTTP/3.
Add actuator metrics for QUIC connection counts and stream utilization.

### W10: Implement end-to-end integration test framework for multi-module projects

Create a test framework that spins up multiple Spring Boot applications
in a single test JVM for integration testing across microservices.
Support declaring application topology in `@IntegrationTest(apps=...)`
with automatic port assignment and service discovery wiring. Manage
shared infrastructure (databases, message brokers) via Testcontainers.
Provide a `ServiceClient` abstraction that routes inter-service calls
within the JVM. Add assertions for distributed tracing spans,
message delivery, and eventual consistency. Integrate with
`spring-boot-test` context caching for fast re-execution.
