# Moya/Moya

| Field | Value |
|-------|-------|
| **URL** | https://github.com/Moya/Moya |
| **License** | MIT |
| **Language** | Swift |
| **Scale** | Small |
| **Category** | Network abstraction layer |
| **Set** | Cutoff |

## Why this repo

- **Well-structured**: Clean protocol-oriented architecture —
  `TargetType` (defines API endpoints: `baseURL`, `path`, `method`,
  `task`, `headers`, `sampleData`), `Endpoint` (reified target with
  URL, method, task, sample response, and `urlRequest()` conversion),
  `MoyaProvider` (configurable request pipeline with `EndpointClosure`,
  `RequestClosure`, `StubClosure`, plugin support, and inflight
  request tracking), `Response` (status code, data, request, response),
  `MoyaError` (typed error hierarchy), `Task` (request body variants:
  plain, data, JSON, parameters, multipart, upload, download),
  `PluginType` (prepare, willSend, didReceive, process hooks), four
  built-in plugins (`NetworkLoggerPlugin`, `NetworkActivityPlugin`,
  `CredentialsPlugin`, `AccessTokenPlugin`), reactive extensions
  (`RxMoya`, `CombineMoya`, `ReactiveMoya`), and Alamofire integration.
- **Rich history**: 15K+ stars, 1.8K+ commits. The standard
  network abstraction layer for Swift/iOS, used extensively in
  iOS/macOS applications. Issues span stubbing edge cases, plugin
  ordering, multipart encoding, Combine integration, and error
  mapping.
- **Permissive**: MIT licensed.

## Structure overview

```
Moya/
├── Package.swift                                      # Swift Package Manager manifest
├── Moya.podspec                                       # CocoaPods podspec
├── Sources/
│   ├── Moya/
│   │   ├── MoyaProvider.swift                         # Core provider — EndpointClosure, RequestClosure, StubClosure, request()
│   │   ├── MoyaProvider+Internal.swift                # Internal request logic — requestNormal, sendRequest, stubRequest
│   │   ├── MoyaProvider+Defaults.swift                # Default closures — defaultEndpointMapping, defaultRequestMapping
│   │   ├── TargetType.swift                           # TargetType protocol — baseURL, path, method, task, headers, sampleData
│   │   ├── Endpoint.swift                             # Endpoint class — url, method, task, sampleResponseClosure, urlRequest()
│   │   ├── Response.swift                             # Response class — statusCode, data, request, response, filter, map, mapJSON
│   │   ├── MoyaError.swift                            # Error enum — imageMapping, jsonMapping, statusCode, underlying, etc.
│   │   ├── Task.swift                                 # Task enum — requestPlain, requestData, requestJSONEncodable, uploadMultipart, etc.
│   │   ├── Plugin.swift                               # PluginType protocol — prepare, willSend, didReceive, process
│   │   ├── ValidationType.swift                       # ValidationType — none, successCodes, successAndRedirectCodes, customCodes
│   │   ├── Cancellable.swift                          # Cancellable protocol + CancellableToken implementation
│   │   ├── MultiTarget.swift                          # MultiTarget — type-erased TargetType for mixed-type providers
│   │   ├── MultipartFormData.swift                    # MultipartFormData — provider, name, fileName, mimeType
│   │   ├── AnyEncodable.swift                         # Type-erased Encodable wrapper
│   │   ├── Atomic.swift                               # @Atomic property wrapper for thread-safe access
│   │   ├── Image.swift                                # Platform image type alias (UIImage/NSImage)
│   │   ├── Moya+Alamofire.swift                       # Alamofire integration — Method, Session, Request extensions
│   │   ├── URL+Moya.swift                             # URL extension for Moya
│   │   ├── URLRequest+Encoding.swift                  # URLRequest encoding extensions
│   │   ├── RequestTypeWrapper.swift                   # RequestType protocol wrapper over Alamofire Request
│   │   └── Plugins/
│   │       ├── NetworkLoggerPlugin.swift              # Logs request/response with configurable verbosity
│   │       ├── NetworkActivityPlugin.swift            # Notifies begin/end of network activity (for indicators)
│   │       ├── CredentialsPlugin.swift                # Adds URL credentials to requests
│   │       └── AccessTokenPlugin.swift                # Adds Bearer/Basic token from AccessTokenAuthorizable
│   ├── CombineMoya/
│   │   ├── MoyaProvider+Combine.swift                 # Combine publisher for MoyaProvider.requestPublisher
│   │   ├── MoyaPublisher.swift                        # MoyaPublisher — Combine Publisher wrapping Cancellable
│   │   └── Publisher+Response.swift                   # Combine operators — filterSuccessfulStatusCodes, mapJSON, etc.
│   ├── RxMoya/
│   │   ├── MoyaProvider+Rx.swift                      # RxSwift extension — rx.request returning Single<Response>
│   │   ├── Observable+Response.swift                  # Observable operators — filterSuccessfulStatusCodes, mapJSON
│   │   └── Single+Response.swift                      # Single operators — filterSuccessfulStatusCodes, mapJSON
│   └── ReactiveMoya/
│       ├── MoyaProvider+Reactive.swift                # ReactiveSwift extension — reactive.request
│       └── SignalProducer+Response.swift              # SignalProducer operators — filterSuccessfulStatusCodes, mapJSON
├── Tests/
│   └── MoyaTests/
│       ├── MoyaProviderSpec.swift                     # Provider specs — request, stub, plugin, cancellation
│       ├── MoyaProviderIntegrationTests.swift         # Integration tests with live HTTP
│       ├── EndpointSpec.swift                         # Endpoint construction and URL request conversion specs
│       ├── EndpointClosureSpec.swift                  # Endpoint closure customization specs
│       ├── ErrorTests.swift                           # MoyaError specs
│       ├── NetworkLoggerPluginSpec.swift              # Logger plugin specs
│       ├── MultipartFormDataSpec.swift                # Multipart form data specs
│       ├── MoyaProvider+RxSpec.swift                  # RxSwift extension specs
│       ├── MoyaProvider+CombineSpec.swift             # Combine extension specs
│       └── ...                                        # Additional test files
└── Examples/                                          # Example projects
```

