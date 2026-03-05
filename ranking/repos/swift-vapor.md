# vapor/vapor

| Field | Value |
|-------|-------|
| **URL** | https://github.com/vapor/vapor |
| **License** | MIT |
| **Language** | Swift |
| **Scale** | Medium (multi-module project) |
| **Category** | Server-side web framework |

## Why this repo

- **Multi-module with clear boundaries**: HTTP server (built on SwiftNIO),
  routing, middleware pipeline, content encoding/decoding (JSON, URL-encoded,
  multipart), authentication, sessions, validation, WebSocket support,
  client HTTP — each a distinct subsystem requiring navigation between them.
- **Well-structured**: Source under `Sources/Vapor/` with clear per-concern
  directories. The request pipeline (routing → middleware → handler →
  response) is reflected in code organization.
- **Rich history**: 7K+ commits, the primary Swift server-side framework.
  Active development with PRs covering SwiftNIO integration, async/await
  migration, and API improvements.
- **Permissive**: MIT license.

## Structure overview

```
Sources/Vapor/
├── Application.swift        # Application entry point
├── Routing/                 # Route registration and matching
│   ├── Router.swift         # Route tree
│   ├── Route.swift          # Route definition
│   └── RoutesBuilder.swift  # Route builder DSL
├── HTTP/                    # HTTP layer
│   ├── Server/              # HTTP server (SwiftNIO-based)
│   ├── Client/              # HTTP client
│   └── Headers/             # Header utilities
├── Middleware/               # Middleware pipeline
│   ├── Middleware.swift      # Protocol
│   ├── CORSMiddleware.swift  # CORS handling
│   ├── ErrorMiddleware.swift # Error handling
│   └── FileMiddleware.swift  # Static file serving
├── Content/                  # Content negotiation
│   ├── ContentEncoder.swift  # JSON, URL-encoded, multipart
│   └── ContentDecoder.swift
├── Auth/                     # Authentication
├── Sessions/                 # Session management
├── Validation/               # Input validation
├── WebSocket/                # WebSocket support
├── Request.swift             # Request model
├── Response.swift            # Response model
└── Utilities/                # Shared utilities
```

## Scale indicators

- ~150 Swift source files
- ~30K lines of code
- 2-3 levels of module nesting
- Clear pipeline architecture

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `req.query.decode()` failing for optional enum query parameters

When a query parameter is typed as `Optional<MyEnum>` and the parameter
is absent from the URL, `req.query.decode()` throws a decoding error
instead of returning nil. The `URLEncodedFormDecoder` does not handle
optional enum types correctly when the key is missing. Fix the decoder
to treat missing optional enum keys as nil.

### N2: Add `req.logger` contextual metadata propagation

The `req.logger` includes the request ID but does not automatically
propagate custom metadata added during the request lifecycle. When a
middleware adds metadata via `req.logger[metadataKey: "userId"] = "123"`,
subsequent middleware and route handlers see a fresh logger without the
metadata. Fix logger metadata to propagate through the middleware chain.

### N3: Fix WebSocket `ping` not sent when connection is idle

The WebSocket implementation does not send periodic pings to keep
idle connections alive. Long-lived WebSocket connections through proxies
and load balancers are silently closed after the proxy's idle timeout.
Implement automatic ping/pong with a configurable interval (default 30s)
and a dead connection timeout.

### N4: Fix `FileMiddleware` not setting `Content-Type` for `.wasm` files

`FileMiddleware` uses an extension-to-MIME lookup to set the
`Content-Type` header when serving static files. WebAssembly `.wasm`
files are not included in the mapping, so they are served as
`application/octet-stream`. Browsers refuse to compile-stream them
without the correct `application/wasm` type. Add the mapping entry
for `.wasm` and any other modern file types missing from the table.

### N5: Fix `CORSMiddleware` not handling preflight `DELETE` and `PATCH` correctly

When a browser sends an `OPTIONS` preflight request with
`Access-Control-Request-Method: DELETE` or `PATCH`, the middleware
returns the allowed methods list but omits the
`Access-Control-Max-Age` header, causing the browser to re-send the
preflight on every request. Also, the middleware does not set
`Vary: Origin` when the configuration allows a specific origin list.
Fix both header omissions.

### N6: Fix route parameter percent-decoding for path components with `+`

Route parameters captured via `req.parameters.get("name")` are
percent-decoded, but `+` characters are not translated to spaces.
A request to `/greet/hello+world` returns `"hello+world"` instead
of `"hello world"`. The `URLEncodedFormDecoder` already handles `+`
as a space but the route parameter extraction path does not share
that logic. Apply the same decoding to route parameter values.

### N7: Fix `Response.Body` streaming back-pressure not propagated to handler

When a route handler writes to a streaming `Response.Body` via the
`BodyStreamWriter`, the NIO channel writability flag is not checked.
If the client reads slowly, buffers grow unbounded in memory. Wire
the `BodyStreamWriter.write()` future to the channel's `isWritable`
state so the handler is back-pressured when the outbound buffer
exceeds the high-water mark.

