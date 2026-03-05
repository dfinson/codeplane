# nestjs/nest

| Field | Value |
|-------|-------|
| **URL** | https://github.com/nestjs/nest |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Large (multi-team project) |
| **Category** | Enterprise server-side framework |

## Why this repo

- **No single developer knows it all**: Core DI container, HTTP platform
  adapters (Express/Fastify), WebSockets, microservices (TCP, Redis, NATS,
  MQTT, gRPC, Kafka, RabbitMQ), GraphQL integration, scheduling, caching,
  testing utilities, CLI — each a substantial, independently complex subsystem.
- **Well-structured**: Monorepo with `packages/` containing clearly separated
  modules (`@nestjs/core`, `@nestjs/common`, `@nestjs/microservices`,
  `@nestjs/websockets`, `@nestjs/testing`, `@nestjs/platform-express`, etc.).
  Each package has its own internal module hierarchy.
- **Rich history**: 8K+ commits, active PR flow with detailed reviews.
  Enterprise adoption drives realistic, varied development patterns.
- **Permissive**: MIT license.

## Structure overview

```
packages/
├── core/                # DI container, module system, lifecycle hooks
│   ├── injector/        # Dependency injection engine
│   ├── router/          # Route resolution and execution
│   ├── middleware/       # Middleware pipeline
│   ├── interceptors/    # Interceptor chain
│   ├── guards/          # Guard execution
│   └── pipes/           # Pipe transformation
├── common/              # Decorators, interfaces, utilities
├── microservices/       # Transport strategies (TCP, Redis, NATS, gRPC, Kafka)
├── websockets/          # WebSocket gateway
├── platform-express/    # Express HTTP adapter
├── platform-fastify/    # Fastify HTTP adapter
├── testing/             # Test module and utilities
└── platform-socket.io/  # Socket.IO adapter
```

## Scale indicators

- ~500 TypeScript source files across packages
- ~80K+ lines of code
- Deep module hierarchies (3-5 levels)
- Cross-cutting DI, lifecycle, and middleware concerns

---

## Tasks

10 tasks (3 narrow, 4 medium, 3 wide) for the TypeScript enterprise server-side framework.

## Narrow

### N1: Fix circular dependency error message missing module path

When the DI container detects a circular dependency, the error message
shows the provider tokens but not the module path that caused the cycle.
Add the full module → module → ... → module chain to the error message
so developers can immediately identify which module imports create the
cycle.

### N2: Add `@Version()` decorator for controller method versioning

NestJS supports URI versioning at the controller level but individual
methods cannot override the controller's version. Add a `@Version()`
method decorator that overrides the controller-level version for a
specific endpoint. The decorator should work with all versioning
strategies (URI, header, media type).

### N3: Fix `@UseInterceptors()` ordering with global interceptors

When both global interceptors and method-level `@UseInterceptors()` are
registered, the execution order is inconsistent — global interceptors
sometimes run after method-level ones depending on module initialization
order. Fix the interceptor chain to guarantee that global interceptors
always wrap method-level interceptors (global runs first/outermost).

## Medium

### M1: Implement request-scoped caching

The current `CacheModule` uses a single shared cache store that doesn't
distinguish between requests. Implement request-scoped caching that
supports per-request cache keys (incorporating user identity, locale,
etc.). Add a `@CacheScope()` decorator to mark which request properties
contribute to the cache key. Support TTL and invalidation through the
existing cache manager interface.

### M2: Add health check aggregation for microservice architectures

Extend `@nestjs/terminus` health checks to support aggregating health
status from downstream microservices. When a service depends on other
NestJS microservices, the health check should recursively query their
health endpoints and aggregate the results into a tree. Add
timeout and circuit-breaker protection for downstream health checks.

### M3: Implement typed event emitter with DI integration

The current `EventEmitter2` integration lacks TypeScript type safety
for event payloads. Implement a typed event system where event names
are mapped to payload types via a registry interface. Events should
be injectable as dependencies. Add support for async event handlers
with configurable concurrency limits. Include dead letter handling
for failed event processing.

