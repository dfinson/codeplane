# nestjs/nest

| Field | Value |
|-------|-------|
| **URL** | https://github.com/nestjs/nest |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Large (multi-team project) |
| **Category** | Enterprise server-side framework |
| **Set** | ranker-gate |
| **Commit** | `3de9ef6c92531869a0537e25ce79d83d32d9337f` |

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

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix circular dependency error message missing module path

When the DI container detects a circular dependency, the error message
shows the provider tokens but not the module path that caused the cycle.
Add the full module → module → ... → module chain to the error message
so developers can immediately identify which module imports create the
cycle.

### N2: Fix `ParseEnumPipe.isEnum()` including reverse-mapped keys for numeric enums

The `ParseEnumPipe` in `packages/common/pipes/parse-enum.pipe.ts`
validates input using `Object.keys(enumType).map(item =>
enumType[item])`. For numeric TypeScript enums, `Object.keys()` returns
both the string keys and the numeric reverse-mapped values. The mapped
values then include both the numeric values and the original string
keys, so `isEnum()` accepts values it should reject. Fix `isEnum()` to
filter out reverse-mapped entries when validating numeric enums.

### N3: Fix `@UseInterceptors()` ordering with global interceptors

When both global interceptors and method-level `@UseInterceptors()` are
registered, the execution order is inconsistent — global interceptors
sometimes run after method-level ones depending on module initialization
order. Fix the interceptor chain to guarantee that global interceptors
always wrap method-level interceptors (global runs first/outermost).

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

### N8: Fix `DefaultValuePipe` not applying default when value is empty string

The `DefaultValuePipe` in `packages/common/pipes/default-value.pipe.ts`
only applies the default when the value is `null`, `undefined`, or
`NaN`. When a query parameter is present but empty (e.g., `?name=`),
the pipe passes through the empty string `""` instead of the default
value. Add an `includeEmptyStrings` option that treats empty strings as
missing values and applies the default.

### N9: Fix `ExceptionFilter` not catching errors from async guards

When an async guard throws an error, the exception filter registered via
`@UseFilters()` on the controller does not catch it. The guard execution
happens before the filter chain is established. Fix the execution
context to wrap guard execution in the exception filter chain.

### N10: Fix `SseStream` not forwarding `Last-Event-ID` to reconnecting clients

The SSE implementation in `RouterResponseController`
(`packages/core/router/router-response-controller.ts`) handles SSE
streaming via `SseStream` (`packages/core/router/sse-stream.ts`) but
does not read the `Last-Event-ID` header from reconnecting clients.
When a client reconnects after a network interruption, the handler
receives no information about which events were already delivered. Fix
the SSE setup to extract `Last-Event-ID` from the incoming request and
expose it to the observable factory so handlers can resume from the
correct event.

## Medium

### M1: Add provider dependency cycle visualization to `GraphInspector`

The `GraphInspector` in `packages/core/inspector/graph-inspector.ts`
builds a serialized dependency graph (`SerializedGraph`) from the DI
container but does not detect or visualize provider-level dependency
cycles. When a `CircularDependencyException` is thrown from
`packages/core/errors/exceptions/circular-dependency.exception.ts`,
developers see only a text message without structural context. Extend
`GraphInspector.inspectModules()` to detect cycles in the serialized
graph edges and annotate them. Add a `getCycles()` method to
`SerializedGraph` that returns all detected cycles as ordered paths of
provider tokens and module names.

### M2: Add graceful shutdown timeout for microservice server transports

The abstract `Server` class in `packages/microservices/server/server.ts`
defines a `close()` method that each transport implements (e.g.,
`ServerTCP`, `ServerRedis`, `ServerKafka`), but there is no configurable
shutdown timeout. When `close()` is called, in-flight message handlers
may never complete, leaving the process hanging. Add a configurable
`shutdownTimeout` option to the `Server` base class. When the timeout
expires during `close()`, force-disconnect the transport and log a
warning. Implement the timeout in `ServerTCP`, `ServerRedis`, and
`ServerKafka`.

