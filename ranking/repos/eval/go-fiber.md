# gofiber/fiber

| Field | Value |
|-------|-------|
| **URL** | https://github.com/gofiber/fiber |
| **License** | MIT |
| **Language** | Go |
| **Scale** | Medium-large |
| **Category** | Express-inspired web framework for Go |
| **Set** | eval |
| **Commit** | `f36904db43e5499929f515332c8883f3ffada979` |

## Why this repo

- **Multi-subsystem**: HTTP routing, middleware stack, request/response context, client HTTP, binder/validation, session management, static file serving
- **Well-structured**: Core framework files at root, 30+ middleware packages, dedicated client package, binder subsystem
- **Rich history**: 10K+ commits, high-performance framework built on fasthttp with Express-like API

## Structure overview

```
fiber/
├── app.go                   # Application setup, configuration, server lifecycle
├── ctx.go                   # Request context: method, path, IP, userdata
├── req.go                   # Request helpers: headers, cookies, body, query
├── res.go                   # Response helpers: status, headers, body, send
├── router.go                # HTTP method routing, route registration
├── group.go                 # Route grouping with shared prefix/middleware
├── bind.go                  # Request binding facade
├── path.go                  # URL path parsing, parameter extraction
├── redirect.go              # Redirect helpers
├── hooks.go                 # Lifecycle hooks (OnRoute, OnListen, OnShutdown)
├── listen.go                # Server listener, TLS, prefork
├── mount.go                 # Sub-app mounting
├── state.go                 # Application-level state management
├── prefork.go               # Pre-fork process management
├── helpers.go               # Internal utilities
├── binder/                  # Request data binding
│   ├── json.go, xml.go, form.go, query.go, cookie.go
│   ├── header.go, uri.go, cbor.go, msgpack.go
│   └── mapping.go           # Struct tag reflection mapping
├── client/                  # HTTP client
│   ├── client.go            # Client configuration, interceptors
│   ├── request.go           # Request builder
│   ├── response.go          # Response handling
│   ├── hooks.go             # Request/response hooks
│   └── cookiejar.go         # Cookie persistence
├── middleware/               # 30+ middleware packages
│   ├── cors/                # Cross-origin resource sharing
│   ├── csrf/                # CSRF protection (7 files)
│   ├── cache/               # Response caching (5 files)
│   ├── limiter/             # Rate limiting (6 files)
│   ├── session/             # Session management (6 files)
│   ├── logger/              # Request logging (9 files)
│   ├── compress/            # Response compression
│   ├── proxy/               # Reverse proxy
│   ├── recover/             # Panic recovery
│   ├── basicauth/           # Basic authentication
│   ├── keyauth/             # API key authentication
│   ├── helmet/              # Security headers
│   ├── etag/                # ETag caching
│   ├── static/              # Static file serving
│   ├── timeout/             # Request timeout
│   ├── idempotency/         # Idempotent request handling (5 files)
│   └── ...                  # encryptcookie, envvar, expvar, favicon, etc.
├── log/                     # Logging interface
└── addon/retry/             # Retry addon
```

## Scale indicators

- ~145 Go source files (non-test)
- ~99K total lines (including tests)
- 30+ middleware packages with independent configurations
- Dedicated HTTP client with hook system

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix route parameter parsing not handling URL-encoded slashes in catch-all params

When a route uses a catch-all parameter like `/files/*filepath`, URL-encoded slashes (`%2F`) in the parameter value are decoded before matching, causing nested path segments to split incorrectly. The path parsing in `path.go` decodes percent-encoded characters before parameter extraction.

### N2: Fix `c.Redirect().Route()` not preserving query parameters from the original request

When using `c.Redirect().Route("name")` in `redirect.go`, query parameters from the incoming request are dropped. The redirect target is built from the route pattern only, without forwarding query string values.

### N3: Add `c.Deadline()` method to expose request context deadline to handlers

Handlers have no direct way to check the remaining time budget for a request. Add `c.Deadline()` to the context interface in `ctx_interface.go` and `ctx.go` that returns the deadline from the underlying `context.Context`.

### N4: Fix `Bind().JSON()` not returning proper error for content-type mismatch

When a request has `Content-Type: text/plain` but the handler calls `c.Bind().JSON()`, the binder in `binder/json.go` attempts to parse the body as JSON anyway, producing a confusing parse error instead of a content-type mismatch error.

### N5: Fix CORS middleware not handling `Access-Control-Request-Private-Network` header