### M4: Add OpenTelemetry auto-instrumentation

Implement automatic OpenTelemetry tracing for NestJS applications.
Create spans for HTTP requests, guard execution, pipe validation,
interceptor chains, and provider method calls. Propagate trace context
through the DI container. Add a `@Span()` decorator for custom spans.
Support both HTTP and microservice transports.

## Wide

### W1: Implement module federation for micro-frontends

Add support for dynamic module loading from remote NestJS applications
at runtime. A host application should be able to discover and load
modules from remote services, merging their controllers, providers,
and middleware into the host's module tree. Handle version conflicts,
shared dependencies, and graceful degradation when remote modules are
unavailable.

### W2: Add first-class multi-database support

Implement native support for applications that connect to multiple
databases simultaneously. Each module should be able to declare which
database connection it uses. Add a `@Database('name')` decorator for
providers. Support cross-database transactions where possible. Update
the TypeORM, Mongoose, and Sequelize integrations to support named
connections. Add test utilities for multi-database test isolation.

### W3: Migrate microservice transports to a unified streaming API

Refactor all microservice transport implementations (TCP, Redis, NATS,
MQTT, gRPC, Kafka, RabbitMQ) to share a common streaming abstraction
layer. Currently each transport has its own connection management,
serialization, and error handling. Extract these into a shared transport
SDK so that adding new transports requires implementing only the
protocol-specific parts.

### N4: Fix `@Injectable()` scope not inherited by child classes

When a class decorated with `@Injectable({ scope: Scope.REQUEST })` is
extended, the child class defaults to `Scope.DEFAULT` instead of
inheriting the parent's scope. The metadata reflection does not traverse
the prototype chain. Fix the injector to inherit scope from parent
classes.

### N5: Fix `ValidationPipe` not validating nested arrays of DTOs

When a controller method receives `@Body() items: CreateDto[]`, the
`ValidationPipe` validates the outer array but does not validate
individual elements against `CreateDto`. Array element validation is
skipped when the root type is an array. Fix `ValidationPipe` to
validate each array element.

### N6: Add `@Timeout()` decorator for controller methods

Add a method-level decorator that sets a per-endpoint timeout. When the
handler exceeds the timeout, return a 408 Request Timeout response and
cancel any in-progress async operations. The timeout should include
guard, pipe, and interceptor execution time.

### N7: Fix `forwardRef()` not working with dynamic modules

When using `forwardRef(() => DynamicModule)` in module imports where the
target module is a dynamic module (created via `register()` or
`forRoot()`), the DI container fails with "Nest cannot resolve
dependencies." The forward reference resolver does not handle the
`DynamicModule` wrapper. Fix the resolver to unwrap dynamic modules.

### N8: Add `@Header()` decorator for response header setting

Controllers can read request headers with `@Headers()` but cannot
declaratively set response headers. Add `@Header('X-Custom', 'value')`
method decorator that sets response headers before the response is sent.
Support dynamic values via a factory function.

### N9: Fix `ExceptionFilter` not catching errors from async guards

When an async guard throws an error, the exception filter registered via
`@UseFilters()` on the controller does not catch it. The guard execution
happens before the filter chain is established. Fix the execution
context to wrap guard execution in the exception filter chain.

### N10: Fix `@Param()` type coercion not working with `ParseIntPipe` on optional params

When a route parameter is optional (`/:id?`) and `ParseIntPipe` is
applied, absent parameters cause the pipe to throw instead of allowing
`undefined` through. Fix `ParseIntPipe` to respect optional parameter
semantics.

### M5: Implement database migration management module

Add `@nestjs/migrations` that provides a Nest-native database migration
system. Support migration generation from entity diff, up/down
execution, migration locking for concurrent deployments, dry-run mode,
and a CLI for migration commands. Integrate with TypeORM and Sequelize
entity definitions.

