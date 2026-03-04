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
