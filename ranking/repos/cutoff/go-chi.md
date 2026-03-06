# go-chi/chi

| Field | Value |
|-------|-------|
| **URL** | https://github.com/go-chi/chi |
| **License** | MIT |
| **Language** | Go |
| **Scale** | Medium |
| **Category** | HTTP router |
| **Set** | Cutoff |
| **Commit** | `4eff323f8e26176988c7f5dcb0357ed21d1caae7` |

## Why this repo

- **Well-structured**: Compact single-package core with a clean radix-
  tree router (`tree.go`), a composable mux (`mux.go`), a context-based
  URL parameter system (`context.go`), and a middleware chain builder
  (`chain.go`). The `middleware/` subpackage provides ~25 production-
  ready middlewares covering compression, rate limiting, real-IP
  extraction, panic recovery, request IDs, content negotiation, caching
  headers, and more. Each middleware is a self-contained file with
  minimal coupling. Examples in `_examples/` demonstrate real patterns.
- **Rich history**: 19K+ stars, 900+ commits. One of the most popular
  Go HTTP routers, widely used in production. Issues span routing edge
  cases, middleware ordering, mount behavior, context propagation, and
  radix-tree pattern conflicts.
- **Permissive**: MIT license.

## Structure overview

```
chi/
├── chi.go                         # Package docs, NewRouter(), Router interface, Routes interface
├── mux.go                        # Mux struct — ServeHTTP, Use, Route, Mount, Group, With, NotFound, MethodNotAllowed
├── tree.go                       # Radix trie — node types (static, regexp, param, catchAll), addRoute, findRoute, RegisterMethod
├── context.go                    # RouteContext — URLParam, URLParamFromCtx, RouteContext(), Context struct, RouteParams
├── chain.go                     # Chain() — build net/http middleware chain from variadic handlers
├── middleware/
│   ├── basic_auth.go             # BasicAuth — HTTP Basic authentication middleware
│   ├── clean_path.go             # CleanPath — redirect unclean paths to canonical form
│   ├── compress.go               # Compress — gzip/deflate response compression with level control
│   ├── content_charset.go        # ContentCharset — reject requests with unsupported charset
│   ├── content_encoding.go       # ContentEncoding — reject unsupported Content-Encoding
│   ├── content_type.go           # AllowContentType / SetContentType — Content-Type enforcement
│   ├── get_head.go               # GetHead — automatic HEAD response from GET handlers
│   ├── heartbeat.go              # Heartbeat — simple health-check endpoint at a configurable path
│   ├── logger.go                 # Logger — request/response logging with customizable formatter
│   ├── maybe.go                  # Maybe — conditionally apply a middleware based on a predicate
│   ├── middleware.go             # Package-level helpers — New() wraps http.Handler as middleware
│   ├── nocache.go                # NoCache — set cache-control headers to prevent caching
│   ├── page_route.go             # PageRoute — strip trailing slash pattern helper
│   ├── path_rewrite.go           # PathRewrite — rewrite request path before routing
│   ├── profiler.go               # Profiler — mount net/http/pprof endpoints as a subrouter
│   ├── realip.go                 # RealIP — extract client IP from X-Forwarded-For / X-Real-IP
│   ├── recoverer.go              # Recoverer — panic recovery with stack trace logging
│   ├── request_id.go             # RequestID — inject unique request ID via X-Request-Id header
│   ├── request_size.go           # RequestSize — limit request body size
│   ├── route_headers.go          # RouteHeaders — route by header values with pattern matching
│   ├── strip.go                  # StripSlashes / RedirectSlashes — trailing slash handling
│   ├── sunset.go                 # Sunset — set Sunset and Deprecation headers on responses
│   ├── supress_notfound.go       # SuppressNotFound — prevent 404 from mounted subrouters
│   ├── terminal.go               # Terminal — mark a route as terminal (no further routing)
│   ├── throttle.go               # Throttle — token-bucket rate limiting with backlog queue
│   ├── timeout.go                # Timeout — context-based request timeout
│   ├── url_format.go             # URLFormat — parse .json/.xml extension from URL into context
│   ├── value.go                  # SetValue / GetValue — per-request context value helpers
│   └── wrap_writer.go            # WrapResponseWriter — wrappable ResponseWriter with status/bytes tracking
└── _examples/
    ├── hello-world/main.go        # Minimal chi usage
    ├── rest/main.go               # RESTful API with sub-routers
    ├── todos-resource/            # Resource-oriented CRUD pattern
    ├── versions/                  # API versioning with presenter pattern
    ├── fileserver/main.go         # Static file serving
    ├── graceful/main.go           # Graceful shutdown
    ├── limits/main.go             # Rate limiting and request size
    ├── logging/main.go            # Custom logging middleware
    ├── router-walk/main.go        # Walking the route tree
    └── custom-method/main.go      # Custom HTTP method registration
```

