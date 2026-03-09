# seanmonstar/reqwest

| Field | Value |
|-------|-------|
| **URL** | https://github.com/seanmonstar/reqwest |
| **License** | MIT OR Apache-2.0 |
| **Language** | Rust |
| **Scale** | Large |
| **Category** | HTTP client |
| **Set** | Cutoff |
| **Commit** | `fa74a8b835b2f194253cebdab8d049c0e5d23af3` |

## Why this repo

- **Well-structured**: Clear separation between async and blocking APIs,
  with dedicated modules for connection management, proxying, TLS,
  redirects, cookies, multipart uploads, and response decoding. Built
  on `hyper` + `tokio` with optional HTTP/3 (h3) support.
- **Rich history**: 9K+ stars, 2K+ commits. The most popular Rust HTTP
  client, used by thousands of crates and applications. PRs cover TLS
  backends, proxy authentication, connection pooling, streaming bodies,
  and cookie handling.
- **Permissive**: Dual-licensed MIT OR Apache-2.0.

## Structure overview

```
reqwest/
└── src/
    ├── lib.rs                 # Re-exports, top-level Client and get() convenience API
    ├── async_impl/
    │   ├── client.rs          # Async Client — builder pattern, connection pooling, middleware
    │   ├── request.rs         # Async Request — method, URL, headers, body construction
    │   ├── response.rs        # Async Response — status, headers, body streaming, json/text/bytes
    │   ├── body.rs            # Async Body — from bytes, stream, file, with content-length
    │   ├── multipart.rs       # Multipart form data — Part, Form, streaming encoder
    │   ├── h3_client/          # HTTP/3 client implementation via quinn
    │   │   ├── mod.rs          # H3 client entry point
    │   │   ├── connect.rs      # H3 connection setup
    │   │   ├── dns.rs          # H3-specific DNS
    │   │   └── pool.rs         # H3 connection pooling
    │   └── upgrade.rs         # Connection upgrade support (WebSocket handshake)
    ├── blocking/
    │   ├── client.rs          # Blocking Client — sync wrapper spawning a tokio runtime
    │   ├── request.rs         # Blocking Request — sync API mirroring async_impl
    │   ├── response.rs        # Blocking Response — sync body reading, copy_to, json
    │   ├── body.rs            # Blocking Body — from Read, bytes, with content-length
    │   └── multipart.rs       # Blocking multipart — sync Part and Form builders
    ├── connect.rs             # Connection management — Connector, HttpsConnector, connection pooling
    ├── proxy.rs               # Proxy support — HTTP/HTTPS/SOCKS5 proxies, no_proxy, authentication
    ├── redirect.rs            # Redirect policy — follow, limited, none, custom policy
    ├── tls.rs                 # TLS configuration — native-tls and rustls backends, certificates
    ├── dns/
    │   ├── resolve.rs         # DNS resolution trait and default resolver
    │   ├── gai.rs             # getaddrinfo-based resolver
    │   └── hickory.rs         # Hickory (trust-dns) async resolver
    ├── cookie.rs              # Cookie jar — cookie storage and automatic header management
    ├── error.rs               # Error type — URL, connect, timeout, status, decode errors
    ├── into_url.rs            # IntoUrl trait — string/Url conversion with validation
    ├── util.rs                # Internal utilities
    └── wasm/
        ├── client.rs          # WASM Client — fetch API backend for browser/worker targets
        ├── request.rs         # WASM Request builder
        ├── response.rs        # WASM Response wrapper
        ├── body.rs            # WASM Body — JsValue conversion
        └── multipart.rs       # WASM multipart support
```

## Scale indicators

- ~35 Rust source files
- ~15K lines of code
- Single crate with feature-gated modules (blocking, cookies, multipart, gzip, brotli, h3, rustls, native-tls)
- Key dependencies: hyper, tokio, http, url, h2, quinn (h3), native-tls/rustls

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix Response::json() not returning a typed error when Content-Type is not application/json

When calling `response.json::<T>()` on a response with
`Content-Type: text/html`, the method attempts JSON deserialization
anyway, producing a confusing serde parse error instead of a clear
content-type mismatch error. Fix `json()` in `async_impl/response.rs`
to check the `Content-Type` header and return a `Decode` error with
a descriptive message when the media type is not `application/json`
or a `+json` suffix type.