### M3: Add request-scoped provider lazy initialization to the DI container

The `Injector` in `packages/core/injector/injector.ts` eagerly creates
request-scoped provider instances when a request enters the pipeline,
even if those providers are never used during that request. For
applications with many request-scoped providers, this adds unnecessary
overhead. Add a `lazy: true` option to `@Injectable({ scope:
Scope.REQUEST, lazy: true })` that defers instantiation until the
provider is first accessed. Implement lazy proxies in the
`InstanceWrapper` (`packages/core/injector/instance-wrapper.ts`) and
update the `ModuleRef.resolve()` method
(`packages/core/injector/module-ref.ts`) to support lazy resolution.

### M4: Add OpenTelemetry auto-instrumentation

Implement automatic OpenTelemetry tracing for NestJS applications.
Create spans for HTTP requests, guard execution, pipe validation,
interceptor chains, and provider method calls. Propagate trace context
through the DI container. Add a `@Span()` decorator for custom spans.
Support both HTTP and microservice transports.

### M5: Add exclude patterns to `MiddlewareModule` route matching

The `MiddlewareModule` in `packages/core/middleware/middleware-module.ts`
applies middleware using route configurations from the
`MiddlewareBuilder`. The `RouteInfoPathExtractor`
(`packages/core/middleware/route-info-path-extractor.ts`) resolves route
paths but the exclude patterns configured via
`MiddlewareConsumer.exclude()` use simple string matching in
`RoutesMapper` (`packages/core/middleware/routes-mapper.ts`). Routes
with path parameters (e.g., `/users/:id`) in the exclude list don't
match against actual parameterized requests. Fix the exclude matching
to use path-to-regexp pattern matching consistent with the router,
affecting `RoutesMapper`, `RouteInfoPathExtractor`, and
`MiddlewareModule`.

### M6: Add per-transport serialization configuration for microservice clients

The `ClientProxy` base class in
`packages/microservices/client/client-proxy.ts` accepts a single
`serializer` and `deserializer` for all message patterns. When a
microservice communicates with multiple remote services that use
different serialization formats (e.g., JSON for some patterns,
MessagePack for others), there is no way to configure per-pattern
serialization. Add pattern-based serializer resolution to `ClientProxy`
and update the concrete clients (`ClientTCP` in
`packages/microservices/client/client-tcp.ts`, `ClientRedis` in
`packages/microservices/client/client-redis.ts`, `ClientNats` in
`packages/microservices/client/client-nats.ts`) to route messages
through the appropriate serializer based on the message pattern.

### M7: Add execution context metadata to REPL debug output

The REPL module (`packages/core/repl/`) provides `debug()` and
`methods()` native functions for introspection, but the debug output
from `ReplContext` (`packages/core/repl/repl-context.ts`) only shows
provider and controller names without execution metadata. Extend the
`debugRegistry` to include each provider's scope
(`DEFAULT`/`REQUEST`/`TRANSIENT`), registered lifecycle hooks
(`onModuleInit`, `onModuleDestroy`, etc.), and applied enhancers
(guards, interceptors, pipes). Update the `DebugReplFn` in
`packages/core/repl/native-functions/` to format and display this
information.

### M8: Add request context propagation across async boundaries

Implement automatic context propagation (request ID, user, tenant) across
async operations using `AsyncLocalStorage`. The context should be
available in all services, repositories, and event handlers spawned
during request processing, without explicit parameter passing.

### M9: Add `TestingModuleBuilder.overrideInterceptor()` for test isolation