## Scale indicators

- ~8 Go source files (core library)
- ~1,750 lines core code + ~4,400 lines middleware
- Flat structure (single package + `middleware/` subpackage)
- Zero external dependencies (stdlib only)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix Mount not stripping trailing slash from the mount pattern

When `r.Mount("/api/", subRouter)` is called with a trailing slash,
requests to `/api` (without trailing slash) return 404 instead of being
routed to the sub-router. The `Mount` method in `mux.go` stores the
pattern as-is in the radix tree via `addRoute` in `tree.go`, but the
tree's `findRoute` does not match a request path of `/api` against a
pattern of `/api/`. Fix `Mount` to normalize trailing slashes on the
mount pattern so that both `/api` and `/api/` resolve correctly.

### N2: Fix RealIP middleware not validating X-Forwarded-For against trusted proxies

The `RealIP` middleware in `middleware/realip.go` blindly trusts the
first IP in the `X-Forwarded-For` header chain. An untrusted client
can spoof its IP by setting the header directly. Fix `RealIP` to accept
a configurable list of trusted proxy CIDRs and walk the
`X-Forwarded-For` chain from right to left, selecting the first IP
not in a trusted range.

### N3: Fix Compress middleware not setting Vary: Accept-Encoding header

The `Compress` middleware in `middleware/compress.go` compresses
responses based on the `Accept-Encoding` request header but does not
add `Vary: Accept-Encoding` to the response. Downstream caches may
serve compressed content to clients that do not support it. Fix the
compressor to always set the `Vary` header when compression is
potentially applied.

### N4: Fix RouteContext.URLParam returning empty string for regex-constrained parameters

When a route is defined as `r.Get("/item/{id:[0-9]+}", handler)`, and
the request matches, `chi.URLParam(r, "id")` returns an empty string.
The regex pattern matching in `tree.go`'s `findRoute` correctly
validates the parameter but the matched value is stored under the full
key `id:[0-9]+` instead of just `id` in the `RouteParams`. Fix the
parameter key extraction in `tree.go` to strip the regex portion before
storing the key in `Context.URLParams`.

### N5: Fix Throttle middleware leaking goroutines when the backlog queue is full

When the `Throttle` middleware in `middleware/throttle.go` has a full
backlog queue, it returns 429 Too Many Requests. However, if the
request's context is cancelled while waiting in the backlog queue,
the goroutine that was waiting on the token channel is never cleaned
up. Fix the backlog select to also listen on `<-r.Context().Done()`
and release resources on cancellation.

### N6: Fix WrapResponseWriter not implementing http.Pusher interface

The `WrapResponseWriter` in `middleware/wrap_writer.go` wraps the
underlying `http.ResponseWriter` and implements `http.Flusher` and
`http.Hijacker` via type assertions, but does not check for or
delegate `http.Pusher` (used for HTTP/2 server push). Middleware that
wraps the writer (e.g., Compress, Logger) silently drops HTTP/2 push
capability. Fix `WrapResponseWriter` to also implement `http.Pusher`
when the underlying writer supports it.