### N2: Fix blocking Client not forwarding timeout to connect phase

When `ClientBuilder::timeout(Duration::from_secs(5))` is set on the
blocking client, the timeout applies only to the overall request but
not to the TCP connect phase. A slow DNS + connect can exceed the
intended timeout. Fix `blocking/client.rs` to propagate the timeout
to the `connect_timeout` if no explicit connect timeout is set.

### N3: Fix proxy authentication not URL-encoding special characters in credentials

When proxy credentials contain special characters like `@` or `:` in
the password (e.g., `http://user:p@ss:word@proxy:8080`), the URL parser
in `proxy.rs` splits incorrectly at the embedded `@`. Fix the proxy
URL parsing to properly handle URL-encoded credentials and decode
them before sending the `Proxy-Authorization` header. Also update
the proxy configuration section in `README.md` to document the
required URL-encoding format for special characters in proxy
credentials.

### N4: Fix redirect policy unconditionally overwriting user-supplied Referer header on redirect

When `ClientBuilder::referer(true)` is set (the default) and a
redirect is followed, `TowerRedirectPolicy::on_request` in
`redirect.rs` always calls `req.headers_mut().insert(REFERER, v)`
which overwrites any `Referer` header the user explicitly set on
the original request via `RequestBuilder::header()`. Fix `on_request`
in `redirect.rs` to skip setting the auto-generated referer when a
`Referer` header is already present in the request headers, preserving
user-supplied values.

### N5: Fix redirect policy not preserving fragment from original URL

When a request to `http://example.com/page#section` receives a 301
redirect to `/new-page`, the fragment `#section` is dropped from the
final URL. Per RFC 7231 §7.1.2, the fragment should be inherited if
the redirect target has no fragment. Fix `redirect.rs` to preserve
the original fragment when the redirect URL lacks one.

### N6: Fix IntoUrl not preserving empty query strings

When `IntoUrl` in `into_url.rs` processes a URL string like
`http://example.com/?`, the trailing `?` indicating an empty query
string is stripped during URL parsing. This means
`request.url().query()` returns `None` instead of `Some("")`,
which can cause servers that distinguish between no query string and
an empty query string to behave differently. Fix `IntoUrl` to
preserve the distinction between absent and empty query strings.

### N7: Fix redirect policy not stripping sensitive headers on HTTPS-to-HTTP scheme downgrade

When a request with an `Authorization` or `Cookie` header is
redirected from HTTPS to HTTP on the same host,
`remove_sensitive_headers` in `redirect.rs` preserves them because
it only compares host and port (`next.host_str()` and
`next.port_or_known_default()`), not the URL scheme. The auth
credentials are then sent over an unencrypted connection. Fix
`remove_sensitive_headers` in `redirect.rs` to also detect scheme
downgrades (HTTPS → HTTP) and strip `Authorization`, `Cookie`,
and `Proxy-Authorization` headers when the redirect target uses
a less-secure scheme than the previous request.

### N8: Fix error type not including the HTTP method for request-phase errors

When a request fails during sending (connect error, timeout, etc.),
the error message from `error.rs` includes the URL via `Display` but
omits the HTTP method (GET, POST, etc.). When multiple requests to
the same URL with different methods are in flight, the error message
is ambiguous. Fix the `Error` type to store and display the HTTP
method for `Kind::Request` errors.

### N9: Fix blocking Response::copy_to not reporting bytes written on error

When `blocking::Response::copy_to(&mut writer)` encounters a write
error partway through streaming the response body, the error does not
include how many bytes were successfully written. Fix `copy_to` in
`blocking/response.rs` to wrap the IO error with a context struct
that includes the byte count, so callers can implement resume logic
or progress reporting on partial failures.

### N10: Fix WASM client not propagating timeout to the AbortController signal

When `RequestBuilder::timeout(Duration::from_secs(5))` is set on the
WASM client, the timeout is stored on the `Request` struct but the
`AbortController` in `wasm/client.rs` only calls `abort()` after the
timer fires — it does not use `AbortSignal.timeout()` which is more
efficient and avoids keeping a `setTimeout` handle alive. Fix the WASM
fetch path to use the native `AbortSignal.timeout()` API when available
and fall back to the manual timer approach otherwise.

## Medium

### M1: Implement request event hooks for lifecycle observation