### N8: Fix `req.redirect(to:)` not preserving query string

Calling `req.redirect(to: "/new-path")` from a handler drops query
parameters that were on the original request. Developers expect
`req.redirect(to: "/new-path", preservingQuery: true)` to append
the original `req.url.query` to the redirect location. Add the
`preservingQuery` parameter (default `false`) to the redirect helper.

### N9: Fix `Application.shutdown()` not draining in-flight requests

When `app.shutdown()` is called, the HTTP server immediately closes
the listening socket and drops in-flight requests. Clients receive
connection-reset errors. Implement graceful shutdown: stop accepting
new connections, wait for in-flight requests to complete (with a
configurable timeout, default 30s), then close the server.

### N10: Fix `Sessions` cookie not setting `SameSite` attribute

The session cookie set by `SessionsMiddleware` does not include the
`SameSite` attribute. Modern browsers default missing `SameSite` to
`Lax`, which breaks legitimate cross-origin POST flows (e.g., OAuth
callbacks). Add a `sameSite` configuration option to
`SessionsConfiguration` with `.lax`, `.strict`, and `.none` values,
defaulting to `.lax`.

## Medium

### M1: Implement request body validation middleware

Add declarative request body validation via a `Validatable` protocol.
Types conforming to `Validatable` declare validation rules:
`.count(1...100)`, `.email`, `.url`, `.custom(closure)`. Add a
`ValidateMiddleware` that runs validations before the route handler
and returns structured error responses with per-field error messages.
Support nested object validation and conditional validation rules.

### M2: Add database query logging with source location

