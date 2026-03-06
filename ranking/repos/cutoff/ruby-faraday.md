# lostisland/faraday

| Field | Value |
|-------|-------|
| **URL** | https://github.com/lostisland/faraday |
| **License** | MIT |
| **Language** | Ruby |
| **Scale** | Small |
| **Category** | HTTP client abstraction |
| **Set** | Cutoff |
| **Commit** | `2de6beec29f571051b6e010a8ad745fb667445ca` |

## Why this repo

- **Well-structured**: Rack-inspired middleware architecture —
  `Connection` (manages URL prefix, headers, params, SSL, proxy,
  middleware stack), `RackBuilder` (ordered middleware stack with
  `use`, `request`, `response`, `adapter` DSL), `Middleware` (base
  class with `on_request`/`on_complete` hooks), `Adapter` (base class
  for HTTP backends), `Request`/`Response` (Rack-like env objects),
  `Env` (request/response state container), encoders
  (`NestedParamsEncoder`, `FlatParamsEncoder`), error hierarchy, and
  built-in middleware (`Authorization`, `UrlEncoded`, `Json`,
  `RaiseError`, `Logger`, `Instrumentation`).
- **Rich history**: 5.7K+ stars, 1.1K+ commits. The standard HTTP
  client abstraction for Ruby, used by Octokit, Stripe, and hundreds
  of gems. Issues span middleware ordering, encoding edge cases,
  proxy handling, and adapter compatibility.
- **Permissive**: MIT licensed.

## Structure overview

```
faraday/
├── faraday.gemspec                                    # Gem specification
├── lib/
│   ├── faraday.rb                                     # Module entry point — Faraday.new, default_adapter, default_connection
│   ├── faraday/
│   │   ├── version.rb                                 # VERSION constant
│   │   ├── methods.rb                                 # HTTP method constants (GET, POST, PUT, DELETE, etc.)
│   │   ├── connection.rb                              # Connection — URL prefix, headers, params, SSL, proxy, middleware stack
│   │   ├── rack_builder.rb                            # RackBuilder — middleware stack builder (use, request, response, adapter)
│   │   ├── middleware.rb                              # Middleware base — on_request, on_complete callbacks
│   │   ├── adapter.rb                                 # Adapter base — call(env), close, HTTP backend abstraction
│   │   ├── adapter_registry.rb                        # AdapterRegistry — lazy loading of adapter classes by symbol
│   │   ├── middleware_registry.rb                     # MiddlewareRegistry — lazy loading of middleware classes by symbol
│   │   ├── request.rb                                 # Request class — method, path, headers, body, options
│   │   ├── response.rb                                # Response class — status, headers, body, env
│   │   ├── parameters.rb                              # Parameter encoding module selection
│   │   ├── error.rb                                   # Error hierarchy — ClientError, ServerError, ConnectionFailed, TimeoutError
│   │   ├── options.rb                                 # Options base class with Struct-like hash access
│   │   ├── utils.rb                                   # URI parsing, deep_merge, header utilities
│   │   ├── net_http.rb                                # Built-in Net::HTTP adapter (default)
│   │   ├── logging/
│   │   │   └── formatter.rb                           # Logger formatter — request/response log formatting
│   │   ├── utils/
│   │   │   ├── headers.rb                             # Headers — case-insensitive hash for HTTP headers
│   │   │   └── params_hash.rb                         # ParamsHash — URI query parameter hash
│   │   ├── options/
│   │   │   ├── env.rb                                 # Env — request/response lifecycle state (url, method, body, status, etc.)
│   │   │   ├── request_options.rb                     # RequestOptions — timeout, open_timeout, params_encoder, etc.
│   │   │   ├── ssl_options.rb                         # SSLOptions — verify, ca_file, ca_path, client_cert, client_key
│   │   │   ├── proxy_options.rb                       # ProxyOptions — uri, user, password
│   │   │   └── connection_options.rb                  # ConnectionOptions — builder, url, params, headers, request, ssl, proxy
│   │   ├── request/
│   │   │   ├── authorization.rb                       # Authorization middleware — Basic, Bearer, Token auth
│   │   │   ├── url_encoded.rb                         # UrlEncoded middleware — form-encodes request body
│   │   │   ├── json.rb                                # Json request middleware — JSON-encodes request body
│   │   │   └── instrumentation.rb                     # Instrumentation middleware — ActiveSupport::Notifications
│   │   ├── response/
│   │   │   ├── raise_error.rb                         # RaiseError middleware — raises exceptions for 4xx/5xx
│   │   │   ├── json.rb                                # Json response middleware — parses JSON response body
│   │   │   └── logger.rb                              # Logger middleware — logs request/response via formatter
│   │   ├── encoders/
│   │   │   ├── nested_params_encoder.rb               # NestedParamsEncoder — Rails-style nested hash encoding
│   │   │   └── flat_params_encoder.rb                 # FlatParamsEncoder — flat key=value encoding
│   │   └── adapter/
│   │       └── test.rb                                # Test adapter — stub responses for testing
├── spec/                                              # RSpec test suite
│   ├── faraday/
│   │   ├── connection_spec.rb                         # Connection specs
│   │   ├── rack_builder_spec.rb                       # RackBuilder specs
│   │   ├── adapter_spec.rb                            # Adapter specs
│   │   ├── error_spec.rb                              # Error hierarchy specs
│   │   ├── utils_spec.rb                              # Utils specs
│   │   ├── adapter/
│   │   │   └── test_spec.rb                           # Test adapter specs
│   │   ├── request/                                   # Request middleware specs
│   │   ├── response/                                  # Response middleware specs
│   │   └── options/                                   # Options specs
│   └── support/                                       # Shared spec helpers
└── examples/                                          # Example usage files
```