### N7: Fix URLFormat middleware not handling paths with multiple dots

The `URLFormat` middleware in `middleware/url_format.go` extracts the
file extension from the last path segment (e.g., `/api/data.json`
sets format to `json`). However, paths like `/api/v2.1/data.json`
incorrectly extract `1/data.json` as the format because the logic
splits on the first dot rather than the last. Fix the dot-finding
logic to use `strings.LastIndex` on the final path segment only.

### N8: Fix Recoverer middleware not including the request method and path in panic output

When a handler panics, the `Recoverer` middleware in
`middleware/recoverer.go` logs the stack trace but does not include
the HTTP method or request path, making it difficult to identify which
request triggered the panic. Fix the recovery handler to prepend
`r.Method` and `r.URL.Path` to the logged output before the stack
trace.

### N9: Fix RequestID middleware generating non-unique IDs under high concurrency

The `RequestID` middleware in `middleware/request_id.go` uses a simple
counter with `atomic.AddUint64` combined with a prefix. Under very high
concurrency with multiple server instances, the generated IDs are not
globally unique. Fix the generator to incorporate a per-process random
component (e.g., using `crypto/rand` at init time) into the ID format
to ensure uniqueness across instances.

### N10: Fix Timeout middleware not cancelling the context when the handler finishes early

The `Timeout` middleware in `middleware/timeout.go` creates a context
with `context.WithTimeout` but does not call the cancel function when
the handler completes before the deadline. This leaks the timer
goroutine until the timeout expires. Fix the middleware to `defer
cancel()` immediately after creating the timeout context.

## Medium

### M1: Implement route-level middleware declaration

Add `r.With(middlewares...).Get(pattern, handler)` chaining that applies
middlewares only to the specific route, without affecting sibling routes
or the parent router's middleware stack. Requires a middleware-carrying
wrapper in `mux.go`, integration with `tree.go`'s `addRoute` to attach
per-endpoint middleware, execution in the correct order during
`routeHTTP`, and interaction with `chain.go` for composing the
per-route chain with the global stack.

### M2: Add route grouping with shared prefix and error handler

Implement `r.Group("/api/v1", func(r Router) { ... }, WithErrorHandler(fn))`
that applies a shared error handler to all routes within the group.
The error handler receives `(w, r, error)` when a handler returns an
error. Requires an error-aware handler type, group-level error handler
storage on `Mux`, error propagation from `routeHTTP` in `mux.go`,
and fallback to the parent group's error handler if none is set.

### M3: Implement request body buffering middleware with replay support

Add a `BufferBody` middleware that reads the request body into memory
(up to a configurable limit), replaces `r.Body` with a replayable
reader, and allows downstream handlers to call `GetBody()` to re-read.
Requires a new middleware in `middleware/`, integration with
`middleware/request_size.go` for limit coordination, proper cleanup
of the buffered body, and handling of chunked transfer encoding.

### M4: Add structured route table export

Implement `r.Routes().Export() []RouteInfo` that returns a structured
list of all registered routes with their HTTP method, full pattern,
middleware stack names, and handler function name. Requires recursive
tree walking in `tree.go`, handler name resolution via `runtime`,
middleware identification, mount-point awareness for subrouter routes,
and JSON/text serialization formats.

### M5: Implement content negotiation middleware

Add a `ContentNegotiation` middleware that inspects `Accept` and
`Accept-Language` headers and sets context values for the negotiated
content type and language. Support quality-value parsing (q=0.9),
wildcard matching, and fallback defaults. Requires a new middleware
in `middleware/`, quality-value parsing logic, integration with
`middleware/value.go` for context storage, interaction with
`middleware/url_format.go` for format override precedence, and
response `Content-Type` / `Content-Language` header setting.

