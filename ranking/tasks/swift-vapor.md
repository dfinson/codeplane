# Tasks — vapor/vapor

8 tasks (3 narrow, 3 medium, 2 wide) for the Swift server-side web framework.

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