## Scale indicators

- ~32 Swift source files in Sources/, ~21 test files
- ~9,500 lines total
- Swift Package Manager + CocoaPods distribution
- Dependencies: Alamofire (HTTP backend), RxSwift/ReactiveSwift/Combine (optional reactive extensions)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix MoyaProvider inflight request tracking not removing entries after completion

In `Sources/Moya/MoyaProvider.swift`, `internalInflightRequests` is
a dictionary of `[Endpoint: [Moya.Completion]]` guarded by `@Atomic`.
When `trackInflights` is enabled, completed requests should be removed
from the dictionary. In `MoyaProvider+Internal.swift`, the completion
handler removes the endpoint key, but if a second request for the
same endpoint is added between the response arriving and the
completion callback executing, the removal erases the new request's
callback. Fix the removal logic to only remove the specific callback
instead of the entire endpoint entry.

### N2: Fix Endpoint.urlRequest() crashing for requestCompositeParameters with URLEncoding.default

In `Sources/Moya/Endpoint.swift`, the `urlRequest()` method handles
`.requestCompositeParameters(bodyParameters:bodyEncoding:urlParameters:)`.
It contains a `fatalError` when `bodyParameterEncoding` is a
`URLEncoding` with a destination other than `.httpBody`. However, the
error message is misleading — it says "prohibited" for `URLEncoding.default`
which uses `.methodDependent`, but for GET requests `methodDependent`
maps to query string, which is valid. Fix the guard to only reject
non-httpBody URLEncoding for methods that use body encoding (POST,
PUT, PATCH).

### N3: Fix CancellableToken not actually cancelling the underlying Alamofire request

In `Sources/Moya/Cancellable.swift`, `CancellableToken` stores a
closure `cancelAction` and a `lock` for thread safety. The `cancel()`
method sets `isCancelled = true` and executes `cancelAction`. However,
in `MoyaProvider+Internal.swift`, the `CancellableToken` is created
with an empty closure initially and later updated with the actual
Alamofire request's cancel. If `cancel()` is called before the
request is assigned, the empty closure executes and `isCancelled`
is set, but the actual request proceeds. Fix to store the request
reference atomically and cancel it even if assigned after `cancel()`.

### N4: Fix NetworkLoggerPlugin not redacting sensitive headers in log output

In `Sources/Moya/Plugins/NetworkLoggerPlugin.swift`, the plugin logs
request and response headers. The `Configuration` struct has a
`logOptions` set but no mechanism to redact sensitive headers like
`Authorization`, `Cookie`, or `Set-Cookie`. Fix by adding a
`sensitiveHeaders: Set<String>` configuration option and replacing
their values with `[REDACTED]` in the log output.

### N5: Fix Response.filter(statusCodes:) not including the upper bound of the range

