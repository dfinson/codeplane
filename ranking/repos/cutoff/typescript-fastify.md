# fastify/fastify

| Field | Value |
|-------|-------|
| **URL** | https://github.com/fastify/fastify |
| **License** | MIT |
| **Language** | TypeScript (JavaScript runtime) |
| **Scale** | Medium |
| **Category** | Web framework |
| **Set** | Cutoff |
| **Commit** | `7248a6b39b2769d2ae3b476817287d1d649c507f` |

## Why this repo

- **Well-structured**: Core library under `lib/` with clear per-concern
  modules — route handling (`route.js`), reply construction (`reply.js`),
  request lifecycle (`handle-request.js`), hook system (`hooks.js`),
  content-type parsing (`content-type-parser.js`), schema validation
  (`validation.js`), error handling (`error-handler.js`), plugin
  encapsulation (`plugin-override.js`), decorator system (`decorate.js`),
  and 404 handling (`four-oh-four.js`). TypeScript definitions in `types/`.
- **Rich history**: 4K+ commits, 33K+ stars. One of the fastest Node.js
  web frameworks. PRs cover route resolution, plugin encapsulation,
  serialization performance, and lifecycle hooks.
- **Permissive**: MIT license.

## Structure overview

```
fastify.js                     # Main entry point — Fastify factory function
lib/
├── config-validator.js        # Configuration schema validation
├── content-type-parser.js     # Content-type parser registry and body parsing
├── content-type.js            # Content-type parsing utilities
├── context.js                 # Route context creation
├── decorate.js                # Decorator system for instance/request/reply
├── error-handler.js           # Error handler chain and default handler
├── error-serializer.js        # Error response serialization
├── error-status.js            # HTTP error status code mapping
├── errors.js                  # Custom error code definitions (FST_ERR_*)
├── four-oh-four.js            # 404 not-found route handling
├── handle-request.js          # Core request handling pipeline
├── head-route.js              # Automatic HEAD route generation
├── hooks.js                   # Hook system — onRequest, preParsing, etc.
├── initial-config-validation.js # Startup config validation
├── logger-factory.js          # Logger instance factory
├── logger-pino.js             # Pino logger integration
├── noop-set.js                # No-op Set for disabled features
├── plugin-override.js         # Plugin encapsulation and inheritance
├── plugin-utils.js            # Plugin metadata utilities
├── promise.js                 # Promise/thenable helpers
├── reply.js                   # Reply prototype — send, headers, serialize
├── req-id-gen-factory.js      # Request ID generator factory
├── request.js                 # Request object construction
├── route.js                   # Route registration and lookup
├── schema-controller.js       # Schema compilation controller
├── schemas.js                 # Schema store and $ref resolution
├── server.js                  # HTTP/HTTPS/HTTP2 server creation
├── symbols.js                 # Internal Symbol constants
├── validation.js              # Input validation pipeline
├── warnings.js                # Deprecation warning helpers
└── wrap-thenable.js           # Thenable-to-callback wrapper
types/
├── content-type-parser.d.ts   # ContentTypeParser type definitions
├── context.d.ts               # FastifyContext types
├── errors.d.ts                # Error type definitions
├── hooks.d.ts                 # Hook type definitions
├── instance.d.ts              # FastifyInstance type definitions
├── logger.d.ts                # Logger type definitions
├── plugin.d.ts                # Plugin type definitions
├── register.d.ts              # Register type definitions
├── reply.d.ts                 # Reply type definitions
├── request.d.ts               # Request type definitions
├── route.d.ts                 # Route type definitions
├── schema.d.ts                # Schema type definitions
├── server-factory.d.ts        # Server factory type definitions
├── type-provider.d.ts         # Type provider definitions
└── utils.d.ts                 # Utility type definitions
```

## Scale indicators

- ~31 JavaScript source files in `lib/`
- ~7.8K lines of code
- 15 TypeScript declaration files in `types/`
- Flat module structure (no deep nesting)
- Dependencies: `avvio`, `fast-json-stringify`, `find-my-way`, `pino`

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Add response time header to Reply prototype

The `Reply` prototype in `reply.js` tracks the start time of request
handling but does not automatically include the elapsed time in the
response. Add a configurable `X-Response-Time` header that is set in
`Reply.prototype.send()` by computing the difference between
`process.hrtime()` at request start (stored during route handler
dispatch in `handle-request.js`) and the send time.

### N2: Fix Reply.prototype.header not validating header name format