### M6: Add ETag and conditional request middleware

Implement `ConditionalGet` middleware that computes an ETag from the
response body hash, handles `If-None-Match` / `If-Modified-Since`
headers, and returns 304 Not Modified when appropriate. Requires
response body buffering via `middleware/wrap_writer.go`, hash
computation, ETag header generation, conditional header comparison,
interaction with `middleware/nocache.go` for cache directive
coordination, and support for weak ETags.

### M7: Implement route versioning with Accept header matching

Add `r.Version("v2", func(r Router) { ... })` that routes requests
based on the `Accept` header's version parameter (e.g.,
`Accept: application/json; version=2`). Requires a version-aware
routing layer in `mux.go`, Accept header parsing, fallback to the
latest version, integration with `middleware/route_headers.go` for
header-based routing, and `tree.go` support for version-tagged
endpoints.

### M8: Add request correlation and distributed tracing context middleware

Implement a `TraceContext` middleware that extracts W3C Trace Context
headers (`traceparent`, `tracestate`), generates a new span ID,
propagates the trace context to the response headers, and makes trace
and span IDs available via `chi.TraceID(r)` and `chi.SpanID(r)`.
Requires a new middleware in `middleware/`, W3C traceparent parsing,
context value storage, integration with `middleware/request_id.go`
for ID coordination, and response header injection.

### M9: Implement graceful route migration with shadow routing

Add `r.Shadow(pattern, handler)` that registers a shadow handler
invoked in parallel with the primary handler. The shadow handler's
response is discarded; only the primary response is sent to the
client. Log discrepancies between the two responses. Requires shadow
route storage in `Mux`, parallel invocation in `routeHTTP` via
goroutines, response capture with `wrap_writer.go`, comparison logic,
and structured logging of differences.

### M10: Add per-route rate limiting with configurable rate stores

Extend the `Throttle` middleware to support per-route rate limits with
configurable rate stores (in-memory, Redis). Support rate limit
headers (`X-RateLimit-Limit`, `X-RateLimit-Remaining`,
`X-RateLimit-Reset`). Requires a `RateStore` interface in
`middleware/throttle.go`, an in-memory token-bucket implementation,
per-route rate configuration via route context, header injection,
and integration with the response writer wrapper for header
consistency.

## Wide

### W1: Implement a declarative route definition system from YAML/JSON configuration

Add a configuration-driven route builder that generates a chi router
from YAML or JSON route definitions, supporting nested groups,
middleware references, handler references via registry, path parameters,
and mount points. Include validation for duplicate routes and invalid
patterns. Changes span a new `config/` subpackage for parsing, `mux.go`
for registry-based handler resolution, `tree.go` for validation,
`middleware/` for middleware name registry, and `chi.go` for the
top-level API.

### W2: Implement a full HTTP/2 server push middleware system

Add a push-aware middleware layer that allows handlers to declare push
targets, automatically pushes linked resources (CSS, JS) from HTML
responses, deduplicates push promises, respects client push settings,
and falls back gracefully for HTTP/1.1. Changes span
`middleware/wrap_writer.go` for `http.Pusher` delegation, a new
`middleware/push.go` for push management, `mux.go` for push-target
declaration on routes, integration with `middleware/compress.go` for
pushed content encoding, and `middleware/content_type.go` for MIME
detection.

### W3: Implement an OpenAPI specification generator from the route tree

Add `r.OpenAPI()` that generates a complete OpenAPI 3.0 spec from
registered routes, including path parameters, query parameters (from
middleware annotations), request/response schemas (from handler type
signatures), middleware-derived security schemes, and server
definitions. Changes span `tree.go` for pattern-to-OpenAPI-path
conversion, a new `openapi/` subpackage for spec construction, `mux.go`
for annotation storage on routes, `middleware/basic_auth.go` for
security scheme generation, and schema inference via reflection.

### W4: Implement a WebSocket upgrade and routing system

