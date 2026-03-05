# guzzle/guzzle

| Field | Value |
|-------|-------|
| **URL** | https://github.com/guzzle/guzzle |
| **License** | MIT |
| **Language** | PHP |
| **Scale** | Small (focused library) |
| **Category** | HTTP client library |

## Why this repo

- **Single-purpose**: PHP HTTP client with PSR-7/PSR-18 compliance.
  Middleware/handler pipeline, async requests, and cookie/redirect handling.
  One developer can hold the full codebase in their head.
- **Well-structured**: Source under `src/` with flat organization. Middleware
  stack, handler system, cookie handling, retry logic, and PSR-compliant
  request/response are clearly separated.
- **Rich history**: 4K+ commits, maintained since 2011. The standard HTTP
  client for PHP (Laravel, Symfony, WordPress all use it). Steady PRs with
  a mix of features and fixes.
- **Permissive**: MIT license.

## Structure overview

```
src/
├── Client.php              # Main HTTP client
├── ClientInterface.php     # Client contract
├── HandlerStack.php        # Middleware/handler pipeline
├── Handler/                # HTTP handlers
│   ├── CurlHandler.php     # cURL-based handler
│   ├── CurlMultiHandler.php # Async cURL handler
│   ├── StreamHandler.php   # PHP stream handler
│   └── MockHandler.php     # Testing mock
├── Middleware.php           # Built-in middleware (retry, redirect, cookies)
├── Cookie/                  # Cookie jar and storage
│   ├── CookieJar.php
│   └── SetCookie.php
├── Exception/               # Exception hierarchy
├── RetryMiddleware.php      # Retry logic
├── RedirectMiddleware.php   # Redirect following
└── TransferStats.php        # Transfer statistics
```

## Scale indicators

- ~30 PHP source files
- ~8K lines of code
- Flat structure (1-2 levels)
- Depends on PSR packages (psr/http-message, psr/http-client)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `RedirectMiddleware` not preserving fragment in redirect URL

When following a redirect, the fragment (`#section`) from the original
request URL is dropped. Per RFC 7231, if the redirect target does not
include a fragment, the fragment from the original request should be
preserved. Fix the redirect middleware to carry forward the fragment.

### N2: Add `connect_timeout` separate from `timeout`

Currently `timeout` applies to the entire request lifecycle. Add a
separate `connect_timeout` option that limits only the TCP connection
phase. This allows setting a short connection timeout (2s) with a
longer transfer timeout (60s) for large downloads. Pass the value
through to cURL's `CURLOPT_CONNECTTIMEOUT`.

### N3: Fix `MockHandler` not resetting request history on reuse

When a `MockHandler` is reused across multiple test methods, the
request history from previous tests leaks into subsequent tests. The
history array is never cleared. Add a `reset()` method to
`MockHandler` and clear history between test invocations.

### N4: Fix `CookieJar` not respecting `Max-Age` over `Expires` header

When a `Set-Cookie` header contains both `Max-Age` and `Expires`
attributes, RFC 6265 mandates that `Max-Age` takes precedence.
`SetCookie::isExpired()` currently checks `Expires` first,
causing incorrect expiration decisions when both are present. Fix
the precedence logic in `SetCookie` so `Max-Age` wins.

### N5: Fix `HandlerStack` `remove()` not updating resolved handler cache

After calling `HandlerStack::remove()` to remove a middleware by
name, subsequent requests still execute the removed middleware
because the compiled handler pipeline is cached. The `remove()`
method should invalidate the cached handler so the next request
recompiles the stack without the removed middleware.

### N6: Add `source_address` option to bind requests to a local IP

When a server has multiple network interfaces, users need to bind
outgoing requests to a specific local IP address. Add a
`source_address` request option that sets `CURLOPT_INTERFACE` on
the cURL handler and the `bindto` stream context option on the
stream handler. Validate the address format before passing it down.

### N7: Fix `TransferStats` not recording redirect timing

`TransferStats` captures total transfer time but does not break
down time spent on individual redirect hops. When
`allow_redirects` is enabled, each hop's timing is lost. Record
per-hop timing in `TransferStats` so callers can see where latency
accumulates across redirects.

### N8: Fix `CurlMultiHandler` tick loop ignoring `select_timeout`

`CurlMultiHandler` uses `curl_multi_select()` in its tick loop
but hard-codes the select timeout to 1 second, ignoring any
user-provided `select_timeout` option. Pass the configured
`select_timeout` (defaulting to 1.0) to `curl_multi_select()` so
users can tune the polling interval for latency-sensitive workloads.

### N9: Add `on_stats` callback support to `StreamHandler`

The `on_stats` request option fires a callback with
`TransferStats` after a transfer completes, but only the cURL
handlers populate it. When using `StreamHandler`, the callback
is silently never called. Collect timing data from PHP stream
operations and invoke the `on_stats` callback in `StreamHandler`.

### N10: Fix `RetryMiddleware` doubling delay on non-retryable responses