In `Sources/Moya/Response.swift`, `filter(statusCodes: Range<Int>)`
uses Swift's `Range` (half-open interval), so
`filter(statusCodes: 200..<300)` correctly excludes 300. However,
the convenience method `filterSuccessfulStatusCodes()` uses
`200...299` (`ClosedRange`), and there is no overload accepting
`ClosedRange`. If a user calls `filter(statusCodes: 200...299)`,
Swift selects the `Range` overload via implicit conversion, which
silently excludes 299. Fix by adding an explicit
`filter(statusCodes: ClosedRange<Int>)` overload.

### N6: Fix AccessTokenPlugin not applying token when TargetType declares .custom authorization

In `Sources/Moya/Plugins/AccessTokenPlugin.swift`, the `prepare`
method checks if the target conforms to `AccessTokenAuthorizable`
and reads `authorizationType`. The plugin handles `.bearer` and
`.basic` cases, but the `.custom(String)` case is not handled —
custom authorization schemes are silently ignored and no
`Authorization` header is set. Fix `prepare` to handle `.custom`
by prepending the custom scheme string to the token value.

### N7: Fix MultiTarget losing the inner target's sampleData when stubbing

In `Sources/Moya/MultiTarget.swift`, `MultiTarget` conforms to
`TargetType` and forwards all properties to the wrapped `target`.
The `sampleData` property returns `target.sampleData`. However,
when `MoyaProvider<MultiTarget>` stubs a request, the `Endpoint`
is created via `endpointClosure` which uses `MultiTarget`'s
`sampleData`. If the `EndpointClosure` captures the `MultiTarget`
instance and the inner target is deallocated, the sample data
reference becomes invalid. Fix by eagerly capturing `sampleData`
in the `Endpoint`'s `sampleResponseClosure`.

### N8: Fix ProgressResponse.progress returning 0.0 for uploads with known content length

In `Sources/Moya/MoyaProvider.swift`, `ProgressResponse.progress`
returns `progressObject.fractionCompleted` only when
`totalUnitCount > 0`. For upload tasks, Alamofire may not set
`totalUnitCount` on the `Progress` object until the upload begins.
During the initial phase, `totalUnitCount` is 0 and `progress`
returns 0.0 even though `completedUnitCount` is incrementing.
Fix the `progress` computed property to also check
`completedUnitCount > 0` as a signal that progress tracking has
started.

### N9: Fix MoyaProvider+Combine publisher not completing on cancellation

In `Sources/CombineMoya/MoyaPublisher.swift`, the `MoyaPublisher`
wraps a `MoyaProvider` request into a Combine `Publisher`. When
the subscription is cancelled, the underlying `Cancellable` is
cancelled, but the publisher never sends a `.finished` completion.
Downstream subscribers waiting for completion hang indefinitely.
Fix the cancellation handler to send `.finished` to the subscriber
before cancelling the underlying request.

### N10: Fix CredentialsPlugin not being called for stubbed requests

In `Sources/Moya/Plugins/CredentialsPlugin.swift`, the plugin
implements `willSend` to add URL credentials. In
`MoyaProvider+Internal.swift`, when a request is stubbed, the
`willSend` plugin hook is called with a `RequestTypeWrapper`. However,
the `RequestTypeWrapper` for stubbed requests does not support
`authenticate(with:)` — calling it is a no-op. Fix the stub
request flow to apply credentials to the `URLRequest` directly
before creating the stub response.

## Medium

### M1: Implement request interceptor plugin for token refresh flow

Add a `TokenRefreshPlugin` that intercepts 401 responses,
refreshes the access token, and retries the original request.
Requires a new `Sources/Moya/Plugins/TokenRefreshPlugin.swift`,
`TokenRefreshDelegate` protocol for token refresh logic, retry
state management with retry count limit, thread-safe token
storage via `@Atomic`, integration with `process` plugin hook,
and tests.

### M2: Add response caching plugin with ETag and If-Modified-Since support

Implement `CachingPlugin` that caches responses by endpoint and
automatically sends conditional requests. Requires
`Sources/Moya/Plugins/CachingPlugin.swift`, `CacheStore` protocol
with in-memory default implementation, ETag and Last-Modified
header extraction, conditional request header injection via
`prepare`, cache hit/miss metrics, configurable per-endpoint TTL,
and tests.

### M3: Implement request batching for multiple concurrent API calls