Implement query logging for Fluent (Vapor's ORM) that includes the
Swift source file and line number where the query was initiated.
Support log levels: none, errors-only, slow queries (with configurable
threshold), and all queries. Include query duration, bound parameters
(with sensitive value masking), and row count. Add an in-memory query
log for testing assertions.

### M3: Implement server response compression

Add configurable response compression middleware supporting gzip,
deflate, and brotli. Negotiate encoding via Accept-Encoding header.
Support minimum response size threshold, content type filtering
(compress text/JSON but not images), and compression level
configuration. Add streaming compression for large responses.
Use SwiftNIO's built-in compression support where available.

### M4: Add structured concurrency-aware request context

Vapor's `Request` is a class that relies on NIO `EventLoop` for
thread-safety. With Swift's structured concurrency (`async`/`await`),
request-scoped values need a safe propagation mechanism. Implement a
`RequestContext` type backed by Swift's `TaskLocal` storage that
carries the request ID, logger, authenticated user, and custom
key-value pairs across `async` calls without explicitly passing
`Request`. Ensure middleware, route handlers, and Fluent queries
can access the task-local context.

### M5: Implement rate limiting middleware with sliding window

Add a `RateLimitMiddleware` that supports per-route and per-client
rate limits using a sliding window algorithm. Support client
identification by IP address, API key header, or authenticated user.
Store counters in an in-memory store (default) or Redis (via
protocol). Return standard `429 Too Many Requests` with
`Retry-After` and `X-RateLimit-Remaining` headers. Allow route
groups to declare their own limits.

### M6: Add multipart file upload streaming with progress

The current multipart decoder buffers the entire upload body before
parsing. For large file uploads this causes memory spikes. Implement
a streaming multipart parser that yields `FileChunk` values as they
arrive, backed by SwiftNIO's `ByteBuffer`. Provide a convenience
API: `req.body.collectMultipart(maxSize:)` for small uploads and
`req.body.streamMultipart { part in }` for large ones. Add upload
progress reporting via a delegate callback.

### M7: Implement typed route metadata and middleware filtering

Allow routes to carry typed metadata so middleware can introspect route
characteristics without string matching:
`app.get("admin", "users").metadata(RequiresAuth.self, value: .admin)`.
Middleware receives the metadata via `req.route?.metadata(RequiresAuth.self)`.
This replaces ad-hoc path-prefix checks with a type-safe mechanism.
Support metadata inheritance from route groups to child routes.

### M8: Add HTTP client retry and circuit breaker

Vapor's built-in `Client` has no retry logic. Implement configurable
retry with exponential back-off, jitter, and retry-on conditions
(status codes, error types). Add a circuit breaker that trips after
N consecutive failures, returning errors immediately for a cooldown
period before allowing a probe request. Track circuit state in a
per-host store. Emit structured log entries for retries and state
transitions.

### M9: Implement ETag and conditional request handling

Add `ETagMiddleware` that generates weak and strong ETags for
responses. For static file responses via `FileMiddleware`, compute
the ETag from the file modification time and size. For dynamic
responses, compute it from a hash of the response body. Handle
`If-None-Match` and `If-Modified-Since` request headers, returning
`304 Not Modified` when appropriate. Support `Cache-Control` header
configuration per route group.

### M10: Add request body size limiting with per-route overrides

The HTTP server's `maxBodySize` is a global configuration. Implement
per-route body size limits via a `BodySizeMiddleware` that can be
applied to route groups: `admin.grouped(BodySizeMiddleware(max: "50mb"))`.
The middleware should check `Content-Length` before reading the body
and abort streaming reads that exceed the limit. Return
`413 Payload Too Large` with a descriptive JSON error. Support
exemptions for specific content types (e.g., multipart uploads).

## Wide

### W1: Add API versioning with automatic documentation

Implement API versioning across the framework. Support URL path
versioning (`/v1/users`), header versioning (`API-Version: 1`), and
content negotiation (`Accept: application/vnd.api.v1+json`). Routes
should be registerable per version. Add automatic OpenAPI spec
generation per version. Support version deprecation warnings in
response headers. Include a version migration guide generator.

### W2: Implement distributed tracing and observability

Add OpenTelemetry-compatible distributed tracing. Create spans for
each request through the middleware pipeline, route handler, Fluent
database queries, Redis operations, and outbound HTTP client calls.
Propagate trace context across service boundaries via W3C Trace
Context headers. Add metrics collection (request count, latency
histogram, error rate) with Prometheus export. Include a health
check endpoint with dependency status.

### W3: Implement end-to-end type-safe HTML templating engine

Build a Swift DSL for HTML rendering integrated with Vapor's content
pipeline. Support type-safe HTML elements, attribute builders, component
composition via `@resultBuilder`, and async data fetching within
templates. Integrate with the `Response` content negotiation so
routes can return `HTML { ... }` directly. Add layout inheritance
(base layout with named slots), partials, and automatic XSS escaping.
Support streaming rendering for large pages and hot-reload in debug
mode by watching template source files.

### W4: Add background job processing system

Implement an in-process job queue for deferred and scheduled work.
Define jobs via a `Job` protocol with `func handle(context:) async throws`.
Support immediate dispatch, delayed dispatch, recurring schedules
(cron syntax), and job priorities. Implement reliable execution with
configurable retry on failure, dead-letter storage, and completion
callbacks. Store jobs in-memory (default) or Fluent-backed persistence.
Add an admin API for listing, retrying, and cancelling jobs. Integrate
with the application lifecycle for graceful shutdown of running jobs.

### W5: Implement GraphQL server integration

Add a GraphQL execution layer alongside REST routing. Support schema
definition via Swift types with `@GraphQLType` and `@GraphQLField`
property wrappers, query parsing, validation, and execution. Implement
`DataLoader` for batched N+1 query resolution. Support subscriptions
via WebSocket using the `graphql-ws` protocol. Add query complexity
analysis and depth limiting. Integrate authentication and middleware
context so GraphQL resolvers share the same request pipeline as REST
handlers.

### W6: Add multi-tenant isolation framework

Implement tenant-aware request processing. Support tenant resolution
from subdomain, header (`X-Tenant-ID`), or JWT claim. Add a
`TenantMiddleware` that resolves and validates the tenant, attaching
it to the request context. Integrate with Fluent so queries are
automatically scoped to the current tenant via a global query filter.
Support per-tenant configuration (database, feature flags, rate
limits). Add tenant-aware session and cache key prefixing. Include
admin routes for tenant provisioning and listing.

### W7: Implement real-time event broadcasting with channels

Add a pub/sub event system for real-time features. Define typed
events via a `Broadcastable` protocol. Support named channels with
authorization callbacks. Deliver events over WebSocket or
Server-Sent Events, selectable per client. Implement presence
tracking (who is online in a channel). Use an in-memory broker by
default with a Redis adapter for multi-instance deployments.
Integrate with the middleware pipeline for authentication and add
a JavaScript client helper for frontend consumption.

### W8: Add CLI command framework and scaffolding generator

Implement a command subsystem registered via `app.commands.use(...)`.
Commands receive a `CommandContext` with access to the application's
services (Fluent, logger, configuration). Add built-in commands:
`routes` (list all registered routes with middleware),
`migrate` (run/rollback Fluent migrations), `serve` (start server),
and `generate` (scaffold models, controllers, migrations from
templates). Support interactive prompts, progress bars, and colored
output. Allow third-party packages to register commands automatically.

### W9: Implement request pipeline testing DSL

Add a testing library that lets integration tests exercise the full
Vapor pipeline without starting a real HTTP server. Provide a DSL:
`app.test(.GET, "/users") { res in XCTAssertEqual(res.status, .ok) }`.
Support authenticated requests, custom headers, JSON body encoding,
multipart uploads, WebSocket testing, and session persistence across
requests. Add response assertion helpers for status, headers, JSON
body paths, and cookie values. Include snapshot testing for response
bodies with automatic approval workflow.

### W10: Add configuration layering and environment-aware secrets

Implement a unified configuration system that merges values from
command-line flags, environment variables, `.env` files, JSON/YAML
config files, and remote secret stores (AWS SSM, Vault) with a
defined precedence order. Support typed access:
`app.config.get(DatabaseConfig.self)`. Add hot-reload for config
files with change notification callbacks. Validate configuration
at startup, failing fast with descriptive errors for missing
required values. Support per-environment overrides (development,
staging, production) and encrypted local secret storage.
