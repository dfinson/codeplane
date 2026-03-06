# labstack/echo

| Field | Value |
|-------|-------|
| **URL** | https://github.com/labstack/echo |
| **License** | MIT |
| **Language** | Go |
| **Scale** | Medium |
| **Category** | Web framework |
| **Set** | Cutoff |
| **Commit** | `1753170a74959596a69735c553f3fe5a4bd07715` |

## Why this repo

- **Well-structured**: Flat package layout with core types in `echo.go`
  (Echo, Context, HandlerFunc, MiddlewareFunc), radix-tree router in
  `router.go`, request context in `context.go`, route grouping in
  `group.go`, request binding in `binder.go`/`bind.go`, IP extraction
  in `ip.go`, error handling in `httperror.go`, and a rich middleware
  library under `middleware/` (CORS, CSRF, rate limiter, proxy, static,
  compress, auth, etc.).
- **Rich history**: 2K+ commits, 30K+ stars. One of the most popular
  Go web frameworks. PRs cover router performance, middleware additions,
  context enhancements, and security fixes.
- **Permissive**: MIT license.

## Structure overview

```
echo.go                      # Core Echo struct, New(), routing methods, ServeHTTP
context.go                   # Context — request/response, params, binding, rendering
context_generic.go           # Generic context helpers
router.go                    # DefaultRouter — radix tree, Add(), Find()
route.go                     # Route info and matching
group.go                     # Route grouping with prefix and shared middleware
binder.go                    # ValueBinder — typed parameter binding
bind.go                      # DefaultBinder — query/path/form/header binding
binder_generic.go            # Generic binder helpers
ip.go                        # IP extraction with trust options (loopback, private, etc.)
httperror.go                 # HTTPError type and error handling
json.go                      # JSON serializer interface and default implementation
response.go                  # Response writer wrapper
renderer.go                  # Template renderer interface
server.go                    # Server startup and TLS configuration
vhost.go                     # Virtual host routing
version.go                   # Version constant
echotest/
├── context.go               # Test context helpers
└── reader.go                # Test reader helpers
middleware/
├── basic_auth.go            # HTTP Basic Authentication
├── body_dump.go             # Request/response body dump
├── body_limit.go            # Request body size limiter
├── compress.go              # Gzip response compression
├── context_timeout.go       # Context-based request timeout
├── cors.go                  # Cross-Origin Resource Sharing
├── csrf.go                  # CSRF token protection
├── decompress.go            # Request body decompression
├── extractor.go             # Value extractor utilities
├── key_auth.go              # API key authentication
├── method_override.go       # HTTP method override
├── proxy.go                 # Reverse proxy with load balancing
├── rate_limiter.go          # Rate limiting with pluggable stores
├── recover.go               # Panic recovery
├── redirect.go              # HTTP→HTTPS redirect
├── request_id.go            # Request ID generation
├── request_logger.go        # Structured request logging
├── rewrite.go               # URL rewriting
├── secure.go                # Security headers
├── slash.go                 # Trailing slash handling
├── static.go                # Static file serving with directory listing
└── util.go                  # Middleware utility functions
```

## Scale indicators

- ~20 Go source files (core) + ~24 middleware files
- ~11K lines of code (non-test)
- Single package (`echo`) + sub-packages (`middleware`, `echotest`)
- Minimal dependencies (standard library + limited third-party)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add query parameter default value support to Context

The `Context.QueryParam()` method in `context.go` returns an empty
string when a query parameter is missing, and `QueryParamOr()` provides
a default. However, `QueryParams()` returns raw `url.Values` with no
way to specify defaults for multiple parameters at once. Add a
`QueryParamDefaults(defaults map[string]string)` method to `Context`
that returns a `url.Values` map filled with defaults for missing keys.

### N2: Fix DefaultRouter not returning 405 Method Not Allowed

The `DefaultRouter.Find()` method in `router.go` returns a "not found"
result when a path matches but the HTTP method does not. It does not
distinguish between "path not found" (404) and "method not allowed"
(405). Fix `Find()` to check if the path exists with other methods
and return the `Allow` header listing valid methods, enabling the
framework to respond with `405 Method Not Allowed` instead of `404`.

### N3: Add binding error detail to BindingError message

The `BindingError` struct in `binder.go` includes `Field`, `Values`,
and a generic message, but does not include the expected type that
the conversion targeted. When a user passes `"abc"` for an integer
parameter, the error says "invalid value" without stating the expected
type. Add an `ExpectedType` field to `BindingError` and populate it in
all `ValueBinder` type conversion methods (e.g., `Int64()`, `Float64()`).

