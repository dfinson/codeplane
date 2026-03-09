# hyperium/hyper

| Field | Value |
|-------|-------|
| **URL** | https://github.com/hyperium/hyper |
| **License** | MIT |
| **Language** | Rust |
| **Scale** | Large |
| **Category** | HTTP library |
| **Set** | Cutoff |
| **Commit** | `8ba900853b0f619b165e8530fc8c310bc13e056b` |

## Why this repo

- **Well-structured**: Clean module hierarchy — HTTP/1.1 protocol engine
  in `proto/h1/` (connection state machine in `conn.rs`, parser in
  `role.rs`, chunked decoder in `decode.rs`, encoder in `encode.rs`,
  buffered I/O in `io.rs`), HTTP/2 integration in `proto/h2/`,
  client connection abstractions in `client/conn/`, server connection
  abstractions in `server/conn/`, body types in `body/`, error types in
  `error.rs`, runtime traits in `rt/`, and FFI/C-API bindings in `ffi/`.
- **Rich history**: 5K+ commits, 14K+ stars. The foundational Rust HTTP
  library used by `reqwest`, `axum`, `warp`, and `tonic`. PRs cover
  protocol correctness, performance, and API safety.
- **Permissive**: MIT license.

## Structure overview

```
src/
├── lib.rs                     # Crate root — feature flags, pub mod declarations
├── error.rs                   # Error type with parse/user/canceled/closed variants
├── headers.rs                 # Header utility functions (content-length, transfer-encoding)
├── trace.rs                   # Internal tracing macros
├── upgrade.rs                 # HTTP upgrade (WebSocket etc.) — Upgraded, OnUpgrade, Parts
├── mock.rs                    # Mock I/O for testing
├── cfg.rs                     # Conditional compilation macros
├── body/
│   ├── mod.rs                 # Body module re-exports
│   ├── incoming.rs            # Incoming body stream type
│   └── length.rs              # Content-length tracking
├── client/
│   ├── mod.rs                 # Client module
│   ├── dispatch.rs            # Client request dispatch channel
│   └── conn/
│       ├── mod.rs             # Connection module re-exports
│       ├── http1.rs           # HTTP/1 client — SendRequest, Connection, Builder
│       └── http2.rs           # HTTP/2 client — SendRequest, Connection, Builder
├── server/
│   ├── mod.rs                 # Server module
│   └── conn/
│       ├── mod.rs             # Connection module re-exports
│       ├── http1.rs           # HTTP/1 server — Connection, Builder, Parts
│       └── http2.rs           # HTTP/2 server — Connection, Builder
├── proto/
│   ├── mod.rs                 # Protocol module
│   ├── h1/
│   │   ├── mod.rs             # H1 module — Encode, Decode re-exports
│   │   ├── conn.rs            # H1 connection state machine — Conn, State, Reading, Writing
│   │   ├── role.rs            # Client/Server role parsing — parse_headers, encode_headers
│   │   ├── decode.rs          # Transfer-encoding decoder — chunked, content-length, EOF
│   │   ├── encode.rs          # Transfer-encoding encoder — chunked, content-length
│   │   ├── io.rs              # Buffered I/O — Buffered, Cursor, WriteBuf
│   │   └── dispatch.rs        # H1 dispatch — Client/Server dispatcher
│   └── h2/
│       ├── mod.rs             # H2 module re-exports
│       ├── client.rs          # H2 client connection handling
│       ├── server.rs          # H2 server connection handling
│       ├── ping.rs            # H2 keep-alive ping management
│       └── upgrade.rs         # H2 extended CONNECT upgrades
├── common/
│   ├── mod.rs                 # Common utilities
│   ├── buf.rs                 # Buffer utilities (BufList)
│   ├── date.rs                # HTTP date formatting (cached)
│   ├── either.rs              # Either type
│   ├── future.rs              # Future utilities
│   ├── task.rs                # Task/waker utilities
│   ├── time.rs                # Timer utilities
│   ├── watch.rs               # Watch channel for state sharing
│   └── io/
│       ├── mod.rs             # I/O utilities
│       ├── compat.rs          # Tokio ↔ hyper I/O compatibility
│       └── rewind.rs          # Rewind reader (peek + replay)
├── ext/
│   ├── mod.rs                 # Extensions — Protocol, ReasonPhrase
│   ├── h1_reason_phrase.rs    # Custom HTTP/1.1 reason phrase
│   └── informational.rs       # 1xx informational response handling
├── ffi/
│   ├── mod.rs                 # C-API module
│   ├── body.rs                # C-API body operations
│   ├── client.rs              # C-API client operations
│   ├── error.rs               # C-API error handling
│   ├── http_types.rs          # C-API HTTP types
│   ├── io.rs                  # C-API I/O types
│   ├── macros.rs              # C-API helper macros
│   └── task.rs                # C-API task/executor
├── rt/
│   ├── mod.rs                 # Runtime traits
│   ├── bounds.rs              # Send/Sync bound helpers
│   ├── io.rs                  # Read/Write traits
│   └── timer.rs               # Timer/Sleep traits
└── service/
    ├── mod.rs                 # Service traits
    ├── http.rs                # HttpService trait
    ├── service.rs             # Service implementation
    └── util.rs                # Service utilities
```