## Scale indicators

- ~33 Ruby lib files, ~38 spec files
- ~9,264 lines total (lib ~4,200, spec ~5,000)
- Single gem with middleware plugin architecture
- Dependencies: minimal (only `ruby2_keywords` for compatibility)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix Connection#build_url not preserving fragment identifiers

In `lib/faraday/connection.rb`, the `build_url` method constructs a
full URL by merging `url_prefix` with the provided path and params.
URL fragments (e.g., `#section`) in the path argument are silently
stripped during URI parsing. Fix `build_url` to preserve fragment
identifiers when present in the path, passing them through to the
final URI.

### N2: Fix Headers#replace not maintaining case-insensitive key lookup for replaced headers

In `lib/faraday/utils/headers.rb`, the `Headers` class provides
case-insensitive key access. When `replace(new_hash)` is called to
bulk-replace all headers, the internal `@names` mapping (which tracks
the canonical case) is not rebuilt. Subsequent lookups for keys with
different casing fail. Fix `replace` to rebuild the `@names` hash
from the new headers.

### N3: Fix RaiseError middleware not including response headers in the exception

In `lib/faraday/response/raise_error.rb`, the `RaiseError` middleware
raises `Faraday::ClientError` or `Faraday::ServerError` with the
response body, but the response headers are not attached to the
exception object. Downstream error handlers cannot access rate-limit
headers (e.g., `Retry-After`, `X-RateLimit-Remaining`). Fix
`RaiseError` to include the response headers in the raised exception
via the existing `response` attribute hash.

### N4: Fix NestedParamsEncoder not encoding boolean values correctly

In `lib/faraday/encoders/nested_params_encoder.rb`,
`NestedParamsEncoder.encode` converts parameter values to strings
via `to_s`. Ruby's `true.to_s` produces `"true"` and `false.to_s`
produces `"false"`, but some APIs expect `"1"`/`"0"` or have custom
boolean serialization. More critically, `nil` values are encoded as
the string `""` when they should be omitted entirely per HTTP
conventions. Fix the encoder to omit `nil`-valued parameters instead
of encoding empty strings.