### N4: Fix CORS middleware not handling multiple Access-Control-Request-Headers

The CORS middleware in `middleware/cors.go` reads
`Access-Control-Request-Headers` from preflight requests but does not
split or validate individual header names when `AllowHeaders` is
configured with specific values. A preflight with multiple headers in
a single comma-separated value may be incorrectly rejected. Fix the
header comparison in `CORSConfig.ToMiddleware()` to properly split and
trim the request header values before matching.

### N5: Add CIDR notation support to rate limiter identifier extraction

The `RateLimiterConfig.IdentifierExtractor` in `middleware/rate_limiter.go`
defaults to extracting the client IP via `c.RealIP()`, but treats each
IP individually. For IPv6 clients where the address may change within
a `/64` prefix, this allows rate limit evasion. Add a `CIDRPrefix`
configuration option that masks the extracted IP to a CIDR prefix
(e.g., `/24` for IPv4, `/64` for IPv6) before using it as the rate
limit key.

### N6: Fix static middleware not setting Cache-Control headers

The `StaticConfig.ToMiddleware()` in `middleware/static.go` serves
static files but does not set `Cache-Control`, `ETag`, or
`Last-Modified` headers for cache control. Browsers re-download files
on every request. Add `CacheControl` and `MaxAge` configuration options
to `StaticConfig` that set appropriate cache headers based on the
served file's modification time.

### N7: Add request body hash computation to body dump middleware

The `BodyDump` middleware in `middleware/body_dump.go` captures the
request and response bodies for inspection but does not compute a
content hash. Add an optional `ComputeHash` field to `BodyDumpConfig`
that, when enabled, computes a SHA-256 hash of the request body and
passes it to the handler function for integrity verification or
logging purposes.

### N8: Fix context timeout middleware not canceling on client disconnect

The `ContextTimeout` middleware in `middleware/context_timeout.go` sets
a timeout on the request context but does not detect when the client
disconnects early. If the client closes the connection before the
timeout, the handler continues executing until the timeout expires.
Use `http.CloseNotifier` or `request.Context().Done()` to cancel the
handler's context immediately when the client disconnects.

### N9: Add configured trust options to IPChecker string representation

The `ipChecker` struct in `ip.go` stores trust configuration
(loopback, link-local, private net, custom ranges) but has no
`String()` method for debugging. When misconfigured, there is no way
to inspect which trust options are active. Add a `String()` method to
`ipChecker` that lists the active trust options and configured IP
ranges in a human-readable format.

### N10: Fix Group.Static not applying group-level middleware

The `Group.Static()` method in `group.go` registers a static file
route using `e.Static()` on the parent Echo instance rather than going
through the group's middleware chain. Middleware registered via
`g.Use()` is bypassed for static routes. Fix `Group.Static()` to
wrap the static handler with the group's middleware stack, consistent
with how `Group.GET()` works.

## Medium

### M1: Implement route metadata and documentation annotations

Add support for attaching metadata to routes: description, tags,
deprecation status, and custom key-value pairs. Expose a
`RouteInfo.Meta` map populated from a `Meta` option in route
registration. Add an `e.RoutesDocs()` method that returns all route
metadata in a structured format. Changes span `echo.go` for
`RouteInfo` metadata, `router.go` for metadata storage, `group.go`
for metadata propagation, and `route.go` for the documentation output.

### M2: Add request validation middleware with struct tag support

Implement a validation middleware that validates request payloads
against struct tag rules (e.g., `validate:"required,min=1,max=100"`).
Integrate with `Context.Bind()` in `context.go` so validation runs
automatically after binding. Support custom validators registered on
the Echo instance. Add detailed validation error responses listing
all failed fields. Changes span `context.go` for validation integration,
`echo.go` for validator registration, `binder.go` for post-bind
validation hooks, and a new `middleware/validate.go` for the middleware.

### M3: Implement response caching middleware with ETag support

Add a caching middleware that stores responses in a pluggable cache
store. Generate ETags from response body hashes, support
`If-None-Match` conditional requests with `304 Not Modified` responses,
and implement `Cache-Control` header-based caching policies. Changes
span a new `middleware/cache.go` module, `context.go` for response
body interception, `response.go` for response buffering support,
and `echo.go` for cache store configuration.

### M4: Add WebSocket upgrade support to Context

