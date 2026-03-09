# lostisland/faraday

| Field | Value |
|-------|-------|
| **URL** | https://github.com/lostisland/faraday |
| **License** | MIT |
| **Language** | Ruby |
| **Scale** | Small |
| **Category** | HTTP client abstraction |
| **Set** | Cutoff |
| **Commit** | `c6988a840738760fae1a40d653fa2ccd0da425b9` |

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

### N1: Fix Connection#dup not preserving proxy configuration

In `lib/faraday/connection.rb`, `Connection#dup` creates a new
connection instance passing `headers`, `params`, `builder`, `ssl`,
and `request` options, but omits the `proxy` option. When a
connection has a manually-set proxy (via `Connection#proxy=` or the
`:proxy` constructor option), duplicating it via `dup` silently drops
the proxy, causing the duplicate to fall back to the environment proxy
or no proxy. Fix `dup` to include the current `proxy` value in the
options hash passed to the new `Connection`.

### N2: Fix Headers#parse creating phantom headers from malformed lines

In `lib/faraday/utils/headers.rb`, `Headers#parse` processes raw HTTP
response header strings by splitting on `\r\n` and filtering with
`.reject { |p| p[0].nil? }`. A header line that contains no colon
(e.g., a server sending a malformed status continuation or a bare
token) produces a single-element array from `split(/:\s*/, 2)` where
`p[0]` is the line text and `p[1]` is `nil`. Since `p[0]` is not nil,
the line passes the filter and `add_parsed` is called with a nil
value, storing a phantom header entry with a nil value in the hash.
Fix `parse` to also reject entries where `p[1]` is nil (lines with no
colon separator).

### N3: Fix RaiseError middleware not supporting Range values in allowed_statuses

In `lib/faraday/response/raise_error.rb`, the `RaiseError` middleware
supports an `allowed_statuses` option to suppress errors for specific
status codes. The check `Array(options[:allowed_statuses]).include?(env[:status])`
only works for scalar integer values; passing a Range such as
`allowed_statuses: 400..499` does not match because `Array(400..499)`
wraps the range as a single element, and `[400..499].include?(404)`
returns `false`. Fix `on_complete` to use case-equality (`===`) when
checking each element of `allowed_statuses` so that both integer
values and Range values suppress error raising correctly.

### N4: Fix NestedParamsEncoder not omitting nil-valued parameters

In `lib/faraday/encoders/nested_params_encoder.rb`,
`NestedParamsEncoder.encode` converts parameter values to strings
via `to_s`. Ruby's `true.to_s` produces `"true"` and `false.to_s`
produces `"false"`, but some APIs expect `"1"`/`"0"` or have custom
boolean serialization. More critically, `nil` values are encoded as
bare keys without a value (e.g., `{a: nil}` encodes as `"a"` with no
`=`) rather than being omitted entirely, which is non-standard
behavior per HTTP conventions. Fix the encoder to omit `nil`-valued
parameters instead of encoding bare keys.

### N5: Fix ProxyOptions.from not normalizing schemeless URI strings in Hash input

In `lib/faraday/options/proxy_options.rb`, `ProxyOptions.from` handles
a String proxy value by prepending `"http://"` when the string does
not contain `"://"`, then parsing it as a URI. However, the
`Hash`/`Options` branch does not apply the same normalization to the
`:uri` key. Passing `{uri: 'proxy.example.com:8080'}` calls
`Utils.URI('proxy.example.com:8080')` directly, which interprets
`proxy.example.com` as the URI scheme instead of the host, resulting
in `nil` for both `host` and `port`. Fix the `when Hash, Options`
branch to normalize the `:uri` string value by prepending `"http://"`
when it does not contain `"://"`, matching the behavior of the
`when String` branch.

### N6: Fix Connection#initialize not validating URL scheme

In `lib/faraday/connection.rb`, the `Connection` initializer accepts
any string as the base URL without validating the scheme. Passing a
URL without a scheme (e.g., `"example.com/api"`) silently creates a
connection with `url_prefix` set to `"/example.com/api"` (treated as
an absolute path rather than a host), and attempts to build a URL from
this prefix later raise a `URI::BadURIError` because both URIs are
relative. Fix the initializer to validate that the URL, when provided,
has a recognized scheme (`http` or `https`) and raise `ArgumentError`
for URLs with a missing or unrecognized scheme.

### N7: Fix Json request middleware MIME_TYPE_REGEX not matching structured +json types

In `lib/faraday/request/json.rb`, `MIME_TYPE_REGEX =
%r{^application/(vnd\..+\+)?json$}` matches `application/json` and
`application/vnd.*+json` vendor types, but does not match other
structured-syntax `+json` media types such as
`application/problem+json`, `application/merge-patch+json`, or
`application/ld+json`. By contrast, the response `Json` middleware
defaults to `/\bjson$/`, which correctly handles all these types. When
a request is made with `Content-Type: application/problem+json`, the
request middleware's `process_request?` returns `false`, so the body
is not encoded and the content type is not set. Fix `MIME_TYPE_REGEX`
to also match media types with a non-vendor `+json` suffix, making it
consistent with the response middleware's approach.

### N8: Fix Test adapter stubs not checking URL scheme, allowing http/https mismatch

In `lib/faraday/adapter/test.rb`, `Stubs#new_stub` stores the host
extracted from the stub path via `Utils.URI(path).host` but does not
store the URI scheme. `Stub#matches?` checks `host.nil? || host ==
request_host`, so a stub registered for
`get("https://api.example.com/path")` also matches an HTTP request to
`http://api.example.com/path` because only the host is compared. Fix
`new_stub` and `Stub` to also extract and store the URI scheme, and
update `Stub#matches?` to require that the scheme matches when the
stub was registered with an absolute URL.