The `Reply.prototype.header()` method in `reply.js` sets response
headers without validating the header name against RFC 7230 token
rules. Passing header names with spaces, colons, or control characters
succeeds silently and can produce malformed HTTP responses. Add
validation to `header()` that throws `FST_ERR_REP_INVALID_HEADER_NAME`
(defined in `errors.js`) for non-compliant header names.

### N3: Add body size tracking to content-type parser

The `rawBody()` function in `content-type-parser.js` reads the request
body in chunks but does not track the total bytes received for logging
or metrics purposes. Add a `request.bodySize` property that is set
after body parsing completes, reflecting the actual uncompressed byte
count, and expose it through the `Request` object in `request.js`.
Add the `bodySize` property to `types/request.d.ts` and document the
feature with a usage example in `docs/` under the body parsing guide.

### N4: Fix error-handler not preserving original error stack trace

The `handleError()` function in `error-handler.js` wraps non-Error
thrown values into proper Error objects, but when a user error handler
itself throws, the original error's stack trace is lost in favor of
the wrapper. Preserve the original error as a `cause` property (using
ES2022 Error cause) on the wrapper error in `fallbackErrorHandler()`.

### N5: Add wildcard content-type parser removal

The `ContentTypeParser.prototype.remove()` method in
`content-type-parser.js` removes parsers by exact content-type string,
but provides no way to remove all parsers matching a pattern (e.g.,
all `application/*` parsers). Add an optional glob-style matching mode
to `remove()` that removes all registered parsers whose content-type
matches the pattern.

### N6: Fix HEAD route generation including response schema unnecessarily

In `route.js`, auto-generated HEAD routes are created using
`headOpts = { ...options }`, which copies the full `schema` from the GET
route including `schema.response`. Since HEAD responses never include a
body, the `response` schema triggers unnecessary serializer compilation
via `compileSchemasForSerialization()`, adding startup overhead for every
GET route that has a response schema. Strip `schema.response` from
`headOpts` in `route.js` before registering the HEAD route so that
response serializers are not compiled for HEAD routes.

### N7: Add request ID format validation to req-id-gen-factory

The `reqIdGenFactory()` in `req-id-gen-factory.js` creates request ID
generators but does not validate that custom `genReqId` functions
return values that are safe for use in headers and logs. A generator
returning objects, undefined, or strings with newlines can cause
malformed headers. Add runtime validation in `routeHandler()` in
`route.js` that checks the generated ID is a string or number.

### N8: Fix plugin-utils not detecting circular plugin dependencies

The plugin utility functions in `plugin-utils.js` handle plugin
metadata but do not detect circular dependencies when plugins depend
on decorators provided by each other. When two plugins each `dependsOn`
the other, the server hangs during boot. Add cycle detection in the
dependency resolution path and throw `FST_ERR_PLUGIN_CIRCULAR_DEPENDENCY`
with the cycle path listed.

### N9: Add schema compilation caching metrics to schema-controller

The `SchemaController` in `schema-controller.js` compiles JSON schemas
for validation and serialization but provides no visibility into
compilation performance. Add a `getCompilationStats()` method that
returns the number of compiled schemas, cache hit/miss counts, and
total compilation time, useful for diagnosing slow startup in
applications with many routes.

### N10: Fix noop-set missing forEach method and size property

The `noopSet()` factory function in `noop-set.js` is used as a
disabled-feature placeholder for the native `Set` in `server.js`. It
implements `add`, `delete`, `has`, and `[Symbol.iterator]`, but does not
implement the `forEach(callbackFn)` method or the `size` getter. Code
that calls `keepAliveConnections.forEach(...)` or reads
`keepAliveConnections.size` will throw a `TypeError` or return
`undefined` instead of `0`. Add a no-op `forEach()` method and a `size`
getter returning `0` to complete the `Set` interface contract.

## Medium

### M1: Implement route versioning with content negotiation

Add route versioning support that allows registering multiple handlers
for the same path with different versions, selected via `Accept-Version`
header or URL prefix. Requires a version constraint strategy in
`route.js`, integration with `find-my-way` router's constraint system,
version extraction in `handle-request.js`, and TypeScript definitions
in `types/route.d.ts` for version configuration. Document the
versioning API in `docs/` with configuration examples, and update
`README.md` to mention route versioning support in the features list.

### M2: Add response compression with content-type awareness

