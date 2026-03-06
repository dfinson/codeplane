# square/okhttp

| Field | Value |
|-------|-------|
| **URL** | https://github.com/square/okhttp |
| **License** | Apache-2.0 |
| **Language** | Java (Kotlin) |
| **Scale** | Medium (multi-module project) |
| **Category** | HTTP client |
| **Set** | ranker-gate |
| **Commit** | `4f843e44998e52caf60b36a7abd72da421c326d1` |

## Why this repo

- **Multi-module with clear boundaries**: Connection pooling, HTTP/2 framing,
  interceptor chain, TLS/certificate pinning, cache, cookie handling, DNS
  resolver, WebSocket support — each a distinct subsystem requiring navigation
  between them.
- **Well-structured**: Core library under `okhttp/` with clear package split.
  Interceptors, connection management, and protocol handling are cleanly
  separated. Supplementary modules (logging, TLS, mock server) in separate
  directories.
- **Rich history**: 6K+ commits, maintained by Square with strong review
  culture. Migration from Java to Kotlin provides variety in commit patterns.
- **Permissive**: Apache-2.0.

## Structure overview

```
okhttp/src/commonJvmAndroid/kotlin/okhttp3/
├── OkHttpClient.kt       # Main client entry point
├── Call.kt                # HTTP call abstraction
├── Request.kt / Response.kt  # Request/response models
├── internal/
│   ├── connection/        # Connection pool, route selection
│   │   ├── RealConnectionPool.kt
│   │   ├── RouteSelector.kt
│   │   └── Exchange.kt
│   ├── http/              # HTTP protocol handling
│   │   ├── RealInterceptorChain.kt
│   │   ├── RetryAndFollowUpInterceptor.kt
│   │   └── CallServerInterceptor.kt
│   ├── http2/             # HTTP/2 framing
│   ├── cache/             # Disk cache
│   ├── tls/               # TLS, certificate pinning
│   ├── ws/                # WebSocket implementation
│   └── platform/          # Platform-specific (JDK, Android)
├── Interceptor.kt         # Interceptor interface
├── Cache.kt               # Cache API
└── CookieJar.kt           # Cookie handling
```

## Scale indicators

- ~200 Kotlin/Java source files
- ~50K lines of code
- Clear subsystem boundaries with interceptor-chain architecture
- 3-4 levels of package nesting

## Notes

- Primarily Kotlin since OkHttp 4.x, with some Java interop code remaining.
  Codeplane indexes Kotlin as JavaScript family — verify grammar support.
  If Kotlin indexing is insufficient, substitute with `square/retrofit`
  (Apache-2.0, Java-primary) or `apache/commons-lang` (Apache-2.0).

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix connection pool eviction race under high concurrency

Under high concurrency, idle connections are sometimes evicted from the
pool while another thread is in the process of acquiring them. This
causes a `ConnectionShutdownException` on the acquiring thread. Fix the
eviction logic to check for in-flight acquisitions before closing
idle connections.

### N2: Add `DNS-over-HTTPS` cache TTL support

The built-in `DnsOverHttps` client does not cache DNS responses. Each
request performs a fresh DoH lookup even when the previous response
included a TTL. Implement a response cache in the DoH client that
respects the DNS record TTL from the response. Add a configuration
option for maximum cache size.

### N3: Fix `EventListener.callEnd` not called on cancelled calls

When a `Call` is cancelled via `call.cancel()`, the `EventListener`
receives `callFailed` but not `callEnd`. The contract says `callEnd`
is always the last event. Fix the cancellation path to emit `callEnd`
after `callFailed` when the call is cancelled.

### N4: Fix certificate pinning bypass on redirects to different hosts

When a request is redirected from a pinned host to a different host
that also has pins configured, the certificate pins for the original
host are applied instead of the redirect target's pins. Fix the
`CertificatePinner` lookup in the redirect-following path to resolve
pins against the current request URL rather than the original URL.

### N5: Correct `Content-Length` mismatch on retried POST requests

When a POST request with a fixed-length `RequestBody` is retried after
a connection failure, the `Content-Length` header from the original
attempt is reused even if the body source has been partially consumed.
Fix the retry path to re-read the body and recompute the content length
before sending the retried request.

### N6: Fix `RouteSelector` failing to skip failed routes on proxy fallback

When a proxy connection fails and the `RouteSelector` falls back to
the next proxy in the list, it does not record the failed route. This
causes the same broken route to be retried on subsequent requests. Fix
the route failure tracking so that failed proxy routes are properly
excluded during proxy fallback selection.

### N7: Handle `HTTP 421 Misdirected Request` in HTTP/2 coalesced connections

When an HTTP/2 connection is coalesced across multiple hostnames and
the server responds with a 421 status, OkHttp does not retry the
request on a fresh, non-coalesced connection. Add handling for the
421 response code that opens a dedicated connection to the target
host and replays the request.

### N8: Fix stale `Exchange` reference after `CallServerInterceptor` timeout