## Scale indicators

- ~60 Rust source files
- ~21.5K lines of code
- Deep module hierarchy (3–4 levels)
- Key dependencies: `http`, `http-body`, `h2`, `httparse`, `tokio`

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Add connection-level byte counter to H1 Buffered I/O

The `Buffered` struct in `proto/h1/io.rs` wraps read/write I/O but
does not track total bytes read or written over the connection
lifetime. Add `bytes_read` and `bytes_written` `u64` counters to
`Buffered` that are incremented in the `poll_read_from_io()` and
`buffer()` / `flush()` methods, and expose them via public accessor
methods for connection metrics.

### N2: Fix chunked decoder not enforcing maximum chunk size

The chunked transfer-encoding decoder in `proto/h1/decode.rs` parses
chunk sizes from hexadecimal headers but does not enforce a maximum
chunk size. A malicious server can send an extremely large chunk size
value (e.g., `FFFFFFFFFFFFFFFF`), causing allocation of a huge buffer.
Add a configurable `max_chunk_size` limit to the decoder state machine
that returns a parse error when exceeded.

### N3: Fix reason phrase stored even when matching canonical phrase

The `ReasonPhrase` documentation in `ext/h1_reason_phrase.rs` states
that a `ReasonPhrase` extension is present on a client response only
"if the reason phrase is different from the canonical reason phrase for
the response's status code." However, the `Client::parse()` method in
`proto/h1/role.rs` stores the parsed reason phrase unconditionally
whenever `httparse` returns a non-empty reason — including when it
matches the canonical phrase (e.g., `200 OK`). Fix `Client::parse()`
to compare the parsed reason bytes against
`http::StatusCode::canonical_reason()` and only insert the
`ReasonPhrase` extension when the phrases differ.

### N4: Fix H2 keep-alive ping not respecting graceful shutdown

The H2 ping mechanism in `proto/h2/ping.rs` continues sending
keep-alive pings after `graceful_shutdown()` is called on the
connection. During graceful shutdown, pings should stop and the
connection should drain existing streams without initiating new ping
cycles. Check the shutdown state in the ping timer's `poll()` method
and suppress pings when shutting down.

### N5: Add connection-level informational response callback to HTTP/1.1 client Builder

The `ext/informational.rs` module provides `on_informational()` to
register a callback for 1xx responses on a per-request basis by
inserting it into request extensions. However, the HTTP/1.1 client
`Builder` in `client/conn/http1.rs` has no way to register a
connection-level default callback that fires for all 1xx informational
responses on every request sent over that connection. Add an
`on_informational` method to the client `Builder` (and its internal
`Opts` struct) that stores a shared `OnInformational` callback, and
integrate it into `proto/h1/conn.rs` so it is used as a fallback when
no per-request callback is present in the request extensions.

### N6: Fix date header cache not recovering from backward system clock adjustments

The `common/date.rs` module caches the formatted `Date` header value
and refreshes it when `SystemTime::now()` advances past `next_update`.
However, if the system clock is adjusted backward (e.g., by NTP), the
condition `now > self.next_update` remains false indefinitely, so the
cached date is never refreshed and the stale value is served for an
arbitrarily long time. Fix `CachedDate::check()` to also force a
refresh when `now` is significantly earlier than `next_update` (e.g.,
more than two seconds behind), indicating a backward clock jump, so
the cache recovers promptly after such adjustments.