Implement built-in response compression in the `Reply` prototype. Add
a `reply.compress()` method and a global `compression` option that
automatically compresses responses based on `Accept-Encoding` and
content-type. Support gzip, deflate, and Brotli. Integrate with
`Reply.prototype.send()` for automatic compression, the serialization
pipeline for content-length recalculation, and the TypeScript definitions
in `types/reply.d.ts`.

### M3: Add per-field validation error aggregation with early-exit control

The validation pipeline in `validation.js` uses AJV and stops at the
first invalid field per validation phase (body, querystring, headers,
params) because each compile call uses a separate validator. Add an
`allErrors` option (defaulting to the existing AJV `allErrors` setting)
to route definitions that forces AJV to collect all validation errors
across all fields before returning, and expose a new
`validationErrorAggregator` hook on the route context that receives the
full array of AJV errors and can return a unified error object. Integrate
with `compileSchemasForValidation()` in `validation.js` for error
collection, `wrapValidationError()` for aggregation, `context.js` for
the aggregator option, `route.js` for route-level `allErrors` config,
and `types/route.d.ts` for the option type.

### M4: Add graceful shutdown with in-flight request tracking

Implement graceful shutdown that tracks in-flight requests and waits
for them to complete before closing the server. Add a
`fastify.activeRequests` counter incremented in `routeHandler()` in
`route.js` and decremented after `Reply.prototype.send()`. Add a
`fastify.close({ timeout: 30000 })` option in `server.js` that stops
accepting new connections and resolves when all in-flight requests
complete or the timeout expires. Update the hook system in `hooks.js`
to fire `onShutdown` hooks.

### M5: Implement decorator inheritance control for plugins

The decorator system in `decorate.js` always inherits decorators from
parent contexts to child plugin contexts via `plugin-override.js`.
Add an `isolate` option to `fastify.register()` that prevents decorator
inheritance, giving plugins a clean namespace. Requires changes to
`plugin-override.js` for isolation logic, `decorate.js` for the
isolation check, `route.js` for context resolution with isolated
plugins, and `types/register.d.ts` for the option type.

### M6: Add structured error codes to all internal errors

The `errors.js` module defines some error codes (e.g.,
`FST_ERR_CTP_ALREADY_PRESENT`) but several internal throw sites in
`route.js`, `reply.js`, and `hooks.js` throw plain `Error` objects
without codes. Audit all throw sites, define error codes in `errors.js`
for each, replace plain `Error` throws with coded errors, and update
`types/errors.d.ts` with the new error code types.

### M7: Implement request context propagation across async hooks

The hook system in `hooks.js` runs hooks sequentially but does not
propagate context (e.g., tracing IDs, user data) set by early hooks
to later hooks or the route handler without using request decoration.
Add a `request.context` map that is initialized before `onRequest`
hooks and flows through `preParsing`, `preValidation`, `preHandler`,
and `preSerialization` hooks. Integrate with `handle-request.js` for
context initialization and `request.js` for the context property.

### M8: Add onHandlerTimeout lifecycle hook

The per-route `handlerTimeout` option in `route.js` fires a
`setTimeout()` that directly calls `reply.send(new FST_ERR_HANDLER_TIMEOUT(...))`
when the handler exceeds the configured time, with no hook for
customization. Add an `onHandlerTimeout` lifecycle hook that is called
instead of the automatic 408 response, giving applications control over
the timeout response format, logging, or cleanup. Integrate with
`hooks.js` to register and run `onHandlerTimeout` hooks, `route.js` in
`routeHandler()` to call the hook runner when the handler timer fires,
`context.js` to initialize the hook array, and `types/hooks.d.ts` for
the hook type definition.

### M9: Implement response schema validation in development mode

The serialization pipeline in `validation.js` compiles response schemas
for serialization but does not validate outgoing responses against
those schemas. Add a `validateResponse` option (enabled by default in
development) that validates the response body against the declared
response schema before serialization. Log warnings for schema
violations. Integrate with `Reply.prototype.serialize()` in `reply.js`,
the schema controller in `schema-controller.js`, and the logger
in `logger-factory.js`.

### M10: Add support for async content-type parsers with backpressure

The `rawBody()` function in `content-type-parser.js` buffers the entire
request body in memory before calling the parser. Add streaming parser
support where parsers receive a `Readable` stream instead of a
`Buffer`. Implement backpressure handling so slow parsers pause the
incoming stream. Integrate with `ContentTypeParser.prototype.add()` for
parser registration, `ContentTypeParser.prototype.run()` for parser
selection, and `handle-request.js` for stream piping.