When a request times out inside the `CallServerInterceptor`, the
associated `Exchange` object retains a reference to the now-dead
connection. Subsequent attempts to read the response body throw a
misleading `IllegalStateException` instead of a timeout exception.
Fix the exchange lifecycle so the connection is properly detached
on timeout.

### N9: Correct `Cache-Control: only-if-cached` returning `null` instead of 504

The HTTP specification says a request with `Cache-Control: only-if-cached`
must return a 504 Gateway Timeout when no cached response is available.
OkHttp currently returns a null response, which causes a
`NullPointerException` downstream. Fix the cache interceptor to
return a synthetic 504 response in this case.

### N10: Fix `WebSocket` ping/pong frame ordering under back-pressure

When the write buffer is full and the WebSocket writer is blocked,
ping frames are queued behind data frames. If the pong deadline
expires before the queued ping is sent, the connection is incorrectly
closed as unresponsive. Fix the frame writer to prioritize ping/pong
control frames over queued data frames.

## Medium

### M1: Implement automatic retry with exponential backoff

Add configurable automatic retry for failed requests. Support
retry on connection failures, 5xx responses, and configurable
status codes. Use exponential backoff with jitter. Add a
`RetryPolicy` interface with a default implementation, configurable
via `OkHttpClient.Builder.retryPolicy()`. Respect `Retry-After`
headers. Track retry count in the response for observability.

### M2: Add HTTP/3 (QUIC) transport support

Implement an HTTP/3 transport using QUIC. Add connection migration
support (handling network changes without reconnection), 0-RTT
connection resumption, and Alt-Svc header parsing for HTTP/3
discovery. The transport should be selectable via the client builder
and fall back to HTTP/2 when QUIC is unavailable.

### M3: Implement response body decompression pipeline

Currently OkHttp handles gzip decompression as a special case in
the `BridgeInterceptor`. Refactor to support a pluggable decompression
pipeline: gzip, deflate, brotli, and zstd. Each decompressor should
implement a common interface. Content-Encoding negotiation should
advertise all available decompressors in the Accept-Encoding header.
Add content verification (checksum) after decompression.

### M4: Add mutual TLS (client certificate) rotation without restart

OkHttp currently requires rebuilding the `OkHttpClient` to change
client certificates used for mutual TLS. Implement a `ClientCertificateSelector`
interface that is consulted on each TLS handshake, allowing the
application to supply different client certificates per host or rotate
certificates at runtime. Integrate with the `HandshakeCertificates`
builder and ensure that the connection pool evicts connections whose
client certificate no longer matches the selector's current choice.

### M5: Implement conditional request support in the cache interceptor

Add automatic conditional request handling to the cache layer. When a
cached response has an `ETag` or `Last-Modified` header, subsequent
requests should automatically include `If-None-Match` or
`If-Modified-Since`. On a 304 response, merge the cached body with the
new headers and update the cache entry. Track cache revalidation
statistics through the `EventListener`. Handle the edge case of the
server returning a full 200 response to a conditional request.

### M6: Add per-host connection pool configuration

The connection pool currently applies a single idle connection limit
and keep-alive duration to all hosts. Add per-host pool configuration
via `ConnectionPool.Builder` that allows different limits for specific
hosts — for example, more idle connections for high-traffic API
endpoints and fewer for low-traffic ones. Include support for host
pattern matching (exact and wildcard) and a default fallback policy.
Ensure eviction respects per-host limits independently.

### M7: Implement transparent request queuing with priority support

Add a request queue that limits the number of concurrent in-flight
requests per host and globally. Excess requests should be queued and
dispatched as capacity becomes available. Support priority levels
(high, normal, low) so that important requests skip ahead in the queue.
Integrate with the `Dispatcher` and expose queue depth and wait time
through the `EventListener`. Queued calls must be cancellable.

### M8: Add network interceptor for automatic OAuth token refresh

Implement an `Authenticator`-adjacent mechanism that detects expired
OAuth tokens from 401 responses, acquires a new access token using a
refresh token, and retries the original request with the updated
`Authorization` header. Serialize concurrent token refreshes so only
one refresh is in flight at a time while other 401-receiving calls
wait. Expose token lifecycle events through the `EventListener` and
limit the number of refresh retries to prevent loops.

### M9: Implement connection preconnect and speculative warming

Add a `preconnect(url, count)` API to `OkHttpClient` that proactively
opens TCP connections and performs TLS handshakes to a host before any
request is made. Support DNS pre-resolution as an option. The
preconnected sockets should be placed in the connection pool and used
by subsequent requests. Integrate with the `EventListener` to report
preconnect outcomes and ensure that preconnected connections respect
the pool's idle timeout and eviction policies.

### M10: Add response caching for authenticated requests via `Cache-Control` extensions

By default the cache does not store responses to requests that carry an
`Authorization` header. Implement support for the `Cache-Control`
response directives `must-revalidate`, `public`, and `s-maxage` that
explicitly allow caching of authenticated responses. Add a builder
option to opt in to this behavior. Ensure cached authenticated
responses are properly revalidated and that the cache key includes
enough request metadata to prevent serving one user's cached response
to another.

