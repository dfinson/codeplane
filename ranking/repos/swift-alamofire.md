# Alamofire/Alamofire

| Field | Value |
|-------|-------|
| **URL** | https://github.com/Alamofire/Alamofire |
| **License** | MIT |
| **Language** | Swift |
| **Scale** | Small (focused library) |
| **Category** | HTTP networking library |

## Why this repo

- **Single-purpose**: HTTP networking library for Swift. Request building,
  response handling, authentication, server trust evaluation, and request
  interceptors. One developer can hold the full codebase in their head.
- **Well-structured**: Source under `Source/` with clear file-per-concern.
  Core request lifecycle, response serialization, authentication, and server
  trust are separated into focused types.
- **Rich history**: 3K+ commits, the standard networking library for iOS/macOS
  development (40K+ stars). Well-reviewed PRs covering API evolution across
  Swift versions.
- **Permissive**: MIT license.

## Structure overview

```
Source/
├── Session.swift            # Main Session (replaces SessionManager)
├── Request.swift            # Base Request type
├── DataRequest.swift        # Data request specialization
├── DownloadRequest.swift    # Download request specialization
├── UploadRequest.swift      # Upload request specialization
├── HTTPMethod.swift         # HTTP method enum
├── HTTPHeaders.swift        # Header management
├── URLConvertible.swift     # URL conversion protocols
├── ParameterEncoding.swift  # URL/JSON parameter encoding
├── ResponseSerialization.swift  # Response decoders
├── Validation.swift         # Response validation
├── Authentication/          # HTTP auth challenges
├── ServerTrustEvaluation.swift  # SSL pinning, trust evaluation
├── RequestInterceptor.swift # Retry, adapt interceptors
├── EventMonitor.swift       # Request lifecycle events
├── CachedResponseHandler.swift  # Cache control
└── Notifications.swift      # Notification posting
```

## Scale indicators

- ~30 Swift source files
- ~12K lines of code
- Flat structure (1 level)
- Depends only on Foundation

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `RequestInterceptor.retry` called with wrong error on timeout

When a request times out and the `RequestInterceptor` decides to retry,
the error passed to `retry()` is a generic `URLError.timedOut` instead
of preserving the original request context (URL, method, headers). The
interceptor cannot make informed retry decisions. Fix the retry path
to pass a rich error with the original request details.

### N2: Add `cURLDescription` for upload requests with multipart body

The `cURLDescription()` method generates a curl command for debugging
but omits the body for multipart upload requests. It shows
`--data '<multipart body omitted>'`. Generate a proper curl command
with `--form` flags for each multipart part, including file references
and content types.

### N3: Fix `DownloadRequest` not calling `EventMonitor.requestDidFinish`

When a `DownloadRequest` completes successfully, the `EventMonitor`
receives `downloadDidFinishDownloadingTo` but not the generic
`requestDidFinish` event. Code that observes request completion via
`requestDidFinish` misses download completions. Emit `requestDidFinish`
after `downloadDidFinishDownloadingTo`.

### N4: Fix `HTTPHeaders` case-sensitivity mismatch on `value(for:)`

`HTTPHeaders.value(for:)` performs a case-insensitive lookup, but
`HTTPHeaders.remove(name:)` only removes the first matching header by
case-sensitive comparison. When a server returns duplicate headers with
different casing (e.g. `Content-Type` and `content-type`), removing by
name leaves stale entries. Fix `remove(name:)` to use the same
case-insensitive matching that `value(for:)` uses.

### N5: Fix `URLEncodedFormEncoder` dropping nested array parameters

When encoding parameters with nested arrays like
`["filter": ["status": ["active", "pending"]]]`, the
`URLEncodedFormEncoder` produces `filter[status][]=active` but drops
the second value. The array index tracking resets when recursing into
nested containers. Fix the encoder to correctly track array indices
across nested levels so all values are emitted.

### N6: Fix `Session.deinit` not cancelling in-flight requests

When a `Session` instance is deallocated while requests are still in
flight, the underlying `URLSession` is invalidated but individual
`Request` objects are never cancelled. Their completion handlers fire
with a confusing `URLError.cancelled` instead of an Alamofire-level
error. Cancel all tracked requests in `Session.deinit` before
invalidating the `URLSession` so callers receive `AFError.sessionDeinitialized`.

### N7: Fix `Validation` content-type check failing on parameters

`validate(contentType:)` matches the response MIME type against the
acceptable set using exact string comparison, but some servers return
content types with parameters (e.g. `application/json; charset=utf-8`).
A request validated with `[.json]` fails because the parameter suffix
does not match. Parse the MIME type properly and compare only the
type/subtype portion, ignoring parameters.

### N8: Fix `ResponseSerializer` treating empty 201 response as failure

`DecodableResponseSerializer` treats a `201 Created` response with an
empty body as a decoding error, even though an empty body is valid for
creation endpoints. The `emptyResponseCodes` set only includes `204`
and `205`. Add `201` to the default `emptyResponseCodes` when the
generic type is `Empty` or `Void`, so empty creation responses succeed
without requiring callers to override the defaults.