### N7: Add max header count configuration to HTTP/1.1 server builder

The HTTP/1.1 server `Builder` in `server/conn/http1.rs` exposes
`max_headers()` to limit the number of headers parsed per request, but
the default is `None` (unlimited). Server instances receiving requests
with thousands of headers consume excessive memory in `httparse`. Set
a sensible default (e.g., 100) in the `Builder` and document the
option. Also add the new default value to `CHANGELOG.md` as a
behavior change.

### N8: Add peek() method to Rewind I/O for non-destructive prefix inspection

The `Rewind` reader in `common/io/rewind.rs` combines a prefix
`Bytes` buffer with an inner I/O type for peeked-and-replayed reads,
but provides no way to inspect the remaining prefix bytes without
consuming the `Rewind` via `into_inner()`. Code that needs to branch
on the prefix content (e.g., protocol detection) must reconstruct the
buffer or use `into_inner()` prematurely. Add a `pub(crate) fn
peek(&self) -> Option<&Bytes>` method that returns a shared reference
to the pending prefix bytes (if any) without taking ownership or
affecting subsequent reads.

### N9: Add Content-Length validation to incoming body

The `Incoming` body in `body/incoming.rs` receives data frames but
does not validate that the total bytes received match the declared
`Content-Length` header. If a sender sends fewer bytes than declared,
the body stream ends with `Poll::Ready(None)` without signaling an
error, silently delivering a truncated body to the caller. Add
content-length tracking that returns an error when the stream ends
with fewer bytes than expected, using the length information from
`body/length.rs`.

### N10: Fix Error::is_timeout missing doc comment and HeaderTimeout cfg inconsistency

The `Error::is_timeout()` method in `error.rs` has no `///` doc
comment explaining which error conditions it matches. The check for
`Kind::HeaderTimeout` is gated behind
`#[cfg(all(feature = "http1", feature = "server"))]`, but
`new_header_timeout()` creates a `Kind::HeaderTimeout` error without
any `TimedOut` source, so the fallback `find_source::<TimedOut>()` can
never detect it. This means that if feature flags change the cfg
evaluation, header-timeout errors become undetectable. Fix
`new_header_timeout()` to include a `TimedOut` cause so that the
`find_source::<TimedOut>()` fallback in `is_timeout()` reliably
detects header-timeout errors independent of feature flags, and remove
the now-redundant `#[cfg]` guard from the `HeaderTimeout` match arm.
Add a `///` doc comment to `is_timeout()` describing the matched
conditions.

## Medium

### M1: Implement connection pool for HTTP/1.1 client

The `client::conn::http1` module provides single-connection
abstractions (`SendRequest`, `Connection`) but no connection pooling.
Add a `Pool` type that manages a set of connections keyed by
`(scheme, authority)`, supports max connections per host, idle
connection timeout, and LIFO/FIFO dispatch. Integrate with the
`Builder` for pool configuration. Changes span a new
`client/pool.rs` module, `client/conn/http1.rs` for connection
lifecycle hooks, `client/dispatch.rs` for pooled dispatch, and
`client/mod.rs` for pool re-exports.

### M2: Add request/response body size limits

The body types in `body/` do not enforce size limits on incoming
data. Add configurable maximum body size to both client and server
connections. When the limit is exceeded, return an error through the
body stream. Integrate with `server/conn/http1.rs` and
`server/conn/http2.rs` builder options, the `Incoming` body in
`body/incoming.rs`, and the H1 dispatch in `proto/h1/dispatch.rs`
for early rejection with `413 Payload Too Large`. Also update
`Cargo.toml` to add the `body-size-limit` feature flag that gates
the size enforcement code, and document the feature in
`docs/ROADMAP.md` under the new features section.

### M3: Implement HTTP/1.1 pipelining support for the server

The H1 server dispatcher in `proto/h1/dispatch.rs` processes one
request at a time, waiting for the response before reading the next
request. Add pipelining support that reads multiple requests from the
connection while the first is being processed, queues them, and
sends responses in order. Requires changes to `proto/h1/conn.rs` for
connection state management, `proto/h1/dispatch.rs` for request
queuing, `server/conn/http1.rs` for the pipelining builder option,
and `proto/h1/io.rs` for read-ahead buffering.

