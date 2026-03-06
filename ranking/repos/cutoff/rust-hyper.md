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

30 tasks (10 narrow, 10 medium, 10 wide).

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

### N3: Add reason phrase preservation for HTTP/1.1 client responses

The `ReasonPhrase` extension in `ext/h1_reason_phrase.rs` allows
setting custom reason phrases on server responses, but the HTTP/1.1
client parser in `proto/h1/role.rs` (`Client::parse()`) discards the
reason phrase from response status lines. Store the parsed reason
phrase in the response extensions via `ReasonPhrase::from_bytes()` so
client code can access non-standard reason phrases.

### N4: Fix H2 keep-alive ping not respecting graceful shutdown

The H2 ping mechanism in `proto/h2/ping.rs` continues sending
keep-alive pings after `graceful_shutdown()` is called on the
connection. During graceful shutdown, pings should stop and the
connection should drain existing streams without initiating new ping
cycles. Check the shutdown state in the ping timer's `poll()` method
and suppress pings when shutting down.

### N5: Add informational response callback for HTTP/1.1 client

The `ext/informational.rs` module handles 1xx informational responses
for the server side, but the HTTP/1.1 client connection in
`client/conn/http1.rs` silently discards `100 Continue` and other 1xx
responses during parsing. Add an `on_informational` callback to the
client `Builder` that is invoked with the status code and headers of
each 1xx response before the final response is returned.

### N6: Fix date header cache producing stale values across time zones

The `common/date.rs` module caches the formatted `Date` header value
and updates it once per second. However, the cache uses process-level
`Instant` for timing without accounting for system clock adjustments
(e.g., NTP jumps). If the system clock is adjusted backward, the
cached date can remain stale for an extended period. Use
`SystemTime::now()` for cache validation instead of relying solely
on `Instant` elapsed comparison.

### N7: Add max header count configuration to HTTP/1.1 server builder

The HTTP/1.1 server `Builder` in `server/conn/http1.rs` exposes
`max_headers()` to limit the number of headers parsed per request, but
the default is `None` (unlimited). Server instances receiving requests
with thousands of headers consume excessive memory in `httparse`. Set
a sensible default (e.g., 100) and document the option, matching the
behavior of the client-side builder where `max_headers` also defaults
to unlimited.

### N8: Fix Rewind I/O not correctly replaying partial reads

The `Rewind` reader in `common/io/rewind.rs` allows peeking at the
start of a stream and replaying it, but when a `poll_read` returns
fewer bytes than the rewound buffer contains, the remaining rewound
bytes are lost on the next read. Fix the `Rewind` implementation to
track the read position into the prefix buffer and continue serving
prefix bytes across multiple reads before delegating to the inner
reader.

### N9: Add Content-Length validation to incoming body

The `Incoming` body in `body/incoming.rs` receives data frames but
does not validate that the total bytes received match the declared
`Content-Length` header. If a sender sends fewer bytes than declared,
the body stream blocks indefinitely. Add content-length tracking that
returns an error when the stream ends with fewer bytes than expected,
using the length information from `body/length.rs`.

### N10: Fix Error::is_timeout returning false for H2 timeouts

The `Error::is_timeout()` method in `error.rs` checks for timeout
errors but only matches the internal `Kind::Timer` variant. H2
connection-level timeouts (keep-alive ping timeout, header read
timeout) produce errors with different internal kinds that
`is_timeout()` does not recognize. Extend the `is_timeout()` check to
also match H2-sourced timeout errors from the `h2` crate.

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
for early rejection with `413 Payload Too Large`.

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
