# rack/rack

| Field | Value |
|-------|-------|
| **URL** | https://github.com/rack/rack |
| **License** | MIT |
| **Language** | Ruby |
| **Scale** | Small (focused library) |
| **Category** | HTTP server interface |
| **Set** | ranker-gate |
| **Commit** | `1fd28e537f7c8a11e28bae92d368a11e8dafaf35` |

## Why this repo

- **Single-purpose**: The minimal interface between Ruby web servers and web
  frameworks. Defines the request/response contract and provides a small set
  of middleware and utilities. One developer can easily understand the entire
  codebase.
- **Well-structured**: All source under `lib/rack/` with flat organization.
  Core handler, request/response objects, and built-in middleware (session,
  static, sendfile, etc.) are cleanly separated.
- **Rich history**: 4K+ commits, foundational to the Ruby web ecosystem
  (Rails, Sinatra, etc. all run on Rack). Steady maintenance with careful
  backward-compatibility considerations.
- **Permissive**: MIT license.

## Structure overview

```
lib/rack/
├── handler.rb           # Server handler interface
├── request.rb           # Request object
├── response.rb          # Response object
├── builder.rb           # Rack::Builder (config.ru DSL)
├── utils.rb             # URL encoding, header parsing
├── multipart/           # Multipart form parsing
├── session/             # Session middleware
│   ├── abstract/        # Abstract session store
│   ├── cookie.rb        # Cookie-based sessions
│   └── pool.rb          # In-memory session pool
├── auth/                # Basic/digest authentication
├── static.rb            # Static file serving
├── deflater.rb          # Response compression
├── etag.rb              # ETag generation
├── sendfile.rb          # X-Sendfile support
└── mock/                # Mock request/response for testing
```

## Scale indicators

- ~50 Ruby source files
- ~10K lines of code
- Flat structure (1-2 levels)
- Zero external dependencies

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `Rack::URLMap` not handling percent-encoded path segments during dispatch

`Rack::URLMap` in `urlmap.rb` matches incoming request paths against
configured mount paths using a regex built from `Regexp.quote(location)`.
This means a mount point like `/api/v2` won't match a request for
`/api%2Fv2` or `/api/v2%2Fextra` because the regex does not account for
percent-encoded forward slashes. Fix the `URLMap#call` dispatch to
normalize percent-encoded characters in the request path before matching.

### N2: Add `SameSite=None` default for cross-site cookie detection

When `Rack::Response#set_cookie` does not specify a `SameSite` attribute,
browsers default to `Lax`, which breaks cross-site scenarios. Add
automatic `SameSite=None; Secure` for cookies set on cross-origin
requests (detected via the `Sec-Fetch-Site` header). Add a configuration
option to disable this behavior.

### N3: Fix `Rack::ETag` not skipping ETag generation for `no-store` Cache-Control responses

`Rack::ETag` in `etag.rb` has a `skip_caching?` method that checks for
existing `etag` and `last-modified` headers but does not check the
`Cache-Control` directive. When a response includes `Cache-Control:
no-store`, an ETag is still generated and attached. Since `no-store`
semantics mean the response must not be cached, attaching an ETag is
contradictory. Fix `skip_caching?` to also return true when `no-store`
is present in the `Cache-Control` header.

### N4: Fix `Rack::Multipart` boundary detection failing on mixed-case Content-Type

`Rack::Multipart` extracts the boundary parameter from the `Content-Type`
header using a case-sensitive match, causing multipart parsing to fail
when clients send `Boundary=` instead of `boundary=`. Fix the boundary
extraction regex in `multipart/parser.rb` to be case-insensitive per
RFC 2046 Section 5.1.

### N5: Add `REQUEST_METHOD` validation in `Rack::Request`

`Rack::Request#request_method` returns whatever string the server places
in the env hash without validation. Invalid methods like empty strings
or methods containing whitespace can propagate into routing and logging.
Add validation in `Rack::Request` to reject malformed method strings
with a `Rack::InvalidRequestError` and return a 400 response.