The Echo framework does not provide built-in WebSocket support.
Implement `Context.WebSocket()` that upgrades the HTTP connection
to a WebSocket connection using the standard `gorilla/websocket` or
`nhooyr.io/websocket` upgrade handshake. Handle the upgrade in
`context.go`, add WebSocket-aware route registration in `echo.go`
and `group.go`, and implement connection lifecycle management with
`OnConnect`/`OnMessage`/`OnClose` callbacks.

### M5: Implement middleware execution order control

The `Use()` method in `echo.go` and `group.go` appends middleware in
registration order, but there is no way to control priority or insert
middleware before/after specific positions. Add `UseBefore(target,
middleware)` and `UseAfter(target, middleware)` methods that insert
middleware relative to a named middleware. Require middleware to
implement a `Name() string` method for identification. Changes span
`echo.go` for middleware ordering, `group.go` for group-level ordering,
and `middleware/middleware.go` for the naming interface.

### M6: Add structured error response formatting

The `DefaultHTTPErrorHandler` in `echo.go` returns error responses
with a fixed format (`{message, error}`). Add a configurable error
formatter that supports RFC 7807 Problem Details format, custom
error envelopes, and field-level validation error grouping. Changes
span `echo.go` for the error handler configuration, `httperror.go`
for problem details types, `context.go` for error response helpers,
and `middleware/recover.go` for panic error formatting.

### M7: Implement route-level request size limits

The `BodyLimit` middleware in `middleware/body_limit.go` applies a
global body size limit, but there is no way to set per-route limits.
A file upload route might need 100MB while API routes need 1MB. Add
a `BodyLimit` option to route registration that overrides the global
middleware. Changes span `echo.go` for route option handling,
`router.go` for limit storage per route, `context.go` for limit
enforcement during body reading, and `group.go` for group-level
default limits.

### M8: Add request/response logging middleware with structured output

The `RequestLogger` middleware in `middleware/request_logger.go`
provides basic logging but does not output structured JSON logs with
configurable fields. Add structured logging support with field
selection (method, path, status, latency, body size, headers, user
agent, request ID), log level based on status code, and pluggable
output format (JSON, logfmt, text). Changes span
`middleware/request_logger.go` for structured output, `context.go`
for timing data, `response.go` for response size tracking, and
`middleware/request_id.go` for ID integration.

### M9: Implement virtual host routing with per-host middleware

The `vhost.go` module provides basic virtual host routing but does
not support per-host middleware stacks or per-host error handlers.
Add `VHost` configuration that maps hostnames to sub-Echo instances,
each with independent middleware, routes, and error handlers. Support
wildcard host patterns (e.g., `*.example.com`). Changes span
`vhost.go` for host-to-instance mapping, `echo.go` for sub-instance
creation, `server.go` for request dispatch, and `group.go` for
host-scoped groups.

### M10: Add automatic HTTPS redirect with HSTS support

The `Redirect` middleware in `middleware/redirect.go` handles basic
HTTP-to-HTTPS redirection but does not support HSTS headers or
configurable redirect status codes. Add HSTS header injection with
`max-age`, `includeSubDomains`, and `preload` support. Add
configuration for redirect status code (301 vs 308) and path
exclusions. Changes span `middleware/redirect.go` for HSTS and
configuration, `middleware/secure.go` for HSTS header coordination,
`echo.go` for global HSTS configuration, and `server.go` for
TLS-aware redirect decisions.

## Wide

### W1: Implement an OpenAPI 3.0 specification generator

Add automatic OpenAPI spec generation from registered routes. Extract
path parameters, query parameters, request body schemas, and response
schemas from route metadata and struct tags. Support security scheme
declarations, tags, descriptions, and schema references. Serve the
spec as JSON/YAML at a configurable endpoint. Changes span a new
`openapi.go` module, `echo.go` for route metadata collection,
`router.go` for route iteration, `context.go` for schema extraction,
`binder.go` for parameter type reflection, `group.go` for tag
assignment, and `middleware/static.go` for Swagger UI serving.

### W2: Add distributed session management

Implement server-side session management with pluggable stores
(in-memory, Redis, cookie-based). Support session creation, retrieval,
update, and deletion via `Context.Session()`. Include session ID
generation, cookie configuration (secure, httponly, samesite), session
expiration, and flash message support. Changes span a new
`middleware/session.go` module, `context.go` for session access
methods, `echo.go` for session store configuration, `group.go` for
session-scoped routes, `middleware/csrf.go` for session-backed CSRF
tokens, and `response.go` for Set-Cookie header management.

### W3: Implement a testing framework for Echo applications

