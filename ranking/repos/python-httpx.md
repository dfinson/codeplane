# encode/httpx

| Field | Value |
|-------|-------|
| **URL** | https://github.com/encode/httpx |
| **License** | BSD-3-Clause |
| **Language** | Python |
| **Scale** | Small (focused library) |
| **Category** | HTTP client library |

## Why this repo

- **Single-purpose**: Async/sync HTTP client for Python. One developer can hold
  the entire codebase in their head.
- **Well-structured**: Clean separation between transport layer, client API,
  auth, middleware, decoders. Source in `httpx/` with ~50 core modules.
- **Rich history**: Active development since 2019, ~3K commits, regular PRs
  with meaningful code review. Used by FastAPI and many production systems.
- **Permissive**: BSD-3-Clause, fully usable for training data.

## Structure overview

```
httpx/
├── _api.py              # Top-level convenience functions
├── _client.py           # Client and AsyncClient classes
├── _auth.py             # Authentication flows
├── _transports/         # HTTP/1.1, HTTP/2, mock transports
├── _decoders.py         # Content decoders
├── _models.py           # Request/Response models
├── _urls.py             # URL parsing
├── _content.py          # Request body encoding
└── _config.py           # SSL, timeout, proxy config
```

## Scale indicators

- ~50 Python source files
- ~15K lines of code
- Clear module boundaries, no deep nesting
- Minimal dependencies (httpcore, anyio)

---

## Tasks

8 tasks (3 narrow, 3 medium, 2 wide) for the Python HTTP client library.

## Narrow

### N1: Fix timeout not propagated on redirect

When a request with a custom timeout follows a 3xx redirect, the timeout
configuration from the original request is not carried forward to the
redirected request. The redirected request falls back to the client's default
timeout instead. Fix the redirect handling to preserve the per-request timeout
across redirects.

### N2: Add support for reading `.netrc` credentials

httpx should be able to read `~/.netrc` files for automatic authentication
when no explicit auth is provided. Implement a `.netrc` lookup that runs
after explicit auth checks but before sending unauthenticated requests.
Respect the `NETRC` environment variable for custom file paths.

### N3: Fix Content-Length header not removed on 303 redirect

When a POST request gets a 303 See Other redirect, the method changes to GET
and the body is dropped — but the `Content-Length` header from the original
POST is still present in the redirected GET request. Strip `Content-Length`
(and `Content-Type`) headers when the method changes due to a 303 redirect.

## Medium

### M1: Add HTTP/2 server push support

Implement support for HTTP/2 server push in the async client. When the server
sends a PUSH_PROMISE frame, the client should accept the pushed response and
make it available through a new `pushed_responses` attribute on the response
object. Include configuration to disable server push and to set a maximum
number of concurrent pushes.

### M2: Implement response streaming with backpressure

The current streaming implementation (`aiter_bytes`, `aiter_lines`) does not
apply backpressure to the underlying transport when the consumer is slow.
Add flow control so that when the async iterator consumer pauses, the
transport layer stops reading from the socket. This requires changes to the
transport interface, the response streaming API, and the HTTP/1.1 and HTTP/2
transport implementations.

### M3: Add request/response event hooks

Add an event hook system that fires callbacks at key points in the request
lifecycle: before-request, after-response, on-error, on-redirect. Hooks
should be configurable at both the client level and per-request level.
Per-request hooks should run in addition to (not replacing) client-level
hooks. Async hooks should be supported in the async client.

## Wide

### W1: Migrate from httpcore to native transport layer

Replace the dependency on `httpcore` with a native transport implementation
built on top of `anyio` directly. This affects the HTTP/1.1 transport,
HTTP/2 transport, connection pooling, proxy handling, and the transport
interface itself. The public API should remain unchanged — this is an
internal implementation change.

### W2: Add comprehensive request tracing and diagnostics

Implement a request tracing system that captures detailed timing information
for each phase of a request: DNS resolution, TCP connect, TLS handshake,
request send, TTFB (time to first byte), and content transfer. Surface
this through a `request.extensions["trace"]` dict. Add structured logging
integration that emits trace events. Update the mock transport to support
trace simulation for testing.

### N4: Fix proxy CONNECT tunnel not using proxy auth credentials

When connecting through an HTTP proxy with `proxy_auth`, the CONNECT
request for HTTPS tunneling does not include the `Proxy-Authorization`
header. The proxy rejects the tunnel with 407. Fix the proxy transport
to attach proxy credentials to CONNECT requests.

### N5: Fix `AsyncClient` context manager not closing transport on exception

If an exception occurs during request processing inside an
`async with httpx.AsyncClient()` block, the transport connections are
not properly closed. The `__aexit__` method swallows the close error
instead of ensuring cleanup. Fix the async context manager to guarantee
transport closure even when the body raises.

### N6: Add `follow_redirects` per-request override

The `follow_redirects` setting is client-level only. Add support for
overriding it per request: `client.get(url, follow_redirects=False)`.
The per-request value should take precedence over the client default.

### N7: Fix HTTP/2 connection not reused after receiving GOAWAY

After receiving an HTTP/2 GOAWAY frame, the connection pool creates a
new connection but doesn't remove the goaway'd connection from the pool.
Subsequent requests may still be dispatched to the stale connection,
causing stream errors. Fix the connection pool to evict connections
that have received GOAWAY.

### N8: Add `client.head()` convenience method

The client has `get()`, `post()`, `put()`, `delete()`, `patch()`, and
`options()` convenience methods but not `head()`. Add `client.head()`
and `async_client.head()` that send HEAD requests with the same
parameter signature as `get()`.

### N9: Fix URL encoding of path segments with unicode characters