### N6: Fix `Rack::MediaType.params` not handling quoted strings containing semicolons

`Rack::MediaType` in `media_type.rb` splits Content-Type headers on the
pattern `/[;,]/` to extract parameters. When a parameter value contains
a semicolon inside double quotes (e.g., `multipart/form-data;
boundary="foo;bar"`), the split incorrectly breaks the quoted string,
producing malformed parameter keys and values. Fix the `params` method
to respect double-quoted strings during splitting per RFC 2045
Section 5.1.

### N7: Add `Rack::Response#delete_cookie` support for partitioned cookies

The CHIPS proposal introduces a `Partitioned` attribute for cookies.
`Rack::Response#delete_cookie` does not include `Partitioned` in the
expiration cookie, so partitioned cookies are never actually cleared.
Update `delete_cookie` in `response.rb` to carry over the `Partitioned`
attribute when it was present in the original `Set-Cookie`.

### N8: Fix `Rack::Utils.best_q_match` returning wrong match for equal quality values

When multiple `Accept` entries have identical quality values,
`best_q_match` in `utils.rb` returns the last match instead of the
most specific one (per RFC 7231 Section 5.3.2 precedence rules). Fix
the tie-breaking logic to prefer entries with more specific media type
parameters and longer type strings.

### N9: Fix `Rack::Recursive` not restoring original env on forwarding error

`Rack::Recursive` in `recursive.rb` enables internal redirects via
`Rack::ForwardRequest`. When the forwarded request raises an exception,
the modified env (with changed `PATH_INFO`, `SCRIPT_NAME`, and
`REQUEST_METHOD`) is not restored to its original state. This corrupts
error-handling middleware that reads these values from the env hash.
Fix the recursive call handler to save and restore the original env
values when the forwarded request raises.

### N10: Fix `Rack::TempfileReaper` not cleaning up temp files on body iteration error

`Rack::TempfileReaper` in `tempfile_reaper.rb` registers temp files
for cleanup after the response body is closed via the `BodyProxy`
callback. If an exception is raised during body iteration before
`close` is called, the registered temp files are never cleaned up,
causing temp file leaks under error conditions. Fix the reaper to
also trigger cleanup when the body proxy detects an iteration error.

## Medium

### M1: Implement request body streaming with Rack 3 compatible API

Add a streaming request body interface that supports reading the request
body in chunks without buffering the entire body in memory. Implement
`rack.input` as an `Enumerable`-like interface with `#each_chunk` that
yields fixed-size chunks. Support back-pressure by pausing reading when
the consumer is slow. Add a `content_length_limit` configuration to
reject oversized requests early.

### M2: Add structured logging middleware

Implement `Rack::StructuredLogger` middleware that produces JSON-formatted
request logs with fields: timestamp, method, path, status, duration_ms,
request_id, user_agent, client_ip, and response_size. Support log
enrichment via a callback that adds custom fields (user_id, tenant, etc.).
Add correlation with upstream request IDs from `X-Request-Id` headers.

### M3: Implement ETag generation with content-aware hashing

Replace the current `Rack::ETag` middleware (which hashes the entire
response body) with content-aware ETag generation. For HTML responses,
hash only the meaningful content (strip whitespace changes). For JSON
responses, hash the semantically-sorted content (key order independent).
For binary responses, use the current body hash. Add weak ETag support
for semantically-equivalent responses.

### M4: Implement `Rack::RequestTimeout` middleware with per-path limits

Add a `Rack::RequestTimeout` middleware that wraps the downstream
`call` in a timeout, returning a 503 Service Unavailable response with
a `Retry-After` header when the request exceeds the configured
duration. Support a global default timeout and per-path overrides via
a hash map (e.g., longer timeouts for upload endpoints). Integrate
with `Rack::Events` to fire `on_error` callbacks on timeout. Add an
`x-timeout-limit` response header showing the effective limit. Use
`Rack::Utils.clock_time` for consistent time measurement.