The CORS middleware in `middleware/cors/` doesn't handle the `Access-Control-Request-Private-Network` preflight header from the Private Network Access specification. Preflight requests with this header receive no `Access-Control-Allow-Private-Network` response.

### N6: Add `OnError` hook for global error handling lifecycle events

The hooks system in `hooks.go` supports `OnRoute`, `OnListen`, and `OnShutdown` but has no hook for error handling events. Add `OnError` that fires whenever `ErrorHandler` processes an error, providing the error, context, and status code to registered callbacks.

### N7: Fix rate limiter middleware not resetting counters on sliding window boundary

The rate limiter in `middleware/limiter/` uses fixed window counters. When a burst of requests arrives at the boundary between two windows, the effective rate can be double the configured limit. The sliding window logic doesn't properly interpolate between windows.

### N8: Fix `c.SendFile()` not setting `Last-Modified` header for embedded filesystem files

When serving files from an `embed.FS` via `c.SendFile()`, the `Last-Modified` header is missing because the embedded filesystem doesn't provide `ModTime()`. The file serving logic in `res.go` should fall back to the application start time.

### N9: Add `MsgPack` response helper to the response interface

The response interface in `res.go` supports `JSON()`, `XML()`, and `CBOR()` but lacks a `MsgPack()` helper for MessagePack serialization, despite having a MsgPack binder in `binder/msgpack.go`. Add `c.MsgPack(data)` for symmetric serialization support.

### N10: Fix session middleware cookie attributes not updated on `session.Save()`

When session configuration changes cookie attributes (e.g., `MaxAge`) between requests, calling `session.Save()` in `middleware/session/` writes the session data but uses the original cookie attributes from session creation rather than current configuration.

## Medium

### M1: Implement request body streaming for large file uploads

Currently `c.Body()` reads the entire request body into memory. Add `c.BodyStream()` that returns an `io.Reader` for streaming processing of large uploads. Changes span `req.go` for the streaming API, `ctx.go` for context lifecycle, `binder/` for stream-aware binding, and `middleware/compress/` for decompression streaming.

### M2: Add route versioning with content negotiation

Implement API versioning via `app.Version("v2", group)` that routes requests based on `Accept-Version` header, URL prefix, or custom header. Changes span `router.go` for version-aware routing, `group.go` for versioned groups, `app.go` for version registration, and `path.go` for version prefix handling.

### M3: Implement typed route parameters with automatic parsing

Add `c.Params().Int("id")`, `c.Params().UUID("token")` with typed extraction and validation error handling. Changes span `ctx.go` for the typed params API, `path.go` for parameter type annotations in route patterns, `router.go` for type-aware matching, and `error.go` for parameter validation errors.

### M4: Add HTTP client retry with circuit breaker pattern

Extend `client/` with circuit breaker support: track failure rates per host, open/half-open/closed states, configurable thresholds, and fallback handlers. Changes span `client/client.go` for circuit breaker integration, `client/hooks.go` for failure tracking hooks, `addon/retry/` for retry coordination, and `client/transport.go` for connection-level tracking.

### M5: Implement request validation middleware with struct tag rules

Add validation middleware that validates bound structs using field tags (`validate:"required,min=1,max=100"`). Changes span `binder/mapping.go` for tag parsing, `bind.go` for validation integration, `error.go` for structured validation errors, and add a `middleware/validator/` package.

### M6: Add response caching with tag-based invalidation

Extend `middleware/cache/` with cache tag support: handlers tag responses with cache keys, and invalidation can purge by tag. Changes span `middleware/cache/` for tag storage and invalidation API, `ctx.go` for cache tag context methods, `middleware/cache/manager.go` for tag-indexed storage, and `app.go` for cache invalidation hooks.

### M7: Implement WebSocket support with room-based broadcasting

Add WebSocket handling with connection grouping, room join/leave, and broadcast capabilities. Changes span `app.go` for WebSocket upgrade configuration, `router.go` for WebSocket route registration, `ctx.go` for upgrade handling, and add a `middleware/websocket/` package with connection management.

### M8: Add structured error responses with problem details (RFC 9457)

Implement RFC 9457 Problem Details error responses: type URI, status, title, detail, and extension fields. Changes span `error.go` for `ProblemDetail` type, `app.go` for problem-details-aware error handler, `ctx.go` for problem response helpers, and `middleware/recover/` for panic-to-problem conversion.

### M9: Implement server-sent events (SSE) support

Add `c.SSEvent()` for sending server-sent events with proper `text/event-stream` content type, event naming, retry configuration, and connection keep-alive. Changes span `res.go` for SSE response methods, `ctx.go` for SSE lifecycle, `app.go` for SSE configuration, and handle fasthttp streaming constraints.