### M6: Add config validation with typed schemas

Enhance `ConfigModule` with Zod/Joi schema validation at startup. When
config validation fails, provide clear error messages listing which
environment variables are missing or invalid. Support nested config
namespaces, default values, and typed config getters that return the
validated type.

### M7: Implement API rate limiting with distributed state

Add `@nestjs/throttler` improvements: sliding window algorithm (replacing
fixed window), distributed rate limiting via Redis with atomic operations,
per-user rate limits (not just per-IP), rate limit categories (different
limits for authenticated vs anonymous), and a dashboard endpoint showing
current rate limit states.

### M8: Add request context propagation across async boundaries

Implement automatic context propagation (request ID, user, tenant) across
async operations using `AsyncLocalStorage`. The context should be
available in all services, repositories, and event handlers spawned
during request processing, without explicit parameter passing.

### M9: Implement CQRS event replay and projections

Add event replay capability to the CQRS module. Store events in an event
store (database-backed), support replaying events from a specific
timestamp to rebuild projections, add transaction-scoped event publishing,
and implement snapshot support for long event streams.

### M10: Add API documentation generation with examples

Extend `@nestjs/swagger` to auto-generate request/response examples from
DTOs. Infer example values from `class-validator` decorators (e.g.,
`@IsEmail()` → `"user@example.com"`). Support custom examples via
decorator. Add API playground with editable request bodies.

### W4: Implement module hot-reloading for development

Add runtime module replacement without application restart. When a
module's source file changes, unload the old module (disposing providers,
removing routes), compile the new version, and load it into the running
DI container. Preserve request-scoped instances during transition.
Changes span the module loader, DI container, route registry, and
file watcher.

### W5: Add end-to-end API testing framework

Implement `@nestjs/testing/e2e` that starts the full application with a
real HTTP server and database, provides test fixtures for seeding data,
supports parallel test execution with isolated databases, and includes
assertion helpers for response validation. Integrate with Jest's test
lifecycle. Support testing authentication flows, WebSocket connections,
and microservice communication.

### W6: Implement event-driven saga orchestration

Add a saga orchestration system for coordinating long-running distributed
transactions across microservices. Support compensating actions (undo on
failure), timeout-based saga expiration, saga state persistence, and
visual saga flow monitoring. Integrate with all microservice transport
layers (TCP, Redis, NATS, Kafka, etc.) for cross-service communication.

### W7: Add service mesh integration

Implement service discovery and mesh integration for NestJS microservices.
Support automatic registration with Consul/etcd, health check endpoints,
load balancing strategies (round-robin, least-connections, consistent
hash), circuit breaker per upstream service, and distributed tracing
with context propagation across service boundaries. Changes span the
microservices transport layer, health module, and add a discovery module.

### W8: Implement declarative authorization framework

Add `@nestjs/authorization` with policy-based access control. Define
policies as composable rules (`@RequirePolicy('document.edit')`) that
evaluate against the request context. Support resource-based policies
(ABAC), role hierarchies, permission inheritance, and policy caching.
Include an admin API for policy management and an audit log for
access decisions. Changes span guards, decorators, metadata, and a
policy evaluation engine.

### W9: Add database-per-tenant multi-tenancy

Implement multi-tenancy where each tenant has its own database. Add
tenant resolution middleware (from subdomain, header, or JWT claim),
a connection pool manager that maintains per-tenant database connections,
tenant-aware repository injection, and admin APIs for tenant lifecycle
management. Support tenant-aware migrations and cross-tenant queries
for admin operations.

### W10: Implement GraphQL federation gateway

Add `@nestjs/graphql-federation` that implements Apollo Federation v2.
Support `@key`, `@requires`, `@provides`, and `@external` directives.
Implement a gateway that composes schemas from multiple NestJS
microservices, routes queries to the appropriate subgraph, and handles
entity resolution across subgraphs. Include dev tools for schema
composition validation and query planning visualization.