## Wide

### W1: Implement a rate limiting system with pluggable stores

Add built-in rate limiting with configurable strategies (fixed window,
sliding window, token bucket). Support pluggable stores (in-memory,
Redis, custom) for distributed rate limiting. Include per-route and
global rate limit configuration, rate limit headers
(`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`),
and custom key extraction (IP, user ID, API key). Changes span a new
`lib/rate-limit.js` module, `route.js` for per-route config,
`hooks.js` for `onRequest` hook integration, `reply.js` for header
injection, `errors.js` for rate limit error codes, and
`types/instance.d.ts` for configuration types.

### W2: Add OpenAPI specification generation from route schemas

Implement automatic OpenAPI 3.0 document generation from registered
routes. Extract path parameters, query schemas, request body schemas,
and response schemas from route definitions. Support security scheme
declarations, tag grouping, and description annotations. Serve the
generated spec at a configurable endpoint. Changes span a new
`lib/openapi.js` module, `route.js` for schema metadata extraction,
`context.js` for route metadata storage, `schemas.js` for `$ref`
resolution into OpenAPI format, `reply.js` for spec serving, and
`types/instance.d.ts` for OpenAPI configuration types.

### W3: Implement server-sent events (SSE) support

Add first-class SSE support with a `reply.sse()` method that opens a
persistent connection and returns an event emitter. Support named
events, retry intervals, last-event-ID resumption, and automatic
client reconnection detection. Implement connection keep-alive via
periodic comment pings. Changes span `reply.js` for the `sse()` method
and connection lifecycle, a new `lib/sse.js` module for event
formatting and connection management, `hooks.js` for `onClose` hook
integration, `route.js` for SSE route registration, `handle-request.js`
for SSE-aware timeout handling, and `types/reply.d.ts` for SSE types.

### W4: Add request/response transformation pipeline

Implement a transformation pipeline that allows registering ordered
transformers for requests and responses. Transformers can modify
headers, body, and URL before/after the handler. Support conditional
transformers based on content-type, route, or custom predicates.
Include built-in transformers for header normalization, body
encryption/decryption, and response envelope wrapping. Changes span
a new `lib/transform.js` module, `hooks.js` for transformer execution
in `preParsing` and `preSerialization`, `reply.js` for response
transformation, `route.js` for per-route transformer config,
`plugin-override.js` for transformer inheritance, and `types/hooks.d.ts`
for transformer types.

### W5: Implement distributed tracing with W3C Trace Context

Add distributed tracing support following W3C Trace Context (RFC
traceparent/tracestate). Generate trace and span IDs, propagate
context through headers, create spans for request lifecycle phases
(parsing, validation, handler, serialization), and expose a tracer
API on the fastify instance. Support export to console, Jaeger, and
custom backends. Changes span a new `lib/tracing.js` module,
`handle-request.js` for span lifecycle, `hooks.js` for trace hook
integration, `reply.js` for response header injection, `request.js`
for trace context parsing, `logger-factory.js` for trace-aware
logging, and `types/instance.d.ts` for tracer types.

### W6: Add WebSocket support with route-level handlers

Implement WebSocket support that integrates with the route system.
Add `fastify.ws(path, handler)` for WebSocket route registration,
protocol upgrade handling in the server module, per-connection
lifecycle hooks (`onConnection`, `onMessage`, `onClose`, `onError`),
and authentication via `onRequest` hooks. Support broadcasting and
room-based messaging. Changes span `server.js` for upgrade handling,
a new `lib/websocket.js` module for connection management, `route.js`
for WebSocket route registration, `hooks.js` for WebSocket lifecycle
hooks, `reply.js` for upgrade response, `errors.js` for WebSocket
error codes, and `types/instance.d.ts` for WebSocket types.

### W7: Implement configuration-driven route loading

Add a declarative route loading system that reads route definitions
from JSON/YAML configuration files and registers them with the
Fastify instance. Support path patterns, HTTP methods, handler
references (module paths), schema definitions, hook assignments,
and plugin scoping. Include hot-reload support in development mode.
Changes span a new `lib/config-routes.js` module (config parser and
route builder), `route.js` for programmatic registration, `hooks.js`
for config-defined hooks, `validation.js` for schema loading from
files, `plugin-override.js` for plugin-scoped config routes, and
type definitions in `types/instance.d.ts`.

