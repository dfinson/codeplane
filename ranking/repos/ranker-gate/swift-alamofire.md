# Alamofire/Alamofire

| Field | Value |
|-------|-------|
| **URL** | https://github.com/Alamofire/Alamofire |
| **License** | MIT |
| **Language** | Swift |
| **Scale** | Small (focused library) |
| **Category** | HTTP networking library |
| **Set** | ranker-gate |
| **Commit** | `f73a2fcb60198ef2b92dc3b6074b18f98ccee875` |

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

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `RequestInterceptor.retry` called with wrong error on timeout

When a request times out and the `RequestInterceptor` decides to retry,
the error passed to `retry()` is a generic `URLError.timedOut` instead
of preserving the original request context (URL, method, headers). The
interceptor cannot make informed retry decisions. Fix the retry path
to pass a rich error with the original request details.

### N2: Add `cURLDescription` for upload requests with multipart body

The `cURLDescription()` method in `Source/Core/Request.swift` generates
a curl command for debugging. For multipart upload requests where the
encoded data fits below `encodingMemoryThreshold`, the raw multipart
binary is included as `-d "..."`, producing an invalid curl invocation.
For large multipart uploads (file-backed), `httpBody` is nil and the
body is silently omitted entirely. Override `cURLDescription()` in
`UploadRequest` to detect when the upload originated from a
`MultipartFormData` (accessible via the `upload` property cast to
`MultipartUpload`) and emit `--form` flags for each body part,
including file references and content types.

### N3: Fix `AuthenticationInterceptor` not cancelling queued requests on credential invalidation

When `AuthenticationInterceptor` detects an expired credential and
begins a refresh cycle, pending adapt operations are queued in
`mutableState.adaptOperations` and pending retry completions in
`mutableState.requestsToRetry`. If the credential refresh fails
permanently (e.g., refresh token revoked), `handleRefreshFailure`
calls `requestsToRetry.forEach { $0(.doNotRetryWithError(error)) }` and
`adaptOperations.forEach { $0.completion(.failure(error)) }` to drain
the queues, but the underlying `URLSessionTask`s associated with those
requests are never explicitly cancelled. This leaves zombie tasks
consuming connections until the session or task timeout fires. Cancel
the underlying tasks for all queued requests when credential refresh
fails in `Source/Features/AuthenticationInterceptor.swift`.

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

### N6: Fix `JSONParameterEncoder` always encoding parameters into the request body

`JSONParameterEncoder` in `Source/Core/ParameterEncoder.swift` always
places encoded parameters into `request.httpBody` regardless of HTTP
method. Unlike `URLEncodedFormParameterEncoder`, which has a
`methodDependent` `Destination` that places GET/HEAD/DELETE parameters
in the URL query string and other methods in the body, there is no
equivalent on `JSONParameterEncoder`. When callers use
`JSONParameterEncoder` with GET requests and a server expects
JSON-encoded parameters in the URL query string (a pattern used by
some GraphQL and search APIs), they must manually construct the URL.
Add a `Destination` enum to `JSONParameterEncoder` with cases
`.httpBody` (default, preserves current behaviour) and `.queryString`
(percent-encodes the JSON string and appends it as the `query`
component of the URL), matching the structure of
`URLEncodedFormParameterEncoder.Destination`.

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

### N9: Fix `MultipartFormData` silently dropping extensionless files

The convenience overload `append(_ fileURL: URL, withName name: String)`
in `Source/Features/MultipartFormData.swift` calls
`setBodyPartError(withReason: .bodyPartFilenameInvalid(in: fileURL))`
when `fileURL.pathExtension` is empty. This silently drops files whose
names have no extension (common in Unix/Linux environments, e.g.,
named pipes, Dockerfile, Makefile). The private `mimeType(forPathExtension:)`
helper already returns `"application/octet-stream"` when no UTType is
found for a given extension. Relax the guard so that an empty
`pathExtension` still appends the body part using
`"application/octet-stream"` as the MIME type rather than recording
a `bodyPartFilenameInvalid` error.

### N10: Refactor `DownloadRequest.resumeData` platform conditional for readability