### N9: Fix `Notifications` posted on background queue instead of main

`Notification.Name.Task` notifications for request lifecycle events
are posted on the `URLSession` delegate queue (a background serial
queue) instead of the main queue. UI code that observes these
notifications to update progress indicators crashes with a main-thread
assertion. Post the notifications on the main queue, or document the
threading contract and dispatch in the `NotificationCenter` posting.

### N10: Fix `URLConvertible` double-encoding percent-encoded paths

When a `String` conforming to `URLConvertible` already contains
percent-encoded characters (e.g. `/path/hello%20world`), calling
`asURL()` re-encodes the percent sign, producing `/path/hello%2520world`.
This causes 404s for URLs with pre-encoded segments. Use
`URL(string:)` without additional encoding when the string is already
a valid URL, only falling back to percent-encoding for raw strings.

## Medium

### M1: Implement certificate transparency verification

Add Certificate Transparency (CT) verification to the `ServerTrustEvaluating`
system. Verify that the server's certificate has valid Signed Certificate
Timestamps (SCTs) from recognized CT logs. Support both embedded SCTs
(in the certificate) and SCTs delivered via TLS extension. Add a
`CertificateTransparencyEvaluator` that composes with existing trust
evaluators.

### M2: Add response caching with ETags and conditional requests

Implement a response cache that stores responses and automatically
sends conditional requests (`If-None-Match` / `If-Modified-Since`)
on subsequent requests. The cache should respect `Cache-Control`
directives, support disk and memory storage, and be configurable
per-request. Add cache hit/miss tracking through `EventMonitor`.
Support cache invalidation by URL pattern.

### M3: Implement request prioritization with QoS mapping

Add request priority support that maps to URLSession's task priority
and QoS classes. Support `RequestPriority` levels (low, normal, high,
critical) configurable per-request. High-priority requests should
preempt lower-priority ones when connection limits are reached. Add
priority-aware request queuing that respects a configurable maximum
concurrent requests per priority level.

### M4: Add per-request timing metrics via `EventMonitor`

Collect fine-grained timing metrics for each request phase: DNS
resolution, TCP connect, TLS handshake, time-to-first-byte, and
total transfer time. Expose metrics through a `RequestMetrics` struct
attached to the `Request` and reported through `EventMonitor` callbacks.
Map `URLSessionTaskMetrics` and `URLSessionTaskTransactionMetrics`
into Alamofire's type system. Support aggregation across redirect
chains and retry attempts.

### M5: Implement configurable automatic retry with exponential backoff

Add a built-in `ExponentialBackoffRetryPolicy` conforming to
`RequestInterceptor` that retries failed requests with configurable
base delay, multiplier, maximum delay, maximum retry count, and
jitter. Support retry only for idempotent HTTP methods by default.
Detect retryable errors (network timeouts, 429, 503) and respect
`Retry-After` headers when present. Integrate with `EventMonitor`
to report retry attempts and total retry duration.

### M6: Add request/response logging interceptor

Implement a `LoggingEventMonitor` that produces structured logs for
request lifecycle events. Log request method, URL, headers, body
summary, and timing. Log response status, headers, body size, and
decoded error summaries. Support configurable log levels per event
type, body truncation limits, and header redaction for sensitive
fields like `Authorization`. Output to `os_log`, a custom logger
protocol, or both.

### M7: Implement per-host connection limiting and queuing

Add a connection manager that limits concurrent requests per host.
When the limit is reached, queue new requests and dispatch them as
connections become available. Support configurable limits per host
pattern (e.g. `*.api.example.com`). Prioritize queued requests
using `RequestPriority` from M3 if set. Expose queue depth and
active connection count through `EventMonitor` for monitoring.

### M8: Add custom redirect handling policy

Implement a `RedirectHandler` protocol that intercepts HTTP redirects
before they are followed. Support policies: follow all, follow
same-host only, follow with header stripping (remove `Authorization`
on cross-origin redirect), and block. Allow per-request override of
the session-level policy. Expose the redirect chain (all intermediate
URLs and status codes) on the completed `Request` for debugging.

### M9: Implement offline request queue with persistence

Add a persistent request queue that captures requests made while the
device is offline and replays them when connectivity is restored. Use
`NWPathMonitor` to detect connectivity changes. Serialize pending
requests to disk using `Codable`. Support configurable TTL for queued
requests, maximum queue depth, and conflict resolution when the same
endpoint is queued multiple times. Report queue state changes through
`EventMonitor`.

### M10: Add response streaming with incremental progress

Implement a streaming response API on `DataRequest` that delivers
response data in chunks as they arrive instead of buffering the
entire response. Expose a `DataStream<Data>` that yields chunks with
cumulative progress. Support back-pressure so slow consumers pause
the transfer. Integrate with `Validation` to validate response
headers (status code, content type) before streaming begins, failing
fast without buffering the body.

## Wide

### W1: Add comprehensive network mocking framework