Add WebSocket support with `r.WebSocket(pattern, handler)` that handles
the HTTP upgrade, provides a clean WebSocket API (`conn.ReadMessage`,
`conn.WriteMessage`), supports per-connection middleware (auth, rate
limiting), connection groups for broadcasting, and graceful connection
shutdown. Changes span a new `ws/` subpackage, `mux.go` for WebSocket
route registration, `tree.go` for upgrade-aware routing, `middleware/`
integration for per-connection middleware, and `context.go` for
WebSocket context values.

### W5: Add request/response transformation pipeline

Implement a `Transform` middleware system that applies ordered
request transformations (header rewriting, body modification, query
parameter injection) before routing and response transformations
(header injection, body filtering, response rewriting) after the
handler. Support conditional transforms based on route pattern,
content type, or custom predicates. Changes span a new
`middleware/transform.go`, `middleware/wrap_writer.go` for response
body interception, `mux.go` for transform registration on routes,
`chain.go` for transform ordering, and `context.go` for transform
state propagation.

### W6: Implement a service mesh sidecar integration layer

Add middleware support for service mesh patterns: circuit breaker
(with configurable thresholds), retry with exponential backoff,
bulkhead isolation (per-endpoint concurrency limits), health
aggregation from downstream services, and service discovery
integration. Changes span new middlewares in `middleware/` (circuit
breaker, retry, bulkhead), `mux.go` for per-route resilience
configuration, `middleware/heartbeat.go` for aggregated health,
`middleware/timeout.go` for coordinated timeouts, and `context.go`
for resilience state propagation.

### W7: Implement hot-reloadable route configuration

Add `r.LiveReload(configPath)` that watches a YAML route configuration
file and atomically swaps the route tree on changes without dropping
active connections. Support route diffing (added/removed/changed),
graceful drain of in-flight requests on removed routes, middleware
stack updates, and a reload API endpoint. Changes span `mux.go` for
atomic mux swapping, `tree.go` for route diffing, a new file watcher
module, `middleware/` for reload-safe middleware state, and `context.go`
for route version tracking.

### W8: Add comprehensive HTTP caching middleware suite

Implement a full caching layer: `Cache` middleware with configurable
backends (in-memory LRU, file-system, Redis interface), cache-key
generation from method + path + Vary headers, `Surrogate-Control`
header support, cache invalidation via PURGE method, conditional
request handling (`If-None-Match`, `If-Modified-Since`), and stale-
while-revalidate support. Changes span new middleware files, `mux.go`
for PURGE route registration, `middleware/wrap_writer.go` for response
body capture, `middleware/nocache.go` for directive coordination, and
a cache backend interface package.

### W9: Implement a built-in reverse proxy and load balancer

Add `r.Proxy(pattern, backends, options)` that reverse-proxies matched
requests to a pool of backend servers with configurable load-balancing
strategies (round-robin, least-connections, weighted), health checking,
automatic backend removal/re-addition, request/response header
rewriting, and WebSocket proxy support. Changes span a new `proxy/`
subpackage, `mux.go` for proxy route registration, `middleware/`
for proxy-specific middlewares (X-Forwarded headers, hop-by-hop
filtering), `middleware/wrap_writer.go` for streaming response relay,
and `middleware/timeout.go` for backend timeout configuration.

### W10: Implement a full testing and benchmarking framework for chi applications

Add a `chitest` package that provides: a test server builder with
automatic route registration, request builders with fluent API,
response assertions (status, headers, body, JSON path), middleware
testing utilities (isolated middleware invocation), route coverage
reporting (which routes were hit by tests), performance benchmarking
with latency percentiles, and load testing with concurrent request
simulation. Changes span a new `chitest/` subpackage, `mux.go` for
route coverage instrumentation, `tree.go` for route enumeration,
`middleware/wrap_writer.go` for response capture, and example tests
in `_examples/`.