### N9: Fix Authorization middleware Bearer token not being refreshed for retried requests

In `lib/faraday/request/authorization.rb`, the `Authorization`
middleware supports a callable (`Proc`) for the token parameter
to allow dynamic token generation. However, when a request is
retried (e.g., by a retry middleware), the token proc is not
re-evaluated because the header is already set from the first
attempt. Fix the middleware to always re-evaluate the proc on
each `on_request` call, replacing any existing `Authorization`
header.

### N10: Fix FlatParamsEncoder nil values producing broken encode/decode round-trip

In `lib/faraday/encoders/flat_params_encoder.rb`,
`FlatParamsEncoder.encode` encodes `nil` values as bare keys without
a `=` sign (e.g., `{a: nil}` encodes as `"a"`). However,
`FlatParamsEncoder.decode` treats bare keys as `true` (boolean) via
`pair[1] = true if pair[1].nil?`. This creates a broken round-trip:
encoding `{a: nil}` and then decoding the result produces `{"a" =>
true}`, not `{"a" => nil}`. Fix `encode` to omit parameters with
`nil` values entirely, matching standard HTTP query string conventions
and ensuring the encoded output can be meaningfully decoded.

### N11: Fix docs/getting-started/quick-start.md misleading default adapter example

The `docs/getting-started/quick-start.md` guide includes a code block
showing `Faraday.default_adapter = :async_http # defaults to :net_http`.
The comment correctly states that `:net_http` is the default since
Faraday 2.x, but the example substitutes `:async_http` without noting
that this adapter requires the `faraday-async_http` gem (not bundled
with Faraday). The `README.md` includes this gem dependency note for
non-default adapters, but `quick-start.md` does not. Additionally,
`.rubocop_todo.yml` has `Metrics/MethodLength: Max: 33` (a broad global
override masking 28 long methods) instead of per-method suppressions
that would make the actual offenses visible. Fix `quick-start.md` to
add a note that non-default adapters require a separate gem, consistent
with `README.md`, and update `.rubocop_todo.yml` to suppress
`Metrics/MethodLength` per-file rather than globally.

## Medium

### M1: Implement request retry middleware with configurable backoff

Add `Faraday::Request::Retry` middleware that retries failed requests
with configurable retry count, backoff strategy (linear, exponential),
retryable status codes, and exception types. Requires a new
`lib/faraday/request/retry.rb` file, registration in
`middleware_registry.rb`, `RackBuilder` integration via
`builder.request :retry`, timeout-aware retry logic, idempotency
checking (skip retry for POST by default), spec coverage, a new
`docs/middleware/included/retry.md` documentation page describing
configuration options and backoff strategies, an entry in
`docs/middleware/index.md` linking to the retry docs, and an
`UPGRADING.md` note for users migrating from third-party retry gems.

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

### M11: Update UPGRADING.md, add v1-to-v2 migration guide, and improve PR template

`UPGRADING.md` covers the Faraday 2.0 adapter and middleware moves but
lacks a dedicated section documenting the `Faraday::Request#method` →
`#http_method` rename introduced in 2.0 with concrete before/after
code examples. The `docs/customization/` directory has guides on
connection and request options but no standalone migration guide for
custom middleware authors moving from the v1 API (the deprecated
`dependency` method, `Faraday::Response::Middleware` base class, and
old `register_middleware` array syntax) to the v2 API. The
`.github/PULL_REQUEST_TEMPLATE.md` Todos checklist only includes
`Tests` and `Documentation`, with no `UPGRADING.md` item to remind
contributors introducing breaking changes to update the upgrade guide.
Update `UPGRADING.md` with a `#http_method` rename section, add
`docs/customization/migration-v1-to-v2.md` covering the v1-to-v2
custom middleware API changes, add a `docs/_sidebar.md` entry for the
new guide, and add an `- [ ] UPGRADING.md` checklist item to
`.github/PULL_REQUEST_TEMPLATE.md`.

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

### W11: Overhaul docs/ for completeness, consistency, and CI hardening

The `docs/getting-started/errors.md` errors table omits
`Faraday::ParsingError` (raised by the JSON response middleware on
malformed bodies) and `Faraday::InitializationError` (raised for
invalid middleware options), both defined in `lib/faraday/error.rb`.
The `docs/middleware/included/raising-errors.md` example code uses
`e.response[:status]` but never mentions the `e.response_status`,
`e.response_headers`, and `e.response_body` convenience methods on
`Faraday::Error`, and also does not document the
`Faraday::UnprocessableEntityError` legacy alias for
`Faraday::UnprocessableContentError` (defined in `lib/faraday/error.rb`
and still in use by existing code). The `docs/adapters/custom/testing.md`
page does not mention the `#close` method that adapter implementations
should define for connection teardown (documented in
`docs/adapters/custom/index.md` but omitted from the testing guide).
The `docs/index.html` CDN script tags use `@latest` and unversioned
URLs for `docsify-darklight-theme`, `docsify-copy-code`, and
`docsify-edit-on-github`, making the docs site vulnerable to
unintentional breaking changes from upstream package updates. The
`.github/workflows/ci.yml` workflow runs on all pull requests with no
path filter, running the full Ruby test matrix for documentation-only
changes. The `.github/workflows/publish.yml` release workflow has no
link-validation step before publishing. Update
`docs/getting-started/errors.md` to add the missing error classes,
update `docs/middleware/included/raising-errors.md` to document the
convenience accessors and the `UnprocessableEntityError` alias, update
`docs/adapters/custom/testing.md` to mention `#close`, pin all CDN
versions in `docs/index.html`, add a `paths-ignore` filter for
docs-only paths to `.github/workflows/ci.yml`, and add a link-check
step to `.github/workflows/publish.yml`.
