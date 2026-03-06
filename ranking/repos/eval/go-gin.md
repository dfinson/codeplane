# gin-gonic/gin

| Field | Value |
|-------|-------|
| **URL** | https://github.com/gin-gonic/gin |
| **License** | MIT |
| **Language** | Go |
| **Scale** | Medium |
| **Category** | HTTP web framework |
| **Set** | eval |
| **Commit** | `f3e1194361e27f0ed0f6666509d60f15af8b21d8` |

## Why this repo

- **Layered router**: Radix-tree routing with method trees, parameter/wildcard segments, middleware chains
- **Well-scoped**: Clear separation between engine (gin.go), context, routing, binding, render, recovery, logger
- **High adoption**: 80K+ stars, one of the most used Go web frameworks

## Structure overview

```
gin/
├── gin.go               # Engine: router mux, ServeHTTP, Run, route tree init
├── context.go           # Request context: params, query, bind, response render
├── routergroup.go       # Route group: Use, GET/POST/PUT, middleware chaining
├── tree.go              # Radix tree: node insertion, lookup, wildcard matching
├── errors.go            # Error type, error collection on context
├── recovery.go          # Panic recovery middleware
├── logger.go            # Logger middleware, custom formatters
├── response_writer.go   # ResponseWriter wrapper tracking status/size
├── auth.go              # BasicAuth middleware
├── mode.go              # Debug/release/test mode switching
├── path.go              # URL path cleaning
├── fs.go                # File-system helpers
├── utils.go             # Misc utilities
├── binding/             # Request binding: JSON, XML, YAML, form, query, protobuf
├── render/              # Response rendering: JSON, HTML, XML, YAML, protobuf, redirect
├── codec/json/          # Pluggable JSON codec (std, jsoniter, sonic, go_json)
├── ginS/                # Singleton default-engine convenience functions
└── internal/            # bytesconv, fs helpers
```

## Scale indicators

- ~59 Go source files (excluding tests)
- ~8K lines of code
- Radix-tree router with parameter extraction
- Pluggable binding and render subsystems

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `Context.Redirect` allowing redirect with non-redirect status codes

`Context.Redirect` only validates that the status code is 300–308 for GET requests but does not check for other HTTP methods. A POST request can be redirected with status 200, which produces a malformed response with a `Location` header but no redirect semantics.

### N2: Fix route conflict panic not reporting both conflicting paths

When `addRoute` detects a wildcard conflict in `tree.go`, the panic message only shows the new path being inserted. It does not include the existing conflicting route, making it hard to diagnose which prior registration caused the clash.

### N3: Fix `BasicAuth` middleware not using constant-time comparison

The `BasicAuth` middleware in `auth.go` uses `searchCredential` with `subtle.ConstantTimeCompare` for the password but performs an early-exit map lookup on the username. This leaks timing information about whether a username exists.

### N4: Add `Context.RemoveHeader` method

`Context.Header` can set and delete response headers (empty value deletes), but the delete behavior is undiscoverable. Add an explicit `RemoveHeader(key string)` method on `Context` that calls `c.Writer.Header().Del(key)`.

### N5: Fix `LoggerWithConfig` not sanitizing newlines in request path

The default `LoggerWithConfig` writes `c.Request.URL.Path` directly into log output. A request path containing `\n` can inject fake log lines. Sanitize control characters in the path before formatting.

### N6: Fix `tree.getValue` not returning trailing-slash redirect for parameterized routes

When `RedirectTrailingSlash` is enabled and a route `/users/:id/` is registered, a request to `/users/42` (without trailing slash) does not trigger a redirect. The `getValue` function only checks the redirect flag for static parts of the path, not after consuming a parameter segment.

### N7: Add `Context.FullError` to collect all handler errors as a single joined error

`Context.Errors` collects `*Error` values, but there is no convenience method to return a single `error` that joins them. Add `FullError() error` that returns `nil` when empty or `errors.Join`-ed result.

### N8: Fix `Engine.HandleContext` not resetting `Context.index` before re-dispatch

When `HandleContext` is used for internal re-routing, it calls `engine.handleHTTPRequest(c)` without resetting `c.index` to -1. If the previous handler chain called `c.Next()`, the index is at the end and middlewares in the new chain are skipped.

### N9: Fix `SecureJSON` prefix not configurable per-route

`SecureJSONPrefix` is set on the `Engine` globally. When different route groups need different prefixes (e.g., internal vs. external APIs), there is no way to override it. Add a `Context.SetSecureJSONPrefix` method that takes precedence over the engine default.

### N10: Fix `ShouldBindBodyWith` storing body bytes with wrong key for TOML binding

`ShouldBindBodyWith` caches the raw body in `c.Set(BodyBytesKey, ...)` after the first read. But each `BindingBody` type uses the same cache key, so calling `ShouldBindBodyWithJSON` then `ShouldBindBodyWithTOML` reuses the JSON-bound bytes without re-reading. The second binding succeeds or fails based on the first format's cached bytes rather than re-parsing.

## Medium

### M1: Implement request-scoped timeout middleware

Add a `Timeout(duration time.Duration)` middleware that wraps the request context with `context.WithTimeout`. When the timeout fires, the middleware should call `c.Abort()` and respond with 504 Gateway Timeout. Coordinate with `response_writer.go` to prevent double-writes after timeout.

### M2: Add route-level metadata annotations

Implement a mechanism to attach arbitrary metadata (tags, descriptions, deprecation flags) to routes at registration time. Add `RouterGroup.Handle` variant that accepts `RouteOption` functions. Expose metadata through `RoutesInfo` and a new `RouteInfo.Metadata` field.

