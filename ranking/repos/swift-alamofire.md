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

8 tasks (3 narrow, 3 medium, 2 wide) for the Swift HTTP networking library.

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