Add `ClientBuilder::on_request(callback)` and
`ClientBuilder::on_response(callback)` that fire user-provided
callbacks at each stage of the request lifecycle: before DNS
resolution, after connect, after TLS handshake, before request send,
and after response headers are received. Each callback receives a
read-only view of the request/connection state (URL, method, remote
address, TLS version). Requires a hook registry in
`async_impl/client.rs`, event structs for each lifecycle phase,
connection metadata extraction from `connect.rs`, integration with
the blocking client in `blocking/client.rs`, and error handling when
callbacks fail.

### M2: Add request and response interceptors/middleware

Implement a middleware system: `ClientBuilder::with_middleware(mw)` where
middleware can inspect/modify requests before sending and responses after
receiving. Support ordering, short-circuiting (for caching), async
middleware, and access to error cases. Requires a `Middleware` trait, a
middleware chain executor in `async_impl/client.rs`, integration with
the blocking client, error propagation, and changes to the request
pipeline.

### M3: Implement connection pool metrics and diagnostics

Add `Client::pool_status()` returning metrics: active connections,
idle connections, pending requests per host, connection age histogram,
and TLS session reuse rate. Support a callback for pool events (connect,
reuse, close, timeout). Requires instrumentation in `connect.rs`, a
metrics struct, pool-state tracking, integration with hyper's connection
pool, and exposure through both async and blocking clients.

### M4: Add HTTP/3 Alt-Svc automatic upgrade

Implement automatic protocol upgrade from HTTP/2 to HTTP/3 when a
server advertises `Alt-Svc: h3=":443"`. Cache Alt-Svc records per
origin, support max-age expiry, and fall back to HTTP/2 on QUIC failure.
Requires Alt-Svc header parsing, a cache module, integration with
`h3_client.rs`, connection selection logic in `connect.rs`, and
configuration knobs on `ClientBuilder`. Also update `Cargo.toml` to
add the `http3` feature flag documentation in `[package.metadata.docs.rs]`
and ensure the `h3` feature properly gates the new Alt-Svc dependencies.

### M5: Implement streaming multipart upload with progress callbacks

Add progress tracking for multipart uploads:
`Form::progress(|bytes_sent, total| { ... })`. Support per-part progress,
overall progress, and upload speed calculation. Requires wrapping the
body stream in `async_impl/multipart.rs`, a progress callback trait,
integration with `Content-Length` computation, the blocking multipart
wrapper, and cancellation support via the callback return value.

### M6: Add certificate pinning with multiple pin strategies

Implement `ClientBuilder::pin_certificate(pin)` supporting: SHA-256
public key pins, certificate chain pins, and custom pin verification
callbacks. Support pin sets with backup pins, reporting violations
without blocking (report-only mode), and pin validation at connection
time. Requires changes to `tls.rs` for both native-tls and rustls
backends, a pin storage module, verification callbacks in `connect.rs`,
and error types for pin violations.

### M7: Implement response caching with HTTP cache semantics

Add an optional response cache following RFC 7234: respect
`Cache-Control`, `ETag`, `Last-Modified`, and `Vary` headers. Support
in-memory and on-disk cache backends, conditional requests with
`If-None-Match`/`If-Modified-Since`, cache invalidation, and a
`CacheControl` builder for programmatic cache directives. Requires a
cache module, integration with the request pipeline in
`async_impl/client.rs`, header parsing utilities, storage abstraction,
and blocking client integration.

### M8: Add per-request TLS configuration override

Implement `RequestBuilder::tls_config(config)` that lets individual
requests override the client-level TLS settings — for example, to pin
a specific certificate for one endpoint while using system roots for
others. Support overriding: minimum TLS version, accepted cipher
suites, client certificate, and server name indication (SNI). Requires
a per-request TLS config struct, integration with `connect.rs` for
connection-level override, changes to `tls.rs` to create per-request
TLS connectors, and fallback to the client-wide config when no override
is set.

### M9: Implement request body streaming from AsyncRead sources

Add `Body::from_reader(async_reader, content_length)` and
`Body::from_stream(stream)` for streaming request bodies from files,
pipes, or custom async readers without buffering the entire body in
memory. Support `Content-Length` when known, chunked transfer when
unknown, and progress callbacks. Requires changes to `async_impl/body.rs`,
integration with the request pipeline, the blocking body wrapper, and
multipart integration for file-part streaming.