### M5: Add multipart upload progress tracking

Extend `Rack::Multipart::Parser` to support upload progress callbacks.
Add a `rack.multipart.progress` env key that accepts a callable
receiving `(bytes_received, content_length)` during parsing. Implement
a companion `Rack::UploadProgress` middleware that stores progress
per upload (keyed by a client-provided upload ID) in a thread-safe
store and exposes a JSON endpoint for polling progress status.

### M6: Implement `Rack::HostAuthorization` middleware for DNS rebinding protection

Add middleware that validates the `Host` header against a configurable
allowlist of permitted hostnames and IP addresses. Support wildcard
subdomains (`.example.com`), regular expression patterns, and a
configurable response for blocked requests (default 403). Parse and
validate both the `Host` header and the `X-Forwarded-Host` header.
Log blocked requests with the offending host value for monitoring.

### M7: Add `Rack::Builder` support for conditional middleware insertion

Extend `Rack::Builder` DSL in `builder.rb` to support `use_if` and
`use_unless` directives that conditionally insert middleware based on
a predicate. The predicate receives the env hash at request time and
the middleware is only invoked when the condition is met. Support
both block and lambda predicates. Ensure conditional middleware
preserves the middleware stack order and works correctly with `map`
blocks.

### M8: Implement request body validation middleware

Add `Rack::BodyValidator` middleware that validates request bodies
against declared schemas. Support JSON Schema validation for
`application/json` bodies and parameter constraints for form-encoded
bodies. Return 422 Unprocessable Entity with structured error details
when validation fails. Allow per-path schema configuration via a
block DSL. Skip validation for GET, HEAD, and OPTIONS requests.

### M9: Add response body streaming hooks to `Rack::Events`

`Rack::Events` in `events.rb` provides `on_start`, `on_commit`,
`on_send`, `on_finish`, and `on_error` lifecycle hooks. However, for
streaming responses the `on_send` event fires only once when body
iteration begins, providing no visibility into individual chunks. Add
`on_body_chunk(request, response, chunk)` and `on_body_complete`
callbacks that fire during body iteration. Support filtering by
content type to avoid overhead on non-streaming responses. Track
chunk count and cumulative byte size on the response object for
monitoring. Update the `EventedBodyProxy` in `events.rb` to invoke
the new hooks.

### M10: Implement `Rack::MockRequest` support for streaming responses

`Rack::MockRequest` in `mock/request.rb` buffers the entire response
body before returning, making it impossible to test streaming endpoints
or Server-Sent Events. Add `#stream` mode that returns an
`Enumerator`-based response where each chunk can be consumed
individually. Support timeout configuration for slow streams. Add
assertion helpers for verifying chunk order, timing, and content type
in streaming responses.

## Wide

### W1: Implement HTTP/2 support in Rack's interface

Extend the Rack specification to support HTTP/2 features: server push
(via a `rack.push_promise` lambda), stream prioritization, and
multiplexed responses. The `rack.response` should support a streaming
body that can send headers and data frames independently. Add a
compatibility layer so existing Rack 2/3 apps work unchanged on
HTTP/2 servers. Update the mock request/response for testing HTTP/2.

### W2: Add comprehensive security middleware suite

Implement a suite of security middleware as a cohesive package:
`Rack::ContentSecurityPolicy` (CSP headers with nonce generation),
`Rack::PermissionsPolicy` (Permissions-Policy headers),
`Rack::CORSPolicy` (with preflight caching),
`Rack::RateLimiter` (configurable rate limiting),
and `Rack::RequestSanitizer` (input sanitization with configurable
rules). Each middleware should be usable independently or as a bundle
via `Rack::SecureHeaders` that applies sensible defaults.

### W3: Add middleware profiling and instrumentation framework

