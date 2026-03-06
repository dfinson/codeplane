# Alamofire/Alamofire

| Field | Value |
|-------|-------|
| **URL** | https://github.com/Alamofire/Alamofire |
| **License** | MIT |
| **Language** | Swift |
| **Scale** | Small (focused library) |
| **Category** | HTTP networking library |
| **Set** | ranker-gate |
| **Commit** | `14dc760dee02fcd28c42f3d8fd760ebfbae6ce0d` |

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

### N3: Fix `AuthenticationInterceptor` not cancelling queued requests on credential invalidation

When `AuthenticationInterceptor` detects an expired credential and
begins a refresh cycle, requests that arrive during the refresh are
queued in `mutableState.additionalRequests`. If the credential refresh
fails permanently (e.g., refresh token revoked), the queued requests
are completed with the refresh error but their underlying
`URLSessionTask`s are never cancelled. This leaves zombie tasks
consuming connections. Cancel the underlying tasks for all queued
requests when credential refresh fails in
`Source/Features/AuthenticationInterceptor.swift`.

### N4: Fix `RequestTaskMap` not cleaning up cancelled tasks

In `Source/Core/RequestTaskMap.swift`, when a `Request` is cancelled
and its `URLSessionTask` transitions to `.completed`, the entry is
removed from the map. However, if the cancellation races with task
creation (the task is cancelled before the delegate receives
`didCreateTask`), the `RequestTaskMap` retains a dangling entry
for the request with no associated task. Over time this leaks
memory in long-lived `Session` instances. Add cleanup logic in the
task-to-request mapping path to remove entries whose tasks are in
a terminal state.

### N5: Fix `URLEncodedFormEncoder` dropping nested array parameters

When encoding parameters with nested arrays like
`["filter": ["status": ["active", "pending"]]]`, the
`URLEncodedFormEncoder` produces `filter[status][]=active` but drops
the second value. The array index tracking resets when recursing into
nested containers. Fix the encoder to correctly track array indices
across nested levels so all values are emitted.

### N6: Fix `ParameterEncoder` not encoding `Date` values consistently

When using `JSONParameterEncoder` vs `URLEncodedFormParameterEncoder`
to encode a struct containing `Date` fields, the two encoders produce
inconsistent date representations. `JSONParameterEncoder` uses the
underlying `JSONEncoder.dateEncodingStrategy` (defaulting to
`deferredToDate`), while `URLEncodedFormEncoder` in
`Source/Features/URLEncodedFormEncoder.swift` always uses
`timeIntervalSinceReferenceDate`. Add a `DateEncoding` configuration
to `URLEncodedFormEncoder` that supports `.iso8601`,
`.secondsSince1970`, and `.formatted(DateFormatter)` to match
`JSONEncoder`'s strategies.

### N7: Fix `ServerTrustManager` not supporting wildcard subdomains

`ServerTrustManager` in `Source/Features/ServerTrustEvaluation.swift`
looks up evaluators by exact host match. A configuration like
`["*.example.com": PinnedCertificatesTrustEvaluator(...)]` does not
match `api.example.com` because the lookup uses dictionary subscript
without wildcard expansion. Fix `serverTrustEvaluator(forHost:)` to
support leading-wildcard patterns by matching against the host's
parent domain when no exact match is found.

### N8: Fix `ResponseSerializer` treating empty 201 response as failure

`DecodableResponseSerializer` treats a `201 Created` response with an
empty body as a decoding error, even though an empty body is valid for
creation endpoints. The `emptyResponseCodes` set only includes `204`
and `205`. Add `201` to the default `emptyResponseCodes` when the
generic type is `Empty` or `Void`, so empty creation responses succeed
without requiring callers to override the defaults.

### N9: Fix `MultipartFormData` not setting `filename` for `Data` appends

When appending raw `Data` to `MultipartFormData` via
`append(_ data:, withName:, mimeType:)` in
`Source/Features/MultipartFormData.swift`, the `Content-Disposition`
header omits the `filename` parameter. Some server frameworks
(e.g., Rails, Django) reject file uploads that lack a `filename`
in the multipart part headers, returning 422 errors. Add an optional
`fileName` parameter to the `Data` append overload (defaulting to
`nil` for backward compatibility) and include it in the
`Content-Disposition` when provided.