### N5: Fix ProxyOptions.from not parsing proxy credentials from URI string

In `lib/faraday/options/proxy_options.rb`, `ProxyOptions.from(string)`
parses a proxy URL string like `"http://user:pass@proxy:8080"` into
a `ProxyOptions` instance. The `user` and `password` fields are
extracted from the URI but not URI-decoded, so special characters
in credentials (e.g., `%40` for `@`) remain encoded. Fix `from` to
URI-decode `user` and `password` after extraction from the URI.

### N6: Fix Connection#initialize not validating URL scheme

In `lib/faraday/connection.rb`, the `Connection` initializer accepts
any string as the base URL without validating the scheme. Passing a
URL without a scheme (e.g., `"example.com/api"`) silently creates a
connection with `url_prefix` set to `"http:/example.com/api"` (note
single slash from the default `"http:/"`). Fix the initializer to
validate that the URL has a recognized scheme (`http` or `https`)
and raise `ArgumentError` for invalid URLs.

### N7: Fix Json request middleware not setting Content-Type when body is already a string

In `lib/faraday/request/json.rb`, the `Json` middleware checks if
the request body responds to `to_json` and serializes it. When the
body is already a JSON string (plain `String`), the middleware skips
serialization but also skips setting the `Content-Type` header to
`application/json`. Fix the middleware to always set the content
type header when the request body is a non-empty string and no
content type is already set.

### N8: Fix Test adapter stubs not matching requests with query parameters in the path

In `lib/faraday/adapter/test.rb`, the `Test` adapter matches stubs
by HTTP method and path. When a stub is registered with a path
containing query parameters (e.g., `get("/api?page=1")`) and the
actual request has the same query parameters in the `params` hash
instead of the path string, the stub does not match. Fix the stub
matching logic to normalize both stub and request paths by
extracting and comparing query parameters separately.

### N9: Fix Authorization middleware Bearer token not being refreshed for retried requests

In `lib/faraday/request/authorization.rb`, the `Authorization`
middleware supports a callable (`Proc`) for the token parameter
to allow dynamic token generation. However, when a request is
retried (e.g., by a retry middleware), the token proc is not
re-evaluated because the header is already set from the first
attempt. Fix the middleware to always re-evaluate the proc on
each `on_request` call, replacing any existing `Authorization`
header.

### N10: Fix FlatParamsEncoder.decode not handling duplicate keys

In `lib/faraday/encoders/flat_params_encoder.rb`,
`FlatParamsEncoder.decode` parses a query string into a hash. When
the query contains duplicate keys (e.g., `"a=1&a=2"`), only the
last value is retained. Fix `decode` to collect duplicate keys into
arrays, matching the behavior of `NestedParamsEncoder.decode`.

## Medium

### M1: Implement request retry middleware with configurable backoff

Add `Faraday::Request::Retry` middleware that retries failed requests
with configurable retry count, backoff strategy (linear, exponential),
retryable status codes, and exception types. Requires a new
`lib/faraday/request/retry.rb` file, registration in
`middleware_registry.rb`, `RackBuilder` integration via
`builder.request :retry`, timeout-aware retry logic, idempotency
checking (skip retry for POST by default), and spec coverage.

### M2: Add request/response caching middleware with ETag support

Implement `Faraday::Middleware::Cache` that caches GET responses
using ETags and `If-None-Match` headers. Requires a new
`lib/faraday/middleware/cache.rb` file, cache store abstraction
(in-memory `Hash` default), ETag extraction and conditional request
logic, `304 Not Modified` handling, cache-control header parsing,
middleware registration, and specs.

### M3: Implement multipart file upload middleware

Add `Faraday::Request::Multipart` middleware that handles multipart
form data encoding for file uploads. Requires a new
`lib/faraday/request/multipart.rb` file, `Faraday::FilePart` and
`Faraday::ParamPart` value classes, MIME boundary generation,
multipart body assembly from mixed hash parameters, content-type
header setting, streaming support for large files, and middleware
registration.