### M10: Add DNS-over-HTTPS resolver backend

Implement a DoH resolver: `ClientBuilder::dns_resolver(DohResolver::new("https://dns.google/dns-query"))`.
Support RFC 8484 wire format, response caching with TTL, concurrent
A/AAAA queries, fallback to system resolver on failure, and bootstrap
resolution for the DoH server itself. Requires a new resolver in
`dns/`, integration with the resolver trait, HTTP client bootstrapping
to avoid circular dependency, and configuration on `ClientBuilder`.

## Wide

### W1: Implement a comprehensive tracing and diagnostics layer

Add OpenTelemetry-compatible instrumentation: trace spans for DNS
resolution, TCP connect, TLS handshake, request send, response headers,
and body streaming. Emit metrics for request duration histogram, bytes
sent/received, connection pool utilization, error rate by type, and
protocol version distribution. Support configurable exporters and
`tracing` crate integration. Changes span `connect.rs`, `async_impl/client.rs`,
`async_impl/response.rs`, `proxy.rs`, `tls.rs`, `dns/`, `error.rs`,
a new telemetry module, and the blocking client wrapper.

### W2: Implement HAR (HTTP Archive) recording and playback

Add `ClientBuilder::record_har(path)` that records all requests and
responses in HAR 1.2 format, including timing, headers, bodies,
cookies, and TLS details. Add `Client::from_har(path)` for test playback
that serves recorded responses for matching requests. Support request
matching by URL, method, headers, and body fuzzy matching. Changes span
`async_impl/client.rs`, `async_impl/request.rs`, `async_impl/response.rs`,
a new `har` module, cookie capture, timing infrastructure in `connect.rs`,
TLS metadata extraction, and blocking client integration.

### W3: Add WebSocket client support with full RFC 6455 compliance

Implement `Client::websocket(url)` returning a `WebSocket` connection
with: text/binary message sending and receiving, ping/pong handling,
close handshake, per-message compression (RFC 7692), connection upgrade
from an existing HTTP response, and automatic reconnection with backoff.
Changes span a new `websocket` module, upgrade handling in
`async_impl/upgrade.rs`, proxy tunneling for WSS, TLS configuration,
the blocking client for sync WebSocket, and `wasm/` for browser
WebSocket API.

### W4: Implement adaptive connection pooling with smart preconnect

Add intelligent connection management: preconnect to hosts based on
usage patterns, adaptive pool sizing per host, connection warm-up (TLS
handshake before first request), HTTP/2 stream multiplexing awareness,
idle connection health checks, and connection affinity for session
stickiness. Changes span `connect.rs`, `async_impl/client.rs`, a new
pool management module, DNS pre-resolution in `dns/`, TLS session
resumption in `tls.rs`, metrics collection, and the blocking client.

### W5: Add comprehensive request/response body transformation pipeline

Implement a body transformation pipeline:
`ClientBuilder::request_transform(compress_gzip).response_transform(decrypt_aes)`.
Support compression (gzip, brotli, zstd), encryption/decryption,
signing/verification, content-type transformation (JSON ↔ MessagePack),
and streaming transforms without buffering. Changes span
`async_impl/body.rs`, `async_impl/response.rs`, `async_impl/request.rs`,
a new transforms module, the multipart encoder, `blocking/body.rs`,
and the WASM body types.

### W6: Implement API client generation from OpenAPI specifications

Build a code-generation tool that reads an OpenAPI 3.x spec and
generates a type-safe reqwest-based API client with: strongly-typed
request/response structs, method-per-endpoint API, authentication
helpers, pagination support, rate limiting, and error mapping. Changes
span a new `reqwest-codegen` crate, template engine integration, type
mapping from JSON Schema, authentication module integration with
the existing client, generated test stubs, and CI integration for
spec-driven regeneration.

### W7: Add platform-native TLS backend abstraction with hot-swapping

Implement a unified TLS abstraction that supports runtime backend
selection between native-tls, rustls, and platform-specific APIs
(Security.framework, SChannel, OpenSSL). Support hot-swapping
certificates without client restart, per-host TLS configuration, client
certificate authentication with hardware token support, and TLS
debugging with key logging. Changes span `tls.rs`, `connect.rs`, a new
backend abstraction layer, certificate management utilities, platform
detection, feature flag reorganization, and integration tests per
backend.