Implement `Rack::Instrumentation` that wraps every middleware in the
stack with timing and execution tracking. Record per-middleware wall
time, allocations, and call count. Expose metrics via a mountable
Rack app at a configurable path (`/_rack/profile`). Support
ActiveSupport::Notifications-compatible event publishing so external
APM tools can subscribe. Add a `Rack::Instrumentation::Tracer` that
generates request-scoped waterfall timelines showing middleware
execution order and duration.

### W4: Implement reloadable middleware stack for development mode

Add `Rack::ReloadableBuilder` that watches `config.ru` and middleware
source files for changes, then rebuilds the middleware stack without
restarting the server process. Use `Listen` gem integration (optional
dependency) or polling-based file watching. Maintain in-flight request
safety by draining active requests before swapping the stack. Add
a development overlay page showing the current middleware stack order
and configuration, accessible at `/_rack/middleware`.

### W5: Add comprehensive multipart streaming parser with disk spooling

Rewrite `Rack::Multipart::Parser` to support true streaming parsing
that never buffers more than a configurable amount of data in memory.
Spool file uploads to temporary files after a size threshold. Support
resumable uploads via `Content-Range` on individual parts. Add
per-part callbacks for headers, data chunks, and completion. Implement
configurable per-part and total size limits with early rejection.
Ensure the parser handles malformed boundaries and truncated uploads
gracefully with descriptive error messages.

### W6: Implement a test harness for middleware composition

Add `Rack::Testing` module with a DSL for writing integration tests
against composed middleware stacks. Support building isolated test
stacks with `Rack::Testing.stack { use Middleware; run app }`. Add
request factories for common scenarios (JSON API, form submission,
multipart upload, WebSocket upgrade). Implement response matchers
for status codes, headers, body content, and cookie state. Add
session inspection helpers and a request recorder that captures
all requests passing through the stack for assertion.

### W7: Add WebSocket handshake and framing support

Implement `Rack::WebSocket` module that handles the HTTP/1.1 upgrade
handshake for WebSocket connections. Parse the `Upgrade: websocket`
and `Sec-WebSocket-Key` headers, compute the accept hash per RFC 6455,
and return the 101 Switching Protocols response. Add a frame parser
and serializer supporting text, binary, ping, pong, and close frames.
Implement `Rack::WebSocket::Handler` as a base class with `on_open`,
`on_message`, `on_close` callbacks. Add mock WebSocket client for
testing in `mock/websocket.rb`.

### W8: Implement content negotiation middleware with variant support

Add `Rack::ContentNegotiation` middleware that performs full HTTP
content negotiation per RFC 7231. Support `Accept` (media type),
`Accept-Language`, `Accept-Encoding`, and `Accept-Charset` headers.
Allow applications to register response variants (JSON, XML, HTML,
MessagePack) with quality factors. Add a transparent variant
selection mechanism that routes to the best representation. Implement
`Vary` header management to ensure correct caching. Support
`406 Not Acceptable` responses with a list of available variants.

### W9: Add request replay and recording middleware for debugging

Implement `Rack::Recorder` middleware that captures full request and
response cycles (method, path, headers, body, timing) to a
configurable store (file, Redis, or in-memory). Add `Rack::Replayer`
that reads recorded sessions and replays them against the application,
comparing responses for regression detection. Support request
anonymization (strip authorization headers, mask PII fields). Add
filtering by path pattern, status code, and time range. Implement
a mountable Rack app for browsing and replaying recorded requests
via a web interface.

### W10: Implement cascading error handling with rescue middleware

Add `Rack::Rescue` middleware that catches exceptions raised by
downstream middleware and applications, mapping them to appropriate
HTTP responses. Support a registry of exception-to-status mappings
(e.g., `ArgumentError` to 400, `NotFoundError` to 404). Render
error responses in the format matching the request `Accept` header
(HTML with stack trace in development, JSON error object in API
mode). Add error callback hooks for external reporting. Implement
`Rack::Rescue::ShowExceptions` for development that renders an
interactive debugger page with source context, request details,
and environment inspection. Support error grouping and rate-limited
logging to prevent log flooding from repeated errors.