Add `MoyaProvider.requestBatch(targets:)` that sends multiple
requests concurrently and returns all results together. Requires
extensions to `MoyaProvider.swift`, `BatchResponse` result type
with per-target results, `DispatchGroup`-based coordination,
partial failure handling (some succeed, some fail), Combine
publisher variant, and tests.

### M4: Add offline mode plugin with request queuing and replay

Implement `OfflineModePlugin` that queues requests when the
network is unavailable and replays them when connectivity
returns. Requires `Sources/Moya/Plugins/OfflineModePlugin.swift`,
`RequestQueue` for persistent request storage (Codable
serialization), network reachability monitoring, queue processing
on reconnection, configurable queue limit, and tests.

### M5: Implement structured concurrency support with async/await API

Add `async`/`await` support to `MoyaProvider`: `request(_ target:) async throws -> Response`,
`requestWithProgress(_ target:) -> AsyncStream<ProgressResponse>`.
Requires `Sources/Moya/MoyaProvider+Async.swift`,
`CheckedContinuation` wrapping of callback-based API, cancellation
propagation via `Task`, `AsyncStream` for progress, async plugin
hooks, and tests.

### M6: Add mock server for integration testing without network

Implement `MockMoyaServer` that serves stub responses via a local
HTTP server, allowing real network stack testing. Requires
`Sources/Moya/Testing/MockServer.swift`, `LocalHTTPServer` using
`NWListener`, route registration from `TargetType.sampleData`,
configurable latency simulation, response sequence support
(different responses for successive requests), and tests.

### M7: Implement request signing plugin for HMAC-based API authentication

Add `RequestSigningPlugin` that signs requests with HMAC-SHA256
using a shared secret. Requires
`Sources/Moya/Plugins/RequestSigningPlugin.swift`, canonical
request string construction (method, path, headers, body hash),
signature computation via `CryptoKit`, timestamp injection,
signed headers specification, nonce generation, and tests.

### M8: Add response mapping pipeline with typed decoders

Implement a `DecodableProvider<Target>` wrapper that adds typed
response decoding: `request(target, as: Model.self)` returning
`Result<Model, MoyaError>`. Requires
`Sources/Moya/DecodableProvider.swift`, `JSONDecoder` configuration
per target, `keyDecodingStrategy` support, error response
decoding for API error bodies, partial response handling,
Combine integration, and tests.

### M9: Implement request throttling with rate limiting

Add `ThrottlePlugin` that limits concurrent requests per endpoint
or globally, queuing excess requests. Requires
`Sources/Moya/Plugins/ThrottlePlugin.swift`, `Semaphore`-based
concurrency limiting, per-endpoint max concurrent configuration,
queue priority support, timeout for queued requests, and tests.

### M10: Add multipart upload progress with per-part tracking

Implement per-part progress tracking for multipart uploads:
`uploadMultipart(target, partProgress: (String, Double) -> Void)`.
Requires extending `MoyaProvider.swift` with part-aware progress,
`MultipartProgress` struct with part name and fraction,
Alamofire `MultipartFormData` integration for per-part byte
counting, aggregated total progress, and tests.

## Wide

### W1: Implement GraphQL support with typed queries and mutations

Build a GraphQL layer on top of Moya: `GraphQLTarget` protocol
extending `TargetType`, typed `Query<T>` and `Mutation<T>`
request builders, variable encoding, response parsing with
`errors` extraction, fragment support, and codegen-compatible
response types. Changes span `Sources/MoyaGraphQL/GraphQLTarget.swift`,
`Sources/MoyaGraphQL/Query.swift`, `Sources/MoyaGraphQL/Mutation.swift`,
`Sources/MoyaGraphQL/GraphQLResponse.swift`, `MoyaProvider`
extension for GraphQL execution, error mapping, and tests.

### W2: Implement WebSocket support through MoyaProvider

Add WebSocket communication via `MoyaProvider`: connection
lifecycle, message send/receive, automatic reconnection, and
plugin support for WebSocket events. Changes span
`Sources/MoyaWebSocket/WebSocketTarget.swift`,
`Sources/MoyaWebSocket/WebSocketProvider.swift`,
`Sources/MoyaWebSocket/WebSocketMessage.swift`,
`Sources/MoyaWebSocket/WebSocketPlugin.swift`,
Alamofire `WebSocketRequest` integration, Combine publisher
for message streams, heartbeat/ping-pong, and tests.

### W3: Implement end-to-end request tracing and diagnostics