### W8: Implement request coalescing and deduplication

Add request deduplication: when multiple concurrent requests target the
same URL+method+headers, coalesce them into a single network request
and fan out the response to all waiters. Support configurable key
extraction, TTL-based result caching, cache invalidation on
non-idempotent methods, and metrics for coalescing hit rate. Changes
span `async_impl/client.rs`, a new coalescing module, response cloning
in `async_impl/response.rs`, body buffering for fan-out, integration
with the caching layer, the blocking client, and error propagation
for coalesced failures.

### W9: Add comprehensive mock server for testing reqwest clients

Implement `reqwest_mock::MockServer` that runs an in-process HTTP
server for testing: register expected requests with matchers (URL, method,
headers, body), return configured responses, support request ordering
verification, simulate latency/errors/timeouts, stream response bodies,
and assert on unmatched requests. Changes span a new `reqwest-mock`
crate, a mock HTTP server built on hyper, request matching engine, a
builder API for expectations, TLS test certificate generation, integration
with the blocking client, and WASM-compatible test support.

### W10: Implement cross-platform HTTP client with unified WASM and native API

Unify the native and WASM clients behind a single type-erased
`UniversalClient` that provides identical API across platforms, with
platform-appropriate implementation. Support feature parity for: headers,
cookies, redirects, timeouts, JSON/form/multipart bodies, streaming
responses, and error types. Add compile-time platform detection and
runtime capability queries. Changes span `async_impl/`, `blocking/`,
`wasm/`, a new unified client module, conditional compilation
refactoring, trait-based abstraction for platform-specific behavior,
and a comprehensive cross-platform test suite.

### N11: Fix Cargo.toml feature documentation not listing all optional TLS backend combinations

The `Cargo.toml` `[features]` section defines multiple TLS backend
combinations (`rustls`, `native-tls`, `native-tls-vendored`,
`native-tls-no-alpn`, `native-tls-vendored-no-alpn`, `rustls-no-provider`)
but the `[package.metadata.docs.rs]` section uses `all-features = true`
without documenting which features are mutually exclusive. Add
comments to `Cargo.toml` clarifying which TLS features conflict,
update the feature table in `README.md` to document all TLS backend
options with a compatibility matrix, and add a `cfg` check in the
crate root that emits a `compile_error!` when incompatible TLS
features are enabled simultaneously.

### M11: Expand WASM CI coverage to test optional features and add missing integration tests

The CI workflow in `.github/workflows/ci.yml` runs `wasm-pack test`
and `cargo check --target wasm32-unknown-unknown` only with default
features, leaving the `multipart` and `stream` WASM feature
combinations untested. The `multipart` feature is gated in
`src/wasm/mod.rs` and has existing `#[wasm_bindgen_test]` tests in
`src/wasm/multipart.rs`, but they are never executed in CI. Add
`cargo check --target wasm32-unknown-unknown --features multipart`
and `--features stream,multipart` steps to the `wasm` job in
`.github/workflows/ci.yml`. Add a `wasm-pack test --headless --chrome
--features multipart` step so the existing WASM multipart tests run in
CI. Expand tests in `src/wasm/multipart.rs` to cover edge cases (empty
form, binary part, file-name with special characters). Add a
`CHANGELOG.md` entry documenting the improved WASM feature test
coverage.

### W11: Restructure Cargo.toml features and overhaul project documentation

Reorganize the `Cargo.toml` feature hierarchy to group TLS backends,
compression codecs, and protocol versions into feature groups with
clear documentation comments. Add a `[package.metadata.features]`
section listing recommended feature combinations for common use
cases (minimal, full, WASM, blocking-only). Rewrite `README.md` to
include a feature comparison table, migration guide from v0.12 to
v0.13, and platform support matrix (native, WASM, Android). Update
`CHANGELOG.md` to consolidate all v0.13.x entries with
cross-references to relevant issues. Add a `CONTRIBUTING.md` file
with build instructions for each target platform, feature-flag
testing requirements, and TLS backend testing procedures. Update
`.github/workflows/ci.yml` to add a feature-matrix job that tests
key feature combinations (`default`, `rustls`, `native-tls`,
`blocking+json`, `http3`, `wasm`) across platforms.