When `RetryMiddleware` receives a response that the decider
callback says should not be retried, it still calculates and
logs the exponential backoff delay before discarding it. This
causes misleading debug output. Skip delay computation entirely
when the decider returns false, and only calculate it on an
actual retry.

## Medium

### M1: Implement retry middleware with circuit breaker

Add a `CircuitBreakerMiddleware` that stops sending requests to a
failing upstream after a configurable failure threshold. Track failures
per host. Transition between closed (normal), open (failing — fast
fail), and half-open (test one request) states. Combine with the
existing retry middleware so retries respect the circuit state. Add
configurable failure detection (status codes, timeouts, exceptions).

### M2: Add request/response body streaming with progress callbacks

Implement streaming body support with progress reporting. For uploads,
add a `progress` callback option that receives bytes sent / total bytes.
For downloads, add a streaming response body that yields chunks with
progress tracking. Support pause/resume for large transfers. Add a
`StreamHandler` that wraps the existing cURL handler with stream support.

### M3: Implement connection pooling with keep-alive management

Add explicit connection pool management to Guzzle. Track persistent
connections per host, enforce maximum connections per host, and close
idle connections after a configurable timeout. Surface pool statistics
(active, idle, total connections per host) through a `PoolStats`
interface. Add `warm()` to pre-establish connections to known hosts.

### M4: Add middleware ordering with explicit priority and dependency constraints

`HandlerStack` uses `push()`, `unshift()`, `before()`, and
`after()` for middleware ordering, which breaks when middleware is
registered across multiple packages with conflicting positions.
Add a priority-based ordering system where each middleware declares
a numeric priority and optional `depends_on` / `before` constraints.
Resolve ordering with topological sort. Detect and report circular
dependencies at stack compilation time instead of at request time.

### M5: Implement automatic request decompression negotiation

Add middleware that automatically sets the `Accept-Encoding` header
to advertise supported compression algorithms (`gzip`, `deflate`,
`br`), then transparently decompresses the response body based on
the `Content-Encoding` header. Handle edge cases: chunked transfer
encoding combined with compression, partial content (206 responses),
and servers that compress without being asked. Register it in the
default `HandlerStack` in the appropriate position relative to
other middleware.

### M6: Add structured logging middleware with PSR-3 integration

Create a `LoggingMiddleware` that logs request/response details to
any PSR-3 logger. Include configurable log levels per status code
range (2xx → debug, 4xx → warning, 5xx → error). Support body
truncation with configurable max length. Redact sensitive headers
(`Authorization`, `Cookie`, custom patterns). Format as structured
JSON context on the log record. Add request correlation IDs that
propagate through redirects and retries so a full request chain
can be traced.

### M7: Implement request rate limiting middleware

Add a `ThrottleMiddleware` that enforces per-host request rate
limits using a token bucket algorithm. Support configurable rates
(e.g., 10 requests/second), burst capacity, and backpressure
strategy (delay vs. reject). For async pools, coordinate the rate
limit across concurrent requests to the same host. Expose remaining
quota through response headers or a `RateLimitInfo` object attached
to the response.

### M8: Add DNS prefetch and custom resolver support