### M4: Add HTTP CONNECT tunnel support for the client

The HTTP/1.1 client in `client/conn/http1.rs` does not support
`CONNECT` method for tunnel establishment (used by HTTP proxies).
Implement `SendRequest::send_connect()` that sends a CONNECT
request, waits for a `200` response, and returns the raw transport
for tunneled I/O. Requires changes to `client/conn/http1.rs` for
the CONNECT handshake, `proto/h1/role.rs` for CONNECT response
parsing, `proto/h1/conn.rs` for state transition to tunnel mode,
and `upgrade.rs` for tunnel-to-upgrade conversion.

### M5: Implement graceful shutdown with drain timeout for HTTP/2 server

The HTTP/2 server `Connection::graceful_shutdown()` in
`server/conn/http2.rs` sends a GOAWAY frame but provides no timeout
mechanism — the connection waits indefinitely for in-flight streams
to complete. Add a `drain_timeout` option to the `Builder` that
forces connection closure after the timeout expires. Requires changes
to `server/conn/http2.rs` for timeout integration, `proto/h2/server.rs`
for stream drain tracking, `common/time.rs` for timer utilities, and
`rt/timer.rs` for timer trait usage.

### M6: Add server-side request tracing with span propagation

Add tracing span creation for each phase of request handling:
connection accept, header parsing, body reading, service call, and
response writing. Propagate span context through the handler so
user code can create child spans. Integrate with the existing
`trace!` macros in `trace.rs`. Changes span `server/conn/http1.rs`
and `server/conn/http2.rs` for connection-level spans,
`proto/h1/dispatch.rs` for request-level spans, `proto/h1/role.rs`
for parse phase spans, and `trace.rs` for span creation utilities.

### M7: Implement HTTP/1.1 connection upgrade for the server

The `upgrade.rs` module defines `OnUpgrade` and `Upgraded` types
but the HTTP/1.1 server path in `server/conn/http1.rs` requires
`with_upgrades()` to enable upgrade support, which changes the
connection future type. Simplify upgrade support by detecting
`101 Switching Protocols` responses automatically in
`proto/h1/dispatch.rs`, transitioning the connection to pass-through
mode, and providing the upgraded I/O without requiring
`with_upgrades()`. Changes span `proto/h1/dispatch.rs`,
`proto/h1/conn.rs`, `server/conn/http1.rs`, and `upgrade.rs`.

### M8: Add write buffer management with backpressure signaling

The write buffering in `proto/h1/io.rs` uses a `WriteBuf` that grows
without bound when the writer cannot keep up. Add configurable high
and low water marks to `Buffered`. When the write buffer exceeds the
high water mark, signal backpressure to the service by returning
`Poll::Pending` from body polls. Resume when the buffer drains below
the low water mark. Changes span `proto/h1/io.rs` for water mark
logic, `proto/h1/conn.rs` for backpressure state, `proto/h1/encode.rs`
for encode-aware flushing, and `server/conn/http1.rs` for
configuration.

### M9: Implement keep-alive timeout for HTTP/1.1 server

The HTTP/1.1 server connection in `server/conn/http1.rs` has a
`keep_alive()` option but no idle timeout — a keep-alive connection
with no new requests stays open indefinitely. Add a
`keep_alive_timeout` builder option that closes the connection after
the specified duration of inactivity between requests. Changes span
`server/conn/http1.rs` for the builder option, `proto/h1/conn.rs`
for idle state timeout detection, `proto/h1/dispatch.rs` for timer
integration, and `common/time.rs` for timer utilities.

### M10: Add header size limit enforcement to HTTP/1.1 parser

The HTTP/1.1 parser in `proto/h1/role.rs` limits the number of
headers via `max_headers` but does not limit the total size of
header data (names + values). A request with a few headers
containing very large values can consume excessive memory. Add a
`max_header_size` option to both client and server builders that
limits the total byte size of all headers combined. Changes span
`proto/h1/role.rs` for size tracking during parsing,
`server/conn/http1.rs` and `client/conn/http1.rs` for builder
options, and `error.rs` for the new error variant.

## Wide