URLs with unicode characters in path segments (e.g., `/api/users/José`)
are double-encoded when the path is already percent-encoded. The URL
parser applies percent-encoding without checking if the segment is
already encoded. Fix `_urls.py` to detect and skip already-encoded
segments.

### N10: Fix `Response.raise_for_status()` losing response body

When `raise_for_status()` raises an `HTTPStatusError`, the response body
has already been consumed and cannot be read from the exception's
`response` attribute. Capture the body text before raising so it's
available via `exc.response.text`.

### M4: Implement connection pool warm-up

Add a `client.warm(urls)` method that pre-establishes connections to a
list of hosts without sending requests. The warmed connections should
be available in the pool for subsequent requests. Support both HTTP/1.1
and HTTP/2, including TLS handshake completion. Add async variant.

### M5: Add response caching with conditional requests

Implement a `CacheTransport` wrapper that caches responses and
automatically sends conditional requests (If-None-Match/If-Modified-Since)
on subsequent requests. Respect Cache-Control directives for cache
validity. Support both memory and disk storage backends. The cache
should work with both `Client` and `AsyncClient`.

### M6: Implement cookie persistence to disk

Add a `PersistentCookieJar` that saves and loads cookies to/from a file
(Netscape cookie format or JSON). Support expiration handling, domain
scoping, and secure-only cookies. The jar should auto-save on changes
and load on initialization. Add a `clear_session_cookies()` method.

### M7: Add SOCKS5 proxy support

Implement SOCKS5 proxy support in the transport layer. Support
SOCKS5 with username/password authentication and SOCKS5 without auth.
Support both IPv4 and IPv6 through the SOCKS proxy. Add configuration
via `proxy="socks5://host:port"` in the client constructor.

### M8: Implement request signing for AWS Signature V4

Add an `AWS4Auth` authentication class that signs requests using AWS
Signature Version 4. Support all HTTP methods, query string signing,
chunked upload signing, and presigned URLs. The implementation should
handle the canonical request construction, string-to-sign generation,
and signature calculation per the AWS specification.

### M9: Implement multipart upload with progress tracking

Add progress callbacks for multipart file uploads. The callback should
receive bytes sent so far and total bytes. Support per-part progress
and overall progress. Work with both sync and async clients. The
progress reporting should not significantly impact upload throughput.

### M10: Add automatic retry with configurable policy

Implement request retry support with a `RetryTransport` that wraps the
base transport. Support configurable retry count, backoff strategy
(fixed, exponential, exponential with jitter), retryable status codes,
retryable exception types, and per-request retry override. Respect
`Retry-After` headers.

### W3: Add HTTP/3 (QUIC) transport support

Implement an HTTP/3 transport using the QUIC protocol. Support 0-RTT
connection resumption, connection migration on network change, and
Alt-Svc header parsing for HTTP/3 discovery. Fall back to HTTP/2 when
QUIC is unavailable. This requires a new transport implementation,
connection pool changes for QUIC connections, and Alt-Svc response
processing in the response handling pipeline.

### W4: Implement a comprehensive middleware system

Add a middleware layer between the client API and transport. Middleware
can intercept requests before sending and responses after receiving.
Built-in middleware: logging, metrics (timing, status counts), rate
limiting, circuit breaker. Support both sync and async middleware.
Middleware ordering should be explicit. This touches the client,
transport interface, and adds a new middleware package.

### W5: Add WebSocket client support

Implement WebSocket support in httpx. Add `client.ws_connect(url)` that
returns a `WebSocketConnection` with `send_text()`, `send_bytes()`,
`receive()`, `close()`, and async iteration. Support ping/pong,
compression (permessage-deflate), subprotocol negotiation, and
connection upgrade from HTTP/1.1. Handle both sync and async clients.

### W6: Implement HAR (HTTP Archive) export

Add the ability to record all HTTP traffic through the client and
export it as HAR format. Implement a `HARTransport` wrapper that
captures request/response details including timing, headers, body
content (with size limits), TLS info, and connection reuse status.
Support both recording and playback (useful for testing). Changes
span the transport layer, response model (timing metadata), and
a new HAR serialization module.

### W7: Add mutual TLS (mTLS) client certificate support

Implement client certificate authentication. Support PEM and PKCS#12
certificate formats, passphrase-protected keys, certificate chain
validation, and per-request certificate selection. The implementation
needs changes to SSL configuration, transport TLS setup, and connection
pool management (certificates affect connection reuse). Add certificate
rotation without client restart.

### W8: Implement HTTP caching proxy mode

Add a mode where httpx acts as a caching HTTP proxy server. The proxy
receives requests from other applications, forwards them to the upstream
server (using httpx's transport), caches responses, and serves cached
responses for subsequent matching requests. This requires a proxy server
component, the caching transport, request matching logic, and
cache invalidation through the admin API.

### W9: Add distributed tracing integration (OpenTelemetry)

Implement automatic OpenTelemetry span creation for every HTTP request.
Create spans with standard HTTP semantic conventions (url, method,
status_code, etc.). Propagate trace context via W3C traceparent headers.
Support context injection for outgoing requests and extraction for
incoming responses. Include spans for DNS resolution, TCP connect, TLS
handshake, and content transfer sub-operations. This crosses the
transport layer, client API, and adds a tracing integration module.

### W10: Implement connection pool monitoring and diagnostics

Add a comprehensive connection pool monitoring system. Track per-host
metrics: active/idle/connecting counts, connection ages, request
queue depths, TLS session resumption rates, and HTTP/2 stream
utilization. Expose via a `client.pool_status()` method returning
structured data. Add an async event stream for pool state changes.
Support export to Prometheus format. This touches the connection pool,
transport layer, client API, and adds a monitoring module.