Implement a test-focused network mocking system built into Alamofire.
Support: URL pattern matching with wildcards, recorded response
playback (HAR format), response delay simulation, network condition
simulation (latency, packet loss, bandwidth throttling), request
assertion (verify expected requests were made in order), and
automatic mock generation from live traffic. Integrate with XCTest.

### W2: Implement reactive/Combine extensions as first-class API

Add Combine publishers for all Alamofire request types. Return
`AnyPublisher<DataResponse<T>, Never>` from request methods.
Support progress tracking via `Progress` publisher, streaming
responses via `Data` publisher chunks, and cancellation via
Combine subscription lifecycle. Add SwiftUI integration with
`@Published` request state. Support back-pressure for streaming.
Deprecate the completion-handler API in favor of Combine/async-await.

### W3: Add full GraphQL client layer

Build a GraphQL layer on top of Alamofire's HTTP primitives. Support
queries, mutations, and subscriptions (over WebSocket). Parse GraphQL
responses and separate `data` from `errors`. Support automatic
persisted queries (APQ) with hash-based lookup. Add request batching
for multiple queries in a single HTTP request. Support file uploads
via the GraphQL multipart request spec. Integrate with `Codable` for
typed response deserialization. Add a query document caching layer
that deduplicates identical in-flight queries.

### W4: Implement connection health monitoring and circuit breaker

Add a circuit breaker pattern that tracks per-host error rates and
temporarily stops sending requests to unhealthy hosts. Support
states: closed (normal), open (blocking requests), half-open (probing
with a single request). Configure error rate thresholds, evaluation
windows, and recovery timeouts per host. Integrate with
`RequestInterceptor` to intercept requests before they hit the
network. Add health check endpoints that can be polled during the
open state. Report circuit state transitions through `EventMonitor`.

### W5: Add middleware pipeline for request/response transformation

Implement a composable middleware system where each middleware can
transform the request before sending and the response after
receiving. Support ordering, conditional execution (by URL pattern,
method, or custom predicate), and short-circuiting (return a
synthetic response without hitting the network). Ship built-in
middleware: header injection, request signing (HMAC), response
caching, body compression (gzip/brotli), and content negotiation.
Middleware should compose with existing `RequestInterceptor` and
`EventMonitor` without replacing them.

### W6: Implement WebSocket support with automatic reconnection

Add a `WebSocketRequest` type that manages a WebSocket connection
through Alamofire's session. Support text and binary messages,
ping/pong, and connection close handshake. Implement automatic
reconnection with configurable backoff when the connection drops.
Route WebSocket lifecycle events through `EventMonitor`. Support
per-message compression (permessage-deflate). Integrate with
`ServerTrustEvaluation` for secure WebSocket (wss://) connections.
Add message framing helpers for JSON-based WebSocket protocols.

### W7: Add distributed tracing with OpenTelemetry-compatible spans

Instrument every request with a trace context compatible with W3C
Trace Context and OpenTelemetry. Create spans for each request phase
(DNS, connect, TLS, send, wait, receive) with timing attributes.
Propagate `traceparent` and `tracestate` headers. Support span
export to stdout, OTLP/HTTP, and a custom exporter protocol. Add
baggage propagation for cross-service context. Integrate with
`EventMonitor` so existing monitors receive span IDs. Support
sampling strategies (always-on, probabilistic, rate-limited).

### W8: Implement full proxy support with authentication

Add configurable HTTP and SOCKS5 proxy support. Allow per-request
proxy configuration overriding the session default. Support proxy
authentication (Basic, Digest, NTLM) through the existing
`Authentication` system. Handle CONNECT tunneling for HTTPS requests
through HTTP proxies. Add proxy auto-configuration (PAC) file
support. Integrate with `ServerTrustEvaluation` for proxy TLS
verification. Route proxy connection events through `EventMonitor`.
Support proxy bypass lists for internal hostnames and IP ranges.

### W9: Add multi-path TCP and adaptive transport selection

Implement multipath TCP support for seamless handover between Wi-Fi
and cellular. Expose `URLSessionConfiguration.multipathServiceType`
mapping through Alamofire's session configuration. Add an adaptive
transport selector that chooses between HTTP/1.1, HTTP/2, and HTTP/3
based on server support and network conditions. Monitor transport
protocol negotiation via `EventMonitor`. Support connection migration
across network transitions without request failure. Add diagnostic
APIs that report which transport protocol each request used and
whether migration occurred.

### W10: Build cross-platform Linux and Windows support layer

Extend Alamofire to compile and run on Linux and Windows via Swift's
cross-platform Foundation. Abstract platform-specific APIs behind
protocol boundaries: replace Security.framework trust evaluation with
OpenSSL-based certificate verification, replace `NWPathMonitor` with
a cross-platform reachability abstraction, and handle file system
differences for download destinations. Ensure `ParameterEncoding`,
`ResponseSerialization`, `HTTPHeaders`, and `Validation` work
identically across platforms. Add CI matrix entries for Ubuntu and
Windows. Ship platform-specific `ServerTrustEvaluating` conformances
that use the platform's native TLS stack.