### W1: Implement a middleware/layer system for server connections

Hyper provides raw connection handling but no built-in middleware
system. Implement a `Layer` trait and combinator system for composing
request/response transformations. Include built-in layers for
request logging, response compression, CORS headers, and timeout.
Support both sync and async layers. Changes span a new `layer/`
module (trait definition, combinator types, built-in layers),
`server/conn/http1.rs` and `server/conn/http2.rs` for layer
integration, `service/` for layered service wrapping, `rt/bounds.rs`
for layer trait bounds, and `common/future.rs` for layer future
types.

### W2: Add HTTP/3 (QUIC) support

Implement HTTP/3 client and server connections using QUIC transport.
Add `client::conn::http3` and `server::conn::http3` modules mirroring
the HTTP/1 and HTTP/2 API patterns. Integrate with the `h3` crate
for QUIC framing and stream management. Support alt-svc advertisement,
connection migration, and 0-RTT. Changes span new
`client/conn/http3.rs` and `server/conn/http3.rs` modules,
`proto/h3/` for QUIC protocol integration, `body/` for QUIC body
streams, `lib.rs` for feature flags, `error.rs` for QUIC error
variants, and `rt/io.rs` for QUIC transport traits.

### W3: Implement connection multiplexing and load balancing

Add a client-side connection manager that multiplexes requests across
multiple connections to the same host and supports load balancing
strategies (round-robin, least-connections, random). Include connection
health monitoring, automatic reconnection, and request retry on
connection failure. Changes span a new `client/balance.rs` module,
`client/conn/http1.rs` and `client/conn/http2.rs` for connection
lifecycle hooks, `client/dispatch.rs` for multiplexed dispatch,
`client/mod.rs` for the balancer API, `error.rs` for retry errors,
and `common/watch.rs` for health state broadcasting.

### W4: Add comprehensive protocol conformance testing framework

Implement a testing framework for HTTP protocol conformance with
pre-built test cases for RFC 7230/7231/7540/9113 requirements.
Include request and response generators for edge cases (malformed
headers, invalid chunk encoding, oversized frames), connection state
verification, and conformance report generation. Changes span a new
`testing/` module, `mock.rs` for enhanced mock I/O, `proto/h1/` for
H1 conformance tests, `proto/h2/` for H2 conformance tests,
`client/` and `server/` for end-to-end test helpers, and
`error.rs` for conformance error classification.

### W5: Implement automatic protocol negotiation and upgrade

Add automatic HTTP version negotiation where a client connection
attempts HTTP/2 with `h2c` upgrade and falls back to HTTP/1.1 if the
server does not support it. Support ALPN-based negotiation for TLS
connections and `Upgrade: h2c` for cleartext. Include prior-knowledge
HTTP/2 and configurable protocol preferences. Changes span
`client/conn/mod.rs` for negotiation logic, `client/conn/http1.rs`
for upgrade initiation, `client/conn/http2.rs` for h2c handling,
`proto/h1/role.rs` for upgrade header parsing, `server/conn/mod.rs`
for server-side negotiation, `rt/io.rs` for TLS ALPN integration,
and `upgrade.rs` for protocol upgrade types.

### W6: Add comprehensive FFI/C-API coverage for server functionality

The `ffi/` module provides C-API bindings for client operations and
body handling but has limited server-side coverage. Extend the FFI
module with server connection creation, configuration, request
handling callbacks, response construction, and graceful shutdown.
Include memory safety documentation and example C code. Changes span
`ffi/mod.rs` for new server exports, a new `ffi/server.rs` module,
`ffi/http_types.rs` for request/response FFI types, `ffi/body.rs`
for server body handling, `ffi/io.rs` for server I/O types,
`ffi/task.rs` for server task management, and `ffi/error.rs` for
server error codes.

### W7: Implement HTTP caching layer for client connections

Add an HTTP cache that sits between the client application and the
connection, implementing RFC 7234 caching semantics. Support
`Cache-Control` directives (max-age, no-cache, no-store, private,
public), conditional requests with `ETag` and `Last-Modified`,
cache invalidation, and configurable storage backends (in-memory,
file-system). Changes span a new `client/cache.rs` module,
`client/conn/http1.rs` and `client/conn/http2.rs` for cache
integration, `headers.rs` for cache header parsing, `body/` for
cached body storage, `common/time.rs` for TTL management, and
`error.rs` for cache error variants.

