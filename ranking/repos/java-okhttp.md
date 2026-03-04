# square/okhttp

| Field | Value |
|-------|-------|
| **URL** | https://github.com/square/okhttp |
| **License** | Apache-2.0 |
| **Language** | Java (Kotlin) |
| **Scale** | Medium (multi-module project) |
| **Category** | HTTP client |

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
okhttp/src/main/kotlin/okhttp3/
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

8 tasks (3 narrow, 3 medium, 2 wide) for the Java/Kotlin HTTP client.

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