Add a comprehensive testing framework beyond `echotest/`. Include a
`TestServer` that starts an Echo instance on a random port, a
`TestClient` with chainable request builders and assertion helpers,
response snapshot testing, middleware testing utilities, and WebSocket
test helpers. Changes span a new `echotest/server.go` module,
`echotest/client.go` for the test client, `echotest/context.go` for
context mocking enhancements, `echo.go` for test-mode configuration,
`response.go` for response capture, `middleware/middleware.go` for
middleware test utilities, and `router.go` for route introspection.

### W4: Add internationalization support for error messages and responses

Implement i18n across Echo's user-facing output. Support locale
detection from `Accept-Language` headers, URL prefixes, or cookies.
Add message catalogs for built-in error messages (404, 405, 500) and
a translation API on `Context`. Include pluralization support and
locale-aware number/date formatting. Changes span a new `i18n.go`
module, `context.go` for locale detection and translation methods,
`echo.go` for catalog registration, `httperror.go` for translated
error messages, `middleware/` for locale middleware, and `group.go`
for locale-scoped route groups.

### W5: Implement request/response transformation pipeline

Add a transformation pipeline that allows chaining ordered
transformers for request and response processing. Transformers can
modify headers, body content, and URL. Support content-type-based
transformer selection, conditional transformers, and built-in
transformers for header normalization, response envelope wrapping,
and body sanitization. Changes span a new `transform.go` module,
`context.go` for transformer context access, `echo.go` for transformer
registration, `group.go` for group-level transformers, `response.go`
for response body interception, `binder.go` for request body
transformation before binding, and `middleware/middleware.go` for
transformer middleware integration.

### W6: Add comprehensive metrics collection and export

Implement request metrics collection (request count, latency
histograms, error rates, active connections, response sizes) with
export to Prometheus, StatsD, and JSON endpoints. Support per-route
metrics labels, custom metric registration, and metric middleware.
Changes span a new `metrics.go` module, `middleware/metrics.go` for
the metrics middleware, `echo.go` for metrics registry configuration,
`context.go` for per-request metric recording, `response.go` for
response size tracking, `server.go` for connection-level metrics,
`router.go` for route label extraction, and `group.go` for
group-scoped metric labels.

### W7: Implement circuit breaker pattern for proxy middleware

Extend the `Proxy` middleware in `middleware/proxy.go` with circuit
breaker support. Track failure rates per backend target, open the
circuit after configurable failure thresholds, implement half-open
probing for recovery detection, and support fallback handlers when
all backends are unavailable. Add circuit state monitoring and event
hooks. Changes span `middleware/proxy.go` for circuit breaker logic,
a new `middleware/circuit_breaker.go` for the circuit breaker state
machine, `echo.go` for global circuit breaker configuration,
`httperror.go` for circuit breaker error types, `context.go` for
circuit state headers, and `middleware/util.go` for health check
utilities.

### W8: Add GraphQL endpoint support

Implement GraphQL support with schema definition, resolver
registration, query execution, and subscription handling. Add
`e.GraphQL(path, schema)` for endpoint registration, support query
batching, persisted queries, and introspection. Include playground
serving for development. Changes span a new `graphql.go` module for
schema and resolver types, `echo.go` for GraphQL endpoint registration,
`router.go` for GraphQL route handling, `context.go` for GraphQL
request parsing, `middleware/body_limit.go` for GraphQL-aware body
limits, `response.go` for streaming subscription responses, and
`group.go` for GraphQL route grouping.

### W9: Implement configuration hot-reload

Add runtime configuration hot-reloading that watches configuration
files and applies changes without server restart. Support reloading
middleware configuration, route definitions, TLS certificates, and
server settings. Include change validation before apply, rollback on
invalid configuration, and reload event hooks. Changes span a new
`config.go` module for configuration management, `echo.go` for
reload triggers, `server.go` for TLS certificate reloading, `router.go`
for dynamic route registration/removal, `middleware/middleware.go` for
middleware reconfiguration, `group.go` for group reconfiguration, and
`context.go` for configuration access.

### W10: Add multi-protocol support with gRPC gateway

Implement a gRPC gateway that translates HTTP/JSON requests to gRPC
calls and vice versa. Add automatic proto-to-REST mapping, request
transcoding, streaming support, and gRPC error-to-HTTP status mapping.
Include health check proxying and reflection API support. Changes span
a new `grpc.go` module for gateway logic, `echo.go` for gRPC service
registration, `router.go` for gRPC route generation, `context.go` for
proto message binding, `response.go` for streaming response support,
`server.go` for HTTP/2 transport configuration, and `binder.go` for
protobuf field binding.