The `TestingModuleBuilder` in
`packages/testing/testing-module.builder.ts` supports
`overrideProvider()` and `overrideModule()` for test isolation, but
there is no dedicated API for overriding interceptors. Developers must
use `overrideProvider()` with the interceptor class token, which doesn't
work for globally registered interceptors set via
`app.useGlobalInterceptors()`. Add
`overrideInterceptor(type).useClass(mock)` and
`overrideGuard(type).useClass(mock)` methods to `TestingModuleBuilder`.
Update `TestingModule` (`packages/testing/testing-module.ts`) and
`TestingInjector` (`packages/testing/testing-injector.ts`) to intercept
global enhancer registration.

### M10: Add `DiscoveryService` method-level metadata scanning

The `DiscoveryService` in
`packages/core/discovery/discovery-service.ts` discovers providers and
controllers by class-level metadata using
`DiscoverableMetaHostCollection`, but has no built-in support for
discovering method-level decorators across all controllers. Scanning
method metadata requires manually iterating controllers and using
`MetadataScanner` (`packages/core/metadata-scanner.ts`). Add
`getMethodsByDecorator(decorator)` to `DiscoveryService` that returns
all methods across all controllers decorated with a given
`DiscoverableDecorator`, including the parent `InstanceWrapper`, method
key, and metadata value. Integrate with the existing
`DiscoverableMetaHostCollection` for efficient lookups.

## Wide

### W1: Implement module federation for micro-frontends

Add support for dynamic module loading from remote NestJS applications
at runtime. A host application should be able to discover and load
modules from remote services, merging their controllers, providers,
and middleware into the host's module tree. Handle version conflicts,
shared dependencies, and graceful degradation when remote modules are
unavailable.

### W2: Add unified error serialization across HTTP, WebSocket, and microservice contexts

Error handling is implemented separately in each execution context:
`ExceptionsHandler` for HTTP (`packages/core/exceptions/`),
`WsExceptionsHandler` for WebSockets
(`packages/websockets/exceptions/ws-exceptions-handler.ts`), and
`RpcExceptionsHandler` for microservices
(`packages/microservices/exceptions/rpc-exceptions-handler.ts`). Each
uses different error shapes, serialization formats, and exception filter
chains. Implement a shared `ErrorSerializationStrategy` interface that
normalizes error responses across all contexts. Add a configurable error
serializer to the `ApplicationConfig`
(`packages/core/application-config.ts`) that HTTP, WS, and RPC
exception handlers all consume. Support consistent error codes,
structured error metadata, and stack trace filtering across
`ExternalExceptionsHandler`
(`packages/core/exceptions/external-exceptions-handler.ts`),
`WsExceptionsHandler`, and `RpcExceptionsHandler`.

### W3: Migrate microservice transports to a unified streaming API

Refactor all microservice transport implementations (TCP, Redis, NATS,
MQTT, gRPC, Kafka, RabbitMQ) to share a common streaming abstraction
layer. Currently each transport has its own connection management,
serialization, and error handling. Extract these into a shared transport
SDK so that adding new transports requires implementing only the
protocol-specific parts.

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

### W10: Add cross-platform versioning support for WebSocket gateways and microservice handlers

The versioning system (URI, header, media type, custom via
`VersioningType` in `packages/common/enums/version-type.enum.ts`) only
works for HTTP routes through `RoutePathFactory`
(`packages/core/router/route-path-factory.ts`) and `RouterExplorer`
(`packages/core/router/router-explorer.ts`). WebSocket gateways
(`packages/websockets/web-sockets-controller.ts`,
`packages/websockets/gateway-metadata-explorer.ts`) and microservice
message handlers (`packages/microservices/server/server.ts`,
`packages/microservices/context/rpc-context-creator.ts`) have no
versioning support. Extend the `@Version()` decorator
(`packages/common/decorators/core/version.decorator.ts`) to work on
gateway `@SubscribeMessage()` handlers and microservice
`@MessagePattern()` handlers. Implement version-aware message routing
in `WebSocketsController` and the `Server` base class, with version
extraction from message metadata or headers.