## Wide

### W1: Add comprehensive request/response logging interceptor

Implement a new `HttpLoggingInterceptor` with structured logging
output. Support logging levels: basic (method, URL, status, duration),
headers (basic + request/response headers with sensitive header
masking), body (headers + request/response body with size limits and
content type filtering). Add format options: text, JSON, and custom
formatters. Log multipart request bodies with per-part metadata.
Support async logging to avoid blocking the request thread.

### W2: Implement connection health monitoring dashboard

Add an internal monitoring system that tracks: connection pool
utilization (active/idle/total per host), connection lifetimes, TLS
handshake durations, DNS resolution times, request durations
(percentiles), retry rates, and cache hit rates. Expose metrics
through a `MetricsListener` interface and provide a JMX MBean
implementation. Add a diagnostic `dump()` method that produces a
human-readable report of all connection pool state.

### W3: Refactor interceptor chain to support async non-blocking execution

The current interceptor chain is synchronous and blocks a thread for
the entire duration of a request. Redesign the chain to support
non-blocking, asynchronous interceptors that yield on I/O operations.
Each interceptor should be able to return a deferred result. The
synchronous API should remain backward-compatible by wrapping the
async chain. Update all built-in interceptors — retry, bridge,
cache, connect, and call-server — to use the async path. Ensure
the `EventListener` callbacks are dispatched on a consistent thread.

### W4: Implement configurable circuit breaker per host

Add a circuit breaker pattern that tracks failures per host and
temporarily stops sending requests to hosts that are consistently
failing. Implement the three-state model: closed (normal), open
(blocking requests and returning an immediate failure), and half-open
(allowing a probe request to test recovery). Make failure thresholds,
open duration, and half-open probe count configurable per host via
the client builder. Integrate with the `EventListener` for state
transitions. Failing-open should still allow callers to opt out
via a request tag.

### W5: Add full HTTP/2 server push support

Implement client-side handling for HTTP/2 server push. When the
server sends a `PUSH_PROMISE` frame, validate the promised request
against a configurable policy (same-origin check, allowed methods,
header filters). Store accepted pushed responses in the cache for
subsequent matching requests. Add a `PushPromiseHandler` callback
interface so applications can accept, reject, or modify pushed
responses. Handle cancellation of push streams, RST_STREAM
propagation, and flow-control accounting for pushed data frames.

### W6: Implement transparent cookie persistence and policy engine

Replace the stateless `CookieJar` interface with a full cookie
management subsystem. Implement persistent cookie storage backed by
a file-based store with encryption at rest. Enforce RFC 6265
semantics: domain and path scoping, Secure and HttpOnly flags,
SameSite attribute, maximum cookie limits per domain and total.
Add a `CookiePolicy` that allows applications to accept, reject,
or modify cookies per domain. Provide migration from the existing
`CookieJar` and ensure thread safety for concurrent request paths.

### W7: Build a diagnostic proxy mode for request/response inspection

Add an embeddable diagnostic proxy to OkHttp that sits between the
application and the network. It should capture full request and
response details, including headers, bodies, TLS handshake
information, timing breakdowns (DNS, connect, TLS, request send,
response receive), and connection reuse metadata. Provide a
programmatic API for registering inspection callbacks, filtering
captured traffic by host or URL pattern, and exporting captured
sessions as HAR files. Ensure the proxy has minimal performance
overhead when no inspector is registered.

### W8: Implement graceful connection draining and shutdown protocol

Add a coordinated shutdown mechanism for the connection pool. When
shutdown is initiated, stop acquiring new connections, send HTTP/2
GOAWAY frames on active HTTP/2 connections, allow in-flight requests
to finish within a configurable grace period, and forcefully close
remaining connections after the deadline. Track shutdown progress
through the `EventListener`. Add a `shutdownNow()` variant that
immediately cancels all in-flight calls. Ensure WebSocket connections
receive a close frame before termination. Integrate with the
`Dispatcher` to reject new calls during shutdown.

### W9: Add end-to-end request tracing with distributed trace propagation

Implement distributed tracing support that assigns a unique trace
and span ID to each request and propagates them via W3C Trace Context
headers. Capture spans for each phase of the request lifecycle: DNS
resolution, TCP connect, TLS handshake, request write, response
headers read, and response body read. Model retries and redirects as
child spans. Provide a `TraceExporter` interface with implementations
for logging and OpenTelemetry-compatible output. Integrate with the
`EventListener` so that existing listener consumers can correlate
events to trace spans.

### W10: Implement a response body streaming transformation pipeline

Add a `BodyTransformer` interface that allows chaining transformations
on the response body as it streams. Support built-in transformers
for decompression, decryption, byte-range reassembly, and content
validation (checksum/signature verification). Transformers should
compose as a pipeline where each stage receives a streaming source
from the previous stage. Integrate with the cache so that
transformed bodies can optionally be stored post-transformation.
Expose per-stage metrics (bytes processed, time spent) through the
`EventListener`.