### W8: Add response caching with cache-control awareness

Implement a response caching system that respects `Cache-Control`
headers. Cache responses in memory or pluggable stores, support
cache key generation from method/URL/query/vary headers, implement
stale-while-revalidate and stale-if-error strategies, and add cache
status headers (`X-Cache: HIT/MISS/STALE`). Changes span a new
`lib/cache.js` module (cache store, key generation, revalidation),
`hooks.js` for `onRequest` cache lookup and `onSend` cache storage,
`reply.js` for cache header injection, `route.js` for per-route
cache configuration, `handle-request.js` for cache-aware request
flow, and `types/route.d.ts` for cache option types.

### W9: Implement health check endpoints with dependency monitoring

Add a comprehensive health check system with `/health`, `/ready`, and
`/live` endpoints. Support dependency health checks (database, cache,
external services) with configurable timeouts and retry logic. Include
aggregated health status with per-dependency detail, startup probe
support, and health check result caching. Changes span a new
`lib/health.js` module (health checker, dependency registry),
`route.js` for health route registration, `hooks.js` for startup
readiness hooks, `server.js` for server state tracking, `errors.js`
for health check error codes, `reply.js` for health response
formatting, and `types/instance.d.ts` for health check types.

### W10: Add multi-tenant routing with tenant isolation

Implement multi-tenant support where routes, plugins, and decorators
can be scoped to specific tenants. Add tenant extraction from headers,
subdomains, or path prefixes. Support tenant-specific configuration,
per-tenant error handlers, and tenant-aware logging. Implement tenant
isolation so plugins registered for one tenant are invisible to others.
Changes span a new `lib/tenant.js` module (tenant extraction and
context), `route.js` for tenant-scoped routes, `plugin-override.js`
for tenant-isolated plugin contexts, `hooks.js` for tenant lifecycle
hooks, `decorate.js` for tenant-scoped decorators, `error-handler.js`
for tenant error handlers, and `types/instance.d.ts` for tenant types.

### N11: Fix CI workflows missing job-level timeout-minutes

Neither `.github/workflows/ci.yml` nor `.github/workflows/integration.yml`
set `timeout-minutes` on any of their jobs, leaving them exposed to
GitHub Actions' default 6-hour limit. A single hanging test can block
CI for hours. Add appropriate `timeout-minutes` values to the unit-test
and lint jobs in `ci.yml` (e.g., 20 minutes) and to each job in
`integration.yml` (e.g., 30 minutes). Also update the `coverage` script
in `package.json` to pass `--timeout 60000` to borp, since
coverage-instrumented runs are measurably slower than plain test runs
and can exceed the default 30-second per-test timeout.

### M11: Add contributor documentation and project governance updates

Rewrite `CONTRIBUTING.md` to include step-by-step guides for adding new
hooks to `hooks.js`, defining error codes in `errors.js` with the
`FST_ERR_*` prefix convention, and creating TypeScript type definitions
in `types/`. Add a `docs/Guides/Error-Codes.md` listing all `FST_ERR_*`
codes with descriptions and error resolution guidance. Update
`README.md` to add a code coverage badge (the existing CI and npm
version badges are already present). Update `.github/dependabot.yml` to
change the schedule from `monthly` to `weekly` for both the `npm` and
`github-actions` package ecosystems. Add a plugin development guide to
`docs/Guides/Plugin-Development.md` explaining the encapsulation model
in `plugin-override.js` and decorator inheritance via `decorate.js`.

### W11: Consolidate CI workflows and overhaul developer tooling

Consolidate the 20 GitHub Actions workflow files under
`.github/workflows/` by merging related workflows: combine
`coverage-nix.yml` and `coverage-win.yml` into a single cross-platform
coverage workflow, merge `ci.yml` and `ci-alternative-runtime.yml` into
one with a runtime matrix, and consolidate `integration.yml` and
`integration-alternative-runtimes.yml`. Add a `package.json` script for
validating the TypeScript declarations in `types/` against `fastify.d.ts`.
Update `eslint.config.js` to add rules for `lib/` that enforce the
`FST_ERR_*` error code prefix pattern. Update `.gitpod.yml` to
pre-install dependencies and start the test watcher. Update
`GOVERNANCE.md` to document the release process, versioning strategy,
and the relationship between `package.json` `version` and
`build/sync-version.js`. Add `.markdownlint-cli2.yaml` ignore patterns
for generated documentation output.