Add a `dns_resolve` request option that maps hostnames to IPs,
bypassing system DNS (similar to cURL's `CURLOPT_RESOLVE`). Support
the same option on `StreamHandler` using a custom stream context.
Add a `DnsCacheMiddleware` that caches resolved addresses with
configurable TTL, prefetches DNS for known hosts on client
construction, and supports fallback resolvers when the primary
fails.

### M9: Implement multipart upload with resumable chunk support

Extend `multipart` request body handling to support chunked uploads
for large files. Break files into configurable chunk sizes, track
upload progress per chunk, and resume from the last successful
chunk on failure. Store chunk state externally (configurable via a
`ChunkStateStore` interface). Add `Content-Range` header generation
per chunk. Integrate with `RetryMiddleware` so chunk failures
trigger per-chunk retries rather than restarting the entire upload.

### M10: Add client-side response caching with RFC 7234 compliance

Implement an HTTP cache middleware following RFC 7234. Support
`Cache-Control` directives (`max-age`, `no-cache`, `no-store`,
`must-revalidate`, `private`, `public`), `ETag`/`If-None-Match`
conditional requests, and `Last-Modified`/`If-Modified-Since`
validation. Provide a `CacheStorageInterface` with a filesystem
implementation. Handle `Vary` header for cache key variation.
Serve stale responses with `stale-while-revalidate` when
configured.

## Wide

### W1: Add PSR-18 async client support

Implement `Psr\Http\Client\ClientInterface` with async support using
PHP 8.1 Fibers. Add `sendAsync()` that returns a `Promise` object
compatible with the existing Guzzle promise interface. Support
concurrent request execution with configurable concurrency limits.
Add async middleware support where middlewares can yield control.
Update all existing middleware to work in both sync and async modes.

### W2: Implement comprehensive HTTP debugging toolkit

Add a debugging toolkit for API development: request/response
recording (`RecorderMiddleware` that saves to HAR format),
request playback (replay recorded requests for regression testing),
request diffing (compare two requests/responses side by side),
traffic inspection UI (browser-based), and mock server generation
(auto-generate `MockHandler` stacks from recorded traffic). Each
tool should be usable independently or together.

### W3: Implement HTTP/2 server push handler

Add support for HTTP/2 server push in the cURL handler. When a
server pushes resources, capture them in a local push cache keyed
by URL. Intercept subsequent requests that match a pushed resource
and serve from the cache instead of making a new connection. Add
push event callbacks so middleware can observe and filter pushed
resources. Handle push promise cancellation, cache eviction based
on size limits, and `Cache-Control` header respect on pushed
resources. Update `TransferStats` to distinguish pushed vs. fetched
responses.

### W4: Add proxy auto-configuration (PAC) and SOCKS5 support

Extend Guzzle's proxy support beyond basic HTTP proxies. Add PAC
file parsing that evaluates `FindProxyForURL()` to select the
correct proxy per request URL. Support SOCKS5 proxies with
authentication (`CURLOPT_PROXYTYPE`). Add proxy health checking
with automatic failover to the next proxy in a list. Support
per-host proxy exclusion patterns (beyond `no_proxy` env). Add
`ProxyResolverInterface` so users can implement custom proxy
selection logic. Update `StreamHandler` to route through SOCKS5
using a stream wrapper.

### W5: Implement request batching with dependency graph

Add a batch execution system where users declare requests with
dependencies between them (e.g., request B needs the response
from request A for its URL). Model the batch as a DAG. Execute
independent requests concurrently using `CurlMultiHandler`.
Support transformers that extract data from a predecessor's
response to build the next request. Add per-request error handling
policies (fail-fast vs. continue). Report results with a
`BatchResult` object containing per-request status, timing, and
the dependency path taken.

### W6: Add comprehensive cookie policy engine

Replace the current simple cookie storage with a full cookie
policy engine. Support `SameSite` attribute enforcement (Strict,
Lax, None), `__Host-` and `__Secure-` cookie prefix validation,
public suffix list checking to prevent superdomain cookies,
third-party cookie blocking policies, and cookie quota enforcement
per domain. Add a `CookiePolicyInterface` with a strict-by-default
implementation. Provide persistent storage backends (filesystem,
SQLite) with encryption-at-rest for sensitive cookies. Update
`CookieJar` to delegate all accept/reject decisions to the policy.

### W7: Implement distributed tracing with OpenTelemetry integration

Add first-class distributed tracing support. Create a
`TracingMiddleware` that starts a span for each HTTP request,
propagates trace context headers (`traceparent`, `tracestate`,
`b3`) automatically, and records span attributes (method, URL,
status code, error). Support parent span injection so Guzzle
requests are children of the caller's span. Add `W3C Baggage`
header propagation for cross-service context. Integrate with
`RetryMiddleware` to create child spans for each retry attempt.
Record redirect hops as span events. Provide configuration for
attribute redaction and sampling.

### W8: Add flexible response body parser pipeline

Build a response body parsing pipeline that automatically
deserializes response bodies based on `Content-Type`. Support
JSON (with JSON Schema validation), XML (with SimpleXML and DOM
options), form-urlencoded, multipart, CSV, and MessagePack.
Add a `ParserRegistry` where users register custom parsers by
media type. Support content negotiation on the request side
(`Accept` header auto-set). Add streaming parsers for large JSON
and XML that yield records incrementally. Integrate error recovery
so malformed bodies produce structured parse errors rather than
PHP exceptions.

### W9: Implement client certificate and mTLS management

Add mutual TLS support with certificate lifecycle management.
Support PEM, PKCS12, and Windows certificate store formats. Add
certificate pinning (pin by public key hash) with configurable
pin sets per host. Support automatic certificate selection from
a keystore when the server requests client authentication.
Validate certificate chains against custom CA bundles with OCSP
stapling verification. Add a `TlsConfigBuilder` that constructs
the correct cURL SSL options and stream SSL context. Log TLS
handshake details (protocol version, cipher suite, certificate
info) in `TransferStats`.

### W10: Add full HTTP compliance test suite and strict mode

Implement a strict HTTP compliance mode with a comprehensive
test suite validating Guzzle's behavior against RFC 7230-7235
and RFC 9110-9112. Cover: header field parsing (folding,
whitespace, multi-value), chunked transfer encoding edge cases,
content-length vs. transfer-encoding conflicts, 1xx informational
responses, HEAD response body handling, conditional request
semantics (`If-Match`, `If-Range`), `Expect: 100-continue`
negotiation, and connection management (`Connection: close`,
keep-alive). In strict mode, reject non-compliant responses
that Guzzle currently tolerates. Add a `ComplianceReport` that
lists deviations found per-response.