In `Source/Core/DownloadRequest.swift`, the `resumeData` computed
property uses `#if !canImport(FoundationNetworking)` (a double-negative)
to select the Apple-platform branch, which includes the
`error?.downloadResumeData` fallback for extracting resume data from a
`URLError`'s userInfo. The `#else` branch handles Linux where
`downloadResumeData` is unavailable (gated by `canImport(Security)` in
`AFError`). The double-negative condition makes it easy to misread
which platform gets which behavior. Restructure the conditional to use
the positive form `#if canImport(FoundationNetworking)` for the Linux
branch first, with the Apple `#else` branch containing the
`downloadResumeData` fallback second, so the platform intent is
immediately clear without changing runtime behavior.

## Medium

### M1: Implement certificate transparency verification

Add Certificate Transparency (CT) verification to the `ServerTrustEvaluating`
system. Verify that the server's certificate has valid Signed Certificate
Timestamps (SCTs) from recognized CT logs. Support both embedded SCTs
(in the certificate) and SCTs delivered via TLS extension. Add a
`CertificateTransparencyEvaluator` that composes with existing trust
evaluators. Update `.jazzy.yaml` to add the new
`CertificateTransparencyEvaluator` to a dedicated "Security" custom
category so it appears alongside `ServerTrustEvaluation` in the
generated API docs. Add a "Certificate Transparency" section to
`Documentation/AdvancedUsage.md` explaining how to enable and
configure CT verification.

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
that use the platform's native TLS stack. Update
`.github/workflows/ci.yml` to add Linux (Ubuntu 22.04) and Windows
(Server 2022) runners to the CI matrix alongside existing macOS jobs.
Update `Package.swift` platform specifications to document Linux and
Windows as supported platforms in code comments.

### N11: Fix `.jazzy.yaml` generating flat symbol list without category groupings

The `.jazzy.yaml` configuration specifies `xcodebuild_arguments` with
the `Alamofire iOS` scheme and `Alamofire.xcworkspace`, but the project
has moved to SPM-based builds via `Package.swift`. The generated API
documentation at `https://alamofire.github.io/Alamofire/` renders all
public symbols in a single flat list without logical grouping. Update
`.jazzy.yaml` to use `--spm-module Alamofire` instead of
`xcodebuild_arguments`, add custom category groupings for
`Authentication/`, `ServerTrustEvaluation`, `RequestInterceptor`,
`ResponseSerialization`, and `EventMonitor` so related symbols
appear together. Fix the `root_url` to use the current GitHub Pages
path and update the `theme` from `fullwidth` to `apple` for
consistency with modern Jazzy output.

### M11: Update `CONTRIBUTING.md` and CI workflow for contributor onboarding

The `CONTRIBUTING.md` references the Firewalk test server for running
automated tests but does not include Docker-based setup instructions,
making it difficult for new contributors to run the test suite. Add a
"Docker Test Setup" section to `CONTRIBUTING.md` with step-by-step
instructions for starting Firewalk in a container. Update
`.github/workflows/ci.yml` to add a SwiftFormat lint job that checks
all Swift sources against the rules in `.swiftformat` (currently
`--swiftversion 6.0`, `--commas inline`, `--extensionacl
on-declarations`, etc.) and fails on formatting violations. Add a
new `.github/PULL_REQUEST_TEMPLATE.md` checklist item requiring
contributors to confirm that public API changes include corresponding
updates to `Documentation/Usage.md` or
`Documentation/AdvancedUsage.md`.

### W11: Overhaul `Documentation/` guides and release configuration

The `Documentation/Usage.md` (1087 lines) and
`Documentation/AdvancedUsage.md` (1551 lines) cover the current
synchronous callback API thoroughly but lack async/await examples
that reflect modern Swift usage patterns. Add async/await code
examples to every major section in `Usage.md` (requests, downloads,
uploads, streaming) alongside the existing callback examples.
Create `Documentation/Alamofire 5.12 Migration Guide.md` following
the format of existing migration guides (`Alamofire 5.0 Migration
Guide.md`, etc.) documenting API changes since 5.11. Update
`CHANGELOG.md` to add a template section for the next release with
proper semantic versioning headers matching the existing format
(the changelog tracks releases from 3.x through 5.11.x). Update
`Alamofire.podspec` to align `deployment_target` values with those
in `Package.swift` (iOS 12, macOS 10.13, tvOS 12, watchOS 4) since
the podspec currently declares iOS 10.0 and macOS 10.12. Update
`README.md` to refresh the Quick Start section and CI badge URLs
to point to the current `ci.yml` workflow.