### W8: Add request/response streaming transformation pipeline

Implement a streaming transformation system for modifying request
and response bodies in-flight without full buffering. Support
transformers for compression, decompression, encryption, content
rewriting, and custom user transformations. Include transformer
chaining with backpressure propagation. Changes span a new
`transform/` module (trait definition, built-in transformers),
`body/` for transformed body types, `proto/h1/encode.rs` and
`proto/h1/decode.rs` for transform integration, `client/conn/` for
client-side transforms, `server/conn/` for server-side transforms,
and `service/` for transform service wrappers.

### W9: Implement connection migration and resumption

Add support for connection migration (moving a connection to a
different transport) and session resumption (reconnecting with
cached state). For HTTP/2, support GOAWAY-triggered migration where
in-flight requests are retried on a new connection. For HTTP/1.1,
support transparent reconnection with request replay for idempotent
methods. Changes span `client/conn/http1.rs` and
`client/conn/http2.rs` for migration logic, `client/dispatch.rs`
for request replay, `proto/h1/conn.rs` for connection state
serialization, `proto/h2/client.rs` for GOAWAY handling,
`common/io/rewind.rs` for transport rebinding, `error.rs` for
migration errors, and a new `client/migrate.rs` module.

### W10: Add observability integration with metrics and health checks

Implement comprehensive observability for hyper connections: request
latency histograms, connection pool sizes, protocol error counters,
body transfer sizes, and health check endpoints. Support Prometheus
exposition format, OpenTelemetry integration, and custom metric
backends. Include connection health probes and readiness checks.
Changes span a new `metrics/` module (metric types, collectors),
`client/conn/` for client-side metrics, `server/conn/` for server-
side metrics, `proto/h1/` and `proto/h2/` for protocol-level metrics,
`body/` for transfer metrics, `error.rs` for error classification
metrics, and `common/time.rs` for latency measurement.

### N11: Fix docs/MSRV.md not documenting feature-gated MSRV differences

The `docs/MSRV.md` file states the project's minimum supported Rust
version but does not document that enabling optional features (such
as `http2` which depends on the `h2` crate, or `ffi` which requires
nightly for C-ABI exports) may require a newer Rust version than the
base MSRV declared in `Cargo.toml`. Audit all optional dependencies
in `Cargo.toml` for their `rust-version` requirements, and add a
feature-MSRV compatibility table to `docs/MSRV.md` listing each
feature flag and its effective MSRV. Also update `CONTRIBUTING.md`
to require contributors to check feature-MSRV compatibility when
adding new optional dependencies.

### M11: Update CI benchmark workflow and Cargo.toml feature documentation

The `.github/workflows/bench.yml` workflow runs on pushes to `master`
but does not run on pull requests, so performance regressions are not
caught before merging. Update `bench.yml` to also trigger on every PR
targeting `master`, archive benchmark results as CI artifacts, and
post a performance comparison comment against the base branch.
Update `Cargo.toml` to add inline documentation comments for each
feature flag in the `[features]` section explaining what it enables
and its stability status. Update `CHANGELOG.md` with a "Performance"
subsection template for tracking benchmark changes per release.

### W11: Overhaul project documentation and release configuration

Comprehensively update all non-code project files for the hyper 1.x
stable release series. Rewrite `docs/ROADMAP.md` to reflect the
post-1.0 stability guarantees, HTTP/3 timeline, and planned feature
additions. Update `docs/VISION.md` to describe the three-crate
ecosystem (hyper, hyper-util, hyper-tls) and separation of concerns
more explicitly. Revise `CONTRIBUTING.md` to add sections on the
review process, bugfix backporting policy, and security-sensitive
change procedures. Update `Cargo.toml` to reorganize features into
groups (`client`, `server`, `http1`, `http2`, `ffi`) with inline
documentation comments. Update `SECURITY.md` to add a supported
version matrix and a disclosure timeline section to the existing
reporting instructions. Update `.github/workflows/CI.yml` to add a
docs-deployment job that publishes rustdoc to GitHub Pages on each
push to `master`. Update `docs/TENETS.md` with correctness-first HTTP
implementation principles.