### N10: Fix `DownloadRequest` resume data not included in `AFError`

When a `DownloadRequest` fails after partial transfer, the resume
data from `URLSessionDownloadTask` is stored in
`DownloadRequest.mutableState.resumeData` in
`Source/Core/DownloadRequest.swift`, but the `AFError` delivered to
response handlers does not include it. Callers must separately
access `request.resumeData` which may be nil by the time the error
handler runs if the request is deallocated. Attach the resume data
to the `AFError.downloadedFileMoveFailed` error case so that error
handlers have the data needed to retry the download.

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

### M5: Add request deduplication for identical in-flight requests

Implement a `RequestDeduplicator` that detects when multiple callers
make identical requests (same URL, method, headers, and body) while
a matching request is already in flight. Instead of sending duplicate
requests, attach additional response handlers to the existing
`Request`. Key deduplication by a configurable hash of request
properties. Support opting out per-request for non-idempotent
methods. Track deduplication statistics (hits, total saved requests)
through `EventMonitor`. Integrate with `Session` as a configurable
option in `Source/Core/Session.swift`.

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

### M8: Add request body compression with content negotiation

Implement automatic request body compression that integrates with
`RequestCompression` in `Source/Features/RequestCompression.swift`.
Currently `RequestCompression` only supports deflate. Add gzip and
zstd support with algorithm selection based on server capability
advertised via `Accept-Encoding` response headers from prior
requests. Cache server compression support per-host. Add a
`CompressionNegotiator` adapter that can be attached to `Session`
to automatically compress request bodies above a configurable
size threshold and set the appropriate `Content-Encoding` header.

### M9: Implement offline request queue with persistence

Add a persistent request queue that captures requests made while the
device is offline and replays them when connectivity is restored. Use
`NWPathMonitor` to detect connectivity changes. Serialize pending
requests to disk using `Codable`. Support configurable TTL for queued
requests, maximum queue depth, and conflict resolution when the same
endpoint is queued multiple times. Report queue state changes through
`EventMonitor`.

### M10: Add typed error recovery chain for response serialization

Implement an error recovery mechanism for `ResponseSerializer` types
in `Source/Features/ResponseSerialization.swift`. When a
`DecodableResponseSerializer` fails to decode the response body,
allow registering fallback serializers that attempt alternative
deserialization strategies (e.g., try a v2 API model, then a v1
model, then an error envelope model). Chain fallbacks via
`responseDecodable(of:fallbacks:)` on `DataRequest`. Each fallback
receives the original response data and the previous error. Report
which serializer succeeded through `EventMonitor` so callers can
track API version drift.

## Wide

### W1: Add comprehensive network mocking framework

Implement a test-focused network mocking system built into Alamofire.
Support: URL pattern matching with wildcards, recorded response
playback (HAR format), response delay simulation, network condition
simulation (latency, packet loss, bandwidth throttling), request
assertion (verify expected requests were made in order), and
automatic mock generation from live traffic. Integrate with XCTest.

### W2: Add SwiftUI-native request state management

Build a SwiftUI integration layer on top of Alamofire's existing
async/await and Combine support. Add a `@RequestState` property
wrapper that manages request lifecycle (idle, loading, success,
failure) with automatic view updates. Add an `AlamofireView`
modifier that triggers requests on appear and handles retry UI.
Support pull-to-refresh binding, pagination state tracking, and
automatic request cancellation when views disappear. Integrate
with `Session`, `DataRequest`, and `DownloadRequest` in
`Source/Core/`. Add `Identifiable` conformance to `Request` for
list-based UIs. Include a preview-compatible mock `Session`.

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

### W6: Add network traffic recording and replay for testing

Build a traffic recording system that captures all HTTP
request/response pairs flowing through a `Session` and exports
them in a structured format. Implement a `RecordingEventMonitor`
that serializes requests (URL, method, headers, body) and
responses (status, headers, body, timing) to a JSON-based
cassette file. Add a `ReplayProtocolProvider` using
`URLProtocol` subclass that intercepts requests and returns
recorded responses by matching URL, method, and optionally
headers/body. Support strict mode (fail on unmatched requests)
and loose mode (pass through to network). Support updating
stale cassettes by re-recording only changed endpoints.

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
