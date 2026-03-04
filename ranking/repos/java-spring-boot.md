# spring-projects/spring-boot

| Field | Value |
|-------|-------|
| **URL** | https://github.com/spring-projects/spring-boot |
| **License** | Apache-2.0 |
| **Language** | Java |
| **Scale** | Large (multi-team project) |
| **Category** | Application framework |

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
spring-boot-project/
├── spring-boot/                         # Core classes
│   └── src/main/java/org/springframework/boot/
│       ├── SpringApplication.java       # Application bootstrap
│       ├── context/                     # ApplicationContext setup
│       ├── env/                         # Environment, property sources
│       ├── web/                         # Embedded server support
│       ├── logging/                     # Logging system abstraction
│       └── diagnostics/                 # Failure analysis
├── spring-boot-autoconfigure/           # Auto-configuration engine
│   └── src/main/java/.../autoconfigure/
│       ├── web/                         # Web auto-config (servlet, reactive)
│       ├── data/                        # Data source auto-config (JPA, Mongo, Redis)
│       ├── security/                    # Security auto-config
│       ├── cache/                       # Cache auto-config
│       └── ...                          # 100+ technology auto-configs
├── spring-boot-actuator/                # Production-ready features
│   └── src/.../actuator/
│       ├── health/                      # Health indicators
│       ├── metrics/                     # Micrometer metrics
│       └── endpoint/                    # Management endpoints
├── spring-boot-test/                    # Test framework
├── spring-boot-devtools/                # Hot reload, LiveReload
└── spring-boot-docker-compose/          # Docker Compose integration
```

## Scale indicators

- ~5,000 Java source files
- ~500K+ lines of code
- Deep module hierarchies (5+ levels)
- Cross-cutting auto-configuration, condition evaluation, property binding

---

## Tasks

10 tasks (3 narrow, 4 medium, 3 wide) for the Java application framework.

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

## Medium

### M1: Implement feature flags with autoconfiguration

Add a `spring-boot-starter-feature-flags` module that provides feature
flag support. Include an in-memory flag store (default) with a SPI for
external providers (LaunchDarkly, Unleash). Add `@ConditionalOnFeature`
for conditional bean registration, `@FeatureFlag` injectable annotation,
and an actuator endpoint for listing/toggling flags. Support percentage
rollouts and user-targeted flags.

### M2: Add virtual thread auto-configuration for Tomcat

Add auto-configuration that detects Java 21+ and configures Tomcat
to use virtual threads for request handling. Replace the platform
thread executor with a virtual thread executor. Add a configuration
property `server.tomcat.threads.virtual=true` (default false). Include
metrics for virtual thread utilization. Ensure compatibility with
thread-local-dependent Spring features (transactions, security context).

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