### M4: Add circuit breaker middleware for fault tolerance

Implement `Faraday::Middleware::CircuitBreaker` that tracks failure
rates and opens the circuit after a threshold, returning errors
immediately without making requests. Requires
`lib/faraday/middleware/circuit_breaker.rb`, state machine
(closed/open/half-open), configurable failure threshold and reset
timeout, thread-safe state tracking, custom fallback response
support, and specs.

### M5: Implement request signing middleware for HMAC authentication

Add `Faraday::Request::HmacAuth` middleware that signs requests
using HMAC-SHA256 with configurable key, secret, and signed headers.
Requires `lib/faraday/request/hmac_auth.rb`, canonical request
string construction, HMAC signature computation, header injection,
timestamp verification support, middleware registration, and specs.

### M6: Add response body streaming support with chunked transfer

Implement `Faraday::Response::Stream` middleware that yields response
body chunks to a block as they arrive instead of buffering the
entire response. Requires `lib/faraday/response/stream.rb`,
`on_data` callback integration with `Env`, adapter-level streaming
support in `adapter.rb`, `Connection#get` with block for streaming,
content-length vs. chunked handling, and specs.

### M7: Implement connection pooling with keep-alive support

Add `Faraday::ConnectionPool` that maintains a pool of persistent
connections for connection reuse. Requires
`lib/faraday/connection_pool.rb`, per-host connection tracking,
configurable pool size and idle timeout, thread-safe checkout/checkin,
integration with `Connection` via an option, adapter-level
persistent connection support, and specs.

### M8: Add OAuth 2.0 middleware with token refresh flow

Implement `Faraday::Request::OAuth2` middleware that adds Bearer
tokens and automatically refreshes expired tokens using the refresh
token grant. Requires `lib/faraday/request/oauth2.rb`, token storage
abstraction, expiry detection from `401` responses, automatic retry
after token refresh, thread-safe token refresh (single-flight),
middleware registration, and specs.

### M9: Implement request/response compression middleware

Add `Faraday::Middleware::Gzip` that compresses request bodies and
decompresses response bodies using gzip/deflate. Requires
`lib/faraday/middleware/gzip.rb`, `Accept-Encoding` header injection,
`Content-Encoding` header detection on responses, `Zlib` integration
for compression/decompression, streaming decompression support,
content-type preservation, and specs.

### M10: Add response body validation middleware with JSON Schema

Implement `Faraday::Response::Validate` middleware that validates
JSON responses against a schema. Requires
`lib/faraday/response/validate.rb`, JSON Schema loading from
files or inline, validation logic with error collection,
configurable behavior (raise vs. warn), schema caching by URL
pattern, middleware registration, and specs.

## Wide

### W1: Implement async/concurrent request support with Fiber scheduler

Add async request execution using Ruby 3.x Fiber scheduler: parallel
GET/POST operations, response collection, and connection sharing.
Changes span `lib/faraday/async.rb` for async connection wrapper,
`lib/faraday/async_response.rb` for concurrent response handling,
adapter-level fiber-aware I/O, `Connection#in_parallel` block with
fiber-based concurrency, response batch collection, error handling
across fibers, and comprehensive specs.

### W2: Implement comprehensive HTTP/2 support with multiplexing

Add HTTP/2 support with multiplexed requests, server push, and
stream prioritization. Changes span a new `lib/faraday/adapter/http2.rb`
adapter, HPACK header compression in `lib/faraday/http2/headers.rb`,
stream multiplexing in `lib/faraday/http2/multiplexer.rb`, server
push handling, connection-level flow control, upgrade from HTTP/1.1,
`Connection` options for HTTP/2 preferences, and specs with mock
HTTP/2 server.

### W3: Implement request tracing and observability integration