### M10: Add request tracing with OpenTelemetry integration

Implement distributed tracing middleware that creates spans for request handling, propagates trace context, and records attributes. Changes span `middleware/` for a new `otel/` package, `ctx.go` for trace context propagation, `client/hooks.go` for outgoing request span creation, and `router.go` for span naming from route patterns.

## Wide

### W1: Implement gRPC gateway for serving gRPC-Web and REST from the same server

Add gRPC service registration alongside HTTP routes, with automatic REST-to-gRPC translation based on protobuf annotations. Changes span `app.go` for gRPC server integration, `router.go` for gRPC route registration, `ctx.go` for gRPC context adaptation, `binder/` for protobuf binding, `res.go` for protobuf response serialization, and add gRPC gateway middleware.

### W2: Add comprehensive API documentation generation

Implement automatic OpenAPI 3.1 spec generation from route definitions, handler types, and binder structs. Changes span `router.go` for route metadata collection, `app.go` for spec generation API, `group.go` for grouped route documentation, `binder/mapping.go` for schema extraction, `path.go` for parameter documentation, `error.go` for error schema generation, and add an OpenAPI module.

### W3: Implement multi-tenant request isolation

Add tenant-aware request processing: per-tenant middleware stacks, isolated state, tenant-specific rate limits, and tenant resolution from headers/subdomains. Changes span `app.go` for tenant configuration, `router.go` for tenant-scoped routing, `ctx.go` for tenant context, `state.go` for tenant-isolated state, `middleware/limiter/` for per-tenant limits, `middleware/session/` for tenant sessions, and add tenant resolution middleware.

### W4: Add real-time metrics and monitoring dashboard

Implement request metrics collection (latency histograms, error rates, route-level stats) with a built-in dashboard. Changes span `app.go` for metrics collection, `ctx.go` for request timing, `router.go` for route-level metrics, `middleware/logger/` for metric-aware logging, `hooks.go` for metric lifecycle, `middleware/` for a metrics package, and add dashboard UI serving.

### W5: Implement automatic HTTPS with Let's Encrypt certificate management

Add automatic TLS certificate provisioning and renewal using ACME. Changes span `listen.go` for ACME integration, `app.go` for certificate configuration, `router.go` for ACME challenge routes, `middleware/redirect/` for HTTP-to-HTTPS redirect, `hooks.go` for certificate lifecycle events, and add certificate storage and renewal modules.

### W6: Add GraphQL endpoint support with schema-first approach

Implement GraphQL request handling: schema parsing, query execution, subscription support via WebSocket, and DataLoader-style batching. Changes span `app.go` for GraphQL endpoint registration, `router.go` for GraphQL route handling, `ctx.go` for GraphQL context, `binder/` for GraphQL variable binding, `res.go` for GraphQL response formatting, `middleware/` for GraphQL-specific middleware, and add GraphQL execution engine.

### W7: Implement service mesh sidecar integration

Add service mesh support: service discovery, load balancing, health checking, retry policies, and circuit breaking for inter-service communication. Changes span `client/client.go` for service discovery, `client/hooks.go` for load balancer selection, `middleware/healthcheck/` for mesh health probes, `app.go` for service registration, `listen.go` for mesh protocol support, `addon/retry/` for mesh retry policies, and add service mesh integration modules.

### W8: Add database-backed session with migration and multi-store support

Implement session storage backends for PostgreSQL, Redis, and SQLite with automatic schema migration, session garbage collection, and store failover. Changes span `middleware/session/` for store interface extension, `middleware/session/store.go` for multi-backend support, `app.go` for session store configuration, `hooks.go` for GC scheduling, `internal/storage/` for backend implementations, and add migration infrastructure.

### W9: Implement request replay and debugging proxy

Add a debugging mode that records request/response pairs, supports replay for debugging, and provides a live request inspector UI. Changes span `ctx.go` for request recording, `app.go` for debug mode configuration, `req.go` for request serialization, `res.go` for response capture, `middleware/logger/` for replay-aware logging, `hooks.go` for recording lifecycle, and add replay storage and inspector UI modules.

### W10: Add comprehensive load testing and benchmarking framework

Implement a built-in load testing tool: scenario definition, concurrent request generation, latency percentile reporting, and comparison with baseline results. Changes span `app.go` for test mode, `client/` for concurrent request generation, `middleware/` for a benchmarking middleware, `hooks.go` for metric collection, `router.go` for route profiling, and add scenario definition, runner, and reporting modules.