### M3: Implement ETag response middleware

Add middleware that computes a weak ETag from response body hash, sets the `ETag` header, and returns 304 Not Modified when the request includes a matching `If-None-Match`. Integrate with `ResponseWriter` to buffer the body for hashing before flushing.

### M4: Add request ID middleware with propagation

Implement middleware that reads `X-Request-ID` from the incoming request or generates a UUID if absent. Store it on the context, inject it into all response headers, and make it available in `LogFormatter` via `LogFormatterParams`. Changes touch middleware creation, context, and logger.

### M5: Implement CORS middleware with preflight caching

Add a configurable CORS middleware supporting allowed origins (including wildcards), methods, headers, credentials, max-age. Handle OPTIONS preflight requests. Integrate with `RouterGroup` to allow per-group CORS configuration separate from global settings.

### M6: Add response compression middleware with content negotiation

Implement middleware that compresses response bodies using gzip or deflate based on `Accept-Encoding`. Skip compression for small bodies, already-compressed content types, and SSE streams. Wrap `ResponseWriter` to handle streaming compression.

### M7: Implement structured error handling with error types

Add an `ErrorHandler` function type on `Engine` that is called after the handler chain completes when `Context.Errors` is non-empty. Implement typed error matching so handlers can use `errors.As` on `*gin.Error` to extract domain-specific error types and map them to appropriate HTTP responses.

### M8: Add form binding support for nested structs with array indices

The form binding in `binding/form_mapping.go` does not support nested struct arrays like `items[0].name=foo&items[1].name=bar`. Implement array-index parsing in `form_mapping.go`, update `multipart_form_mapping.go`, and add validation for sparse/out-of-order indices.

### M9: Implement file upload progress callback

Add `Context.SaveUploadedFileWithProgress` that accepts a callback `func(bytesWritten, totalBytes int64)` invoked during file copy. Wrap the `io.Copy` in `SaveUploadedFile` with a progress-reporting `io.Writer` and surface the file size from the multipart header.

### M10: Add render negotiation based on route-level accepted types

Extend `Context.Negotiate` to support route-level configuration of offered formats. Currently `NegotiateFormat` uses the `Accepted` field set globally. Add per-`RouterGroup` default offered types and allow individual handlers to override via `NegotiateConfig` options.

## Wide

### W1: Implement graceful shutdown with connection draining

Add `Engine.Shutdown(ctx context.Context)` that stops accepting new connections, waits for in-flight requests to complete, and respects the context deadline. Changes span `gin.go` (Run/RunTLS lifecycle), `response_writer.go` (tracking active connections), and a new `shutdown.go` module for coordination.

### W2: Add OpenAPI 3.0 schema generation from routes

Implement automatic OpenAPI spec generation by inspecting registered routes, binding struct tags, and render types. Changes span `routergroup.go` (route metadata capture), `binding/` (struct-to-schema conversion), `render/` (response type inference), and a new `openapi/` package.

### W3: Implement rate limiting with pluggable backends

Add rate-limiting middleware supporting fixed-window and sliding-window algorithms. Implement in-memory and Redis backends. Configuration per route group with burst, limit, key extraction (IP, header, param). Changes span middleware creation, context key storage, a new `ratelimit/` package, and `routergroup.go` integration.

### W4: Add WebSocket support with upgrade handling

Implement WebSocket upgrade middleware that integrates with gin's context and middleware chain. Support route-level WebSocket handlers via `RouterGroup.WS(path, handler)`. Add connection lifecycle hooks (open, message, close, error). Changes span `routergroup.go`, `context.go`, `gin.go`, and a new `ws/` package.

### W5: Implement request validation framework with custom rules

Add a validation layer beyond struct tag binding. Support custom validation rules registered per field/type, cross-field validation, localized error messages, and validation groups. Changes span `binding/` (validator integration), `context.go` (validation helpers), `errors.go` (validation error type), and a new `validation/` package.

### W6: Add distributed tracing integration

Implement OpenTelemetry tracing middleware that creates spans for each request, propagates trace context through headers, annotates spans with route pattern and status. Add trace context to `Context`, integrate with `logger.go` for correlated logging, and update `recovery.go` to record panic spans.

### W7: Implement template rendering engine with layout support

Extend the HTML rendering system to support template layouts, partials, blocks, and template inheritance. Add a template registry with hot-reload in debug mode. Changes span `render/html.go` (layout rendering), `gin.go` (template configuration), `debug.go` (hot-reload), and a new `templates/` loader package.

### W8: Add server-sent events (SSE) streaming framework

Implement a structured SSE module with event broker pattern: clients subscribe to named channels, handlers publish events, and the framework manages connection lifecycle and heartbeats. Changes span `context.go` (SSEvent rework), `response_writer.go` (flush support), `routergroup.go` (SSE route registration), and a new `sse/` package.

### W9: Implement plugin system for engine extensions

Add a plugin registration API where plugins can hook into the engine lifecycle (startup, shutdown, route registration, request handling). Plugins can add middleware, modify configuration, and register routes. Changes span `gin.go` (plugin registry and lifecycle), `routergroup.go` (plugin route hooks), `context.go` (plugin context access), and a new `plugin/` package.

### W10: Add HTTP/2 server push support

Implement server push integration where handlers can trigger push promises for preloading resources. Add `Context.Push(path string, opts *PushOptions)` that uses the `http.Pusher` interface. Changes span `context.go` (push API), `response_writer.go` (Pusher interface assertion), `gin.go` (HTTP/2 configuration), and `render/html.go` (auto-push linked assets).