Build comprehensive request tracing: timing for DNS, TCP,
TLS, server processing, response transfer; request/response
sizes; redirect following; certificate details. Changes span
`Sources/Moya/Plugins/DiagnosticsPlugin.swift`,
`Sources/Moya/Diagnostics/RequestTimeline.swift`,
`Sources/Moya/Diagnostics/ConnectionMetrics.swift`,
`URLSessionTaskMetrics` integration, structured log output,
aggregate statistics per endpoint, export to console/file, and tests.

### W4: Implement automatic API client generation from OpenAPI specs

Build a code generator that reads an OpenAPI 3.x spec and produces
`TargetType` conformances, request/response `Codable` models, and
`MoyaProvider` extensions. Changes span a command-line tool in
`Tools/MoyaGenerator/`, spec parser, `TargetType` template
generation, model generation from JSON Schema, enum generation
for path/query parameters, error model generation, generated test
stubs, and integration tests with sample specs.

### W5: Implement request replay and mock recording for testing

Build a VCR-style system that records network interactions and
replays them in tests: cassette files, request matching rules,
recording modes, and sensitive data filtering. Changes span
`Sources/MoyaRecorder/RecorderPlugin.swift`,
`Sources/MoyaRecorder/Cassette.swift`,
`Sources/MoyaRecorder/Matcher.swift`,
`Sources/MoyaRecorder/Recorder.swift`,
JSON/YAML cassette serialization, `MoyaProvider` integration
for playback mode, matching by URL/method/headers/body, and tests.

### W6: Implement certificate pinning plugin with HPKP support

Build certificate and public key pinning for Moya requests:
pin validation, backup pin support, pin failure reporting,
HPKP header enforcement. Changes span
`Sources/Moya/Plugins/CertificatePinningPlugin.swift`,
`Sources/Moya/Security/PinStore.swift`,
`Sources/Moya/Security/CertificateValidator.swift`,
Alamofire `ServerTrustManager` integration, TOFU (trust on
first use) mode, pin rotation support, failure analytics,
and tests with self-signed certificates.

### W7: Implement multi-environment configuration management

Build environment-aware API configuration: per-environment
base URLs, headers, and auth, with runtime switching. Changes
span `Sources/MoyaEnvironment/Environment.swift`,
`Sources/MoyaEnvironment/EnvironmentProvider.swift`,
`Sources/MoyaEnvironment/EnvironmentTarget.swift` wrapping
any `TargetType`, configuration loading from plist/JSON,
environment switching without provider recreation,
`MoyaProvider` convenience initializer, debug environment
overlay, and tests.

### W8: Implement request priority and queue management

Build a request queue with priority levels: critical, high,
normal, low, background. Configurable concurrent request limit,
priority-based scheduling, request cancellation by priority
group, and starvation prevention. Changes span
`Sources/MoyaPriority/PriorityProvider.swift`,
`Sources/MoyaPriority/RequestQueue.swift`,
`Sources/MoyaPriority/PriorityScheduler.swift`,
`Sources/MoyaPriority/QueueConfiguration.swift`,
`OperationQueue` integration, Combine publisher for queue
state, priority escalation, and tests.

### W9: Implement response transformation pipeline with middleware chain

Build a composable response transformation pipeline separate
from plugins: `ResponseMiddleware` protocol with `transform(_ response:) -> Response`,
chaining, conditional application by endpoint, and error
recovery middleware. Changes span
`Sources/MoyaTransform/ResponseMiddleware.swift`,
`Sources/MoyaTransform/MiddlewareChain.swift`,
`Sources/MoyaTransform/Middleware/DateDecodingMiddleware.swift`,
`Sources/MoyaTransform/Middleware/PaginationMiddleware.swift`,
`Sources/MoyaTransform/Middleware/ErrorMappingMiddleware.swift`,
`MoyaProvider` integration, Combine operators, and tests.

### W10: Implement reactive API testing framework with snapshot assertions

Build a testing framework for Moya-based API clients: snapshot
testing of request construction (URL, headers, body), response
stub builders, async expectation helpers, Combine test scheduler
integration, and test report generation. Changes span
`Sources/MoyaTesting/MoyaTestCase.swift`,
`Sources/MoyaTesting/SnapshotAssertions.swift`,
`Sources/MoyaTesting/StubBuilder.swift`,
`Sources/MoyaTesting/CombineTestScheduler.swift`,
`Sources/MoyaTesting/TestProvider.swift`,
request/response snapshot serialization, diff output for
mismatches, XCTest integration, and test examples.