Add distributed tracing support: OpenTelemetry span creation per
request, trace context propagation (W3C Trace Context headers),
metrics collection (request count, duration histogram, error rate),
and structured logging. Changes span
`lib/faraday/middleware/tracing.rb`, `lib/faraday/middleware/metrics.rb`,
`lib/faraday/logging/structured_formatter.rb`, OpenTelemetry SDK
integration, configuration for exporters, `Connection`-level tracing
options, and specs.

### W4: Implement adapter test harness for adapter gem developers

Build a shared test suite that adapter gem developers can include
to verify their adapter implementation conforms to the Faraday
adapter contract. Changes span `lib/faraday/adapter/test_harness.rb`,
shared RSpec examples for all HTTP methods, streaming, timeouts,
SSL, proxy, encoding, error handling, and parallel requests.
Add a test HTTP server fixture, documentation for adapter authors,
and integration with CI.

### W5: Implement response mocking and recording for integration tests

Add VCR-style request recording and playback: record HTTP
interactions to YAML/JSON cassettes, replay them in tests, and
match by configurable criteria. Changes span
`lib/faraday/middleware/recorder.rb`, `lib/faraday/cassette.rb`
for interaction storage, `lib/faraday/matcher.rb` for request
matching (URL, method, headers, body), cassette file management,
`Connection`-level record/playback mode, sensitive data filtering,
and comprehensive specs.

### W6: Implement GraphQL client support with query validation

Add a GraphQL-aware layer: typed query/mutation builders, variable
injection, response parsing with error extraction, and introspection-based
query validation. Changes span `lib/faraday/graphql/client.rb`,
`lib/faraday/graphql/query.rb` for query building,
`lib/faraday/graphql/response.rb` for error-aware response parsing,
`lib/faraday/graphql/schema.rb` for introspection,
`lib/faraday/request/graphql.rb` middleware, and specs.

### W7: Implement WebSocket support via Faraday connection

Add WebSocket upgrade and bidirectional communication through the
Faraday connection abstraction. Changes span
`lib/faraday/websocket/connection.rb` for WebSocket lifecycle,
`lib/faraday/websocket/handler.rb` for message handling,
`lib/faraday/adapter/websocket.rb` adapter, frame encoding/decoding,
ping/pong heartbeat, `Connection#websocket(path)` entry point,
middleware support for WebSocket connections, and specs.

### W8: Implement rate limiting middleware with distributed state

Add rate limiting that respects API rate limits: token bucket
algorithm, response header parsing (`X-RateLimit-*`, `Retry-After`),
distributed state via Redis, and automatic request queuing.
Changes span `lib/faraday/middleware/rate_limiter.rb`,
`lib/faraday/rate_limit/token_bucket.rb`,
`lib/faraday/rate_limit/header_parser.rb`,
`lib/faraday/rate_limit/redis_store.rb`, request queue with
priority, configuration per-host, and specs.

### W9: Implement request/response transformation pipeline DSL

Add a declarative transformation DSL for request/response
manipulation: path templating, header injection, body transformation,
response mapping. Changes span `lib/faraday/transform/dsl.rb` for
the DSL builder, `lib/faraday/transform/path_template.rb` for
URI templates (RFC 6570), `lib/faraday/transform/body_mapper.rb`
for request/response body transformation,
`lib/faraday/transform/middleware.rb` adapter, `Connection` DSL
integration, and specs.

### W10: Implement multi-backend connection failover with health checking

Add automatic failover across multiple backend URLs with health
checking: round-robin, least-connections, and active health probes.
Changes span `lib/faraday/failover/connection.rb` for multi-backend
connection, `lib/faraday/failover/health_checker.rb` for periodic
health probes, `lib/faraday/failover/strategy.rb` for load balancing
strategies, `lib/faraday/failover/circuit.rb` for per-backend
circuit state, `Connection` configuration for backend list,
automatic retry on backend failure, and specs.
