# square/retrofit

| Field | Value |
|-------|-------|
| **URL** | https://github.com/square/retrofit |
| **License** | Apache-2.0 |
| **Language** | Java |
| **Scale** | Large |
| **Category** | HTTP client |
| **Set** | Cutoff |
| **Commit** | `4a60aef50e8cc2a323ea6b095b35abaa696d2c67` |

## Why this repo

- **Well-structured**: Core library under `retrofit/src/main/java/retrofit2/`
  with clear separation — interface-to-HTTP adaptation in `Retrofit.java`,
  annotation processing in `RequestFactory.java`, parameter encoding in
  `ParameterHandler.java`, OkHttp integration in `OkHttpCall.java`,
  request construction in `RequestBuilder.java`, type-safe call adapters
  (`CallAdapter.java`, `DefaultCallAdapterFactory.java`), converter SPI
  (`Converter.java`), and HTTP annotations in the `http/` sub-package.
  Adapter modules for RxJava/RxJava2/RxJava3/Guava/Scala and converter
  modules for Gson/Jackson/Moshi/Protobuf/Wire/JAXB/Scalars/etc.
- **Rich history**: 3K+ commits, 43K+ stars. The dominant Android/Java
  HTTP client library. PRs cover annotation processing, type resolution,
  adapter extensions, and OkHttp integration.
- **Permissive**: Apache-2.0 license.

## Structure overview

```
retrofit/src/main/java/retrofit2/
├── Retrofit.java                    # Core: create() proxy, builder, factory resolution
├── RequestFactory.java              # Annotation parser — extracts HTTP method, URL, params
├── RequestBuilder.java              # Builds OkHttp Request from parsed annotations
├── ParameterHandler.java            # Per-parameter encoding — Path, Query, Header, Body, etc.
├── OkHttpCall.java                  # Call<T> implementation wrapping OkHttp Call
├── HttpServiceMethod.java           # Bridge: ServiceMethod → adapted Call with converter
├── ServiceMethod.java               # Abstract service method — parseAnnotations entry point
├── Call.java                        # Call<T> interface — execute, enqueue, cancel
├── CallAdapter.java                 # CallAdapter<R,T> interface + Factory
├── Callback.java                    # Async callback interface — onResponse, onFailure
├── Converter.java                   # Converter<F,T> interface + Factory
├── Response.java                    # Response<T> wrapper — body, errorBody, headers
├── HttpException.java               # HTTP error exception with response
├── Invocation.java                  # Method invocation metadata for interceptors
├── Platform.java                    # Platform detection (Android, Java, etc.)
├── Utils.java                       # Type resolution utilities — parameterized types, etc.
├── Reflection.java                  # Reflective access helpers
├── DefaultCallAdapterFactory.java   # Default Call adapter — sync/async execution
├── CompletableFutureCallAdapterFactory.java # Java 8+ CompletableFuture adapter
├── BuiltInConverters.java           # Built-in converters — Void, Unit, Optional, ResponseBody
├── BuiltInFactories.java            # Built-in adapter/converter factory registration
├── OptionalConverterFactory.java    # Java 8 Optional converter
├── SkipCallbackExecutor.java        # Annotation to skip callback executor
├── SkipCallbackExecutorImpl.java    # SkipCallbackExecutor implementation
├── DefaultMethodSupport.java        # Java 8+ default method support
├── AndroidMainExecutor.java         # Android main thread executor
├── http/
│   ├── GET.java, POST.java, PUT.java, DELETE.java, PATCH.java, HEAD.java, OPTIONS.java
│   ├── HTTP.java                    # Custom HTTP method annotation
│   ├── Path.java                    # URL path parameter annotation
│   ├── Query.java, QueryName.java, QueryMap.java  # Query parameter annotations
│   ├── Header.java, HeaderMap.java, Headers.java   # Header annotations
│   ├── Field.java, FieldMap.java    # Form field annotations
│   ├── Part.java, PartMap.java      # Multipart part annotations
│   ├── Body.java                    # Request body annotation
│   ├── Url.java                     # Dynamic URL annotation
│   ├── FormUrlEncoded.java          # Form encoding marker
│   ├── Multipart.java              # Multipart encoding marker
│   ├── Streaming.java              # Streaming response marker
│   └── Tag.java                     # OkHttp tag annotation
retrofit-adapters/
├── guava/                           # Guava ListenableFuture adapter
├── rxjava/                          # RxJava 1.x Observable/Single adapter
├── rxjava2/                         # RxJava 2.x Observable/Single/Completable adapter
├── rxjava3/                         # RxJava 3.x Observable/Single/Completable adapter
├── java8/                           # Java 8 CompletableFuture adapter (deprecated)
└── scala/                           # Scala Future adapter
retrofit-converters/
├── gson/                            # Gson JSON converter
├── jackson/                         # Jackson JSON converter
├── moshi/                           # Moshi JSON converter
├── protobuf/                        # Protocol Buffers converter
├── wire/                            # Wire Protocol Buffers converter
├── jaxb/                            # JAXB XML converter
├── jaxb3/                           # Jakarta JAXB XML converter
├── simplexml/                       # SimpleXML converter
├── scalars/                         # Scalar (String, primitives) converter
├── guava/                           # Guava Optional converter
└── kotlinx-serialization/           # Kotlin serialization converter
retrofit-mock/                       # Mock adapter for testing
```

## Scale indicators

- ~26 Java source files in core `retrofit2/` + ~20 annotation files in `http/`
- ~5.1K lines of code (core)
- ~14.6K lines total across all modules
- Multi-module Gradle project (core + adapters + converters + mock)
- Key dependency: OkHttp 3/4

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add request URL logging to Invocation metadata

The `Invocation` class in `Invocation.java` captures the method,
interface, and arguments of a service call but does not include the
resolved URL. Interceptors using `Invocation` from the request tag
cannot determine the target URL without re-parsing annotations. Add
a `relativeUrl()` method to `Invocation` and populate it from
`RequestFactory.create()` during request building.

### N2: Fix RequestFactory not validating duplicate HTTP method annotations

The `RequestFactory.Builder` in `RequestFactory.java` processes method
annotations sequentially but does not check for duplicate HTTP method
annotations (e.g., a method annotated with both `@GET` and `@POST`).
The second annotation silently overwrites the first. Add a check in
`parseMethodAnnotation()` that throws `IllegalArgumentException` when
multiple HTTP method annotations are present.

### N3: Add timeout configuration per service method

The `OkHttpCall` in `OkHttpCall.java` uses the `Call.Factory`'s
default timeouts for all requests. Add a `@Timeout` annotation in the
`http/` package that allows per-method timeout configuration
(connect, read, write). Parse the annotation in `RequestFactory.Builder`
and apply it in `OkHttpCall` by creating a cloned `OkHttpClient`
with the specified timeouts.

### N4: Fix ParameterHandler.QueryMap not URL-encoding keys

The `QueryMap` handler in `ParameterHandler.java` encodes query
parameter values via the `Converter<T, String>` but passes map keys
directly to `RequestBuilder.addQueryParam()` without URL-encoding.
Keys containing special characters (e.g., `filter[name]`) produce
malformed URLs. Apply URL encoding to keys in `QueryMap.apply()`,
consistent with how `Query` handles values.

### N5: Add response header access to HttpException

The `HttpException` class in `HttpException.java` wraps a
`Response<?>` and exposes `code()`, `message()`, and `response()`,
but does not provide direct access to response headers. Callers must
extract the response and navigate to headers. Add a `headers()` method
that returns the `okhttp3.Headers` from the underlying response for
convenient access in error handlers.

### N6: Fix CompletableFutureCallAdapterFactory not propagating cancellation

The `CompletableFutureCallAdapterFactory` in
`CompletableFutureCallAdapterFactory.java` wraps `Call<T>` in a
`CompletableFuture`, but canceling the future does not cancel the
underlying OkHttp call. Add a `whenComplete` handler that calls
`call.cancel()` when the future is canceled, preventing leaked
connections.

### N7: Add @Headers validation for malformed header strings

The `@Headers` annotation in `http/Headers.java` accepts string
arrays for static headers, but `RequestFactory.Builder` in
`RequestFactory.java` splits headers on `:` without validating the
format. A header string without a colon (e.g., `"BearerToken123"`)
produces an empty header name. Add validation in
`parseHeaders()` that throws `IllegalArgumentException` for
malformed header strings.

### N8: Fix OkHttpCall.clone() not resetting execution state

The `OkHttpCall.clone()` method in `OkHttpCall.java` creates a new
`OkHttpCall` instance but shares the `RequestFactory` args array
reference. If either the original or clone modifies the args (which
are final), behavior is correct. However, the `executed` and
`canceled` volatile flags are not explicitly reset on the clone —
they start `false` because `clone()` creates a new instance. Document
this behavior and add a test, or defensively copy mutable state.

### N9: Add converter selection tracing for debugging

When `Retrofit.responseBodyConverter()` iterates through registered
`Converter.Factory` instances in `Retrofit.java`, there is no
visibility into which factories were tried and why they returned
`null`. Add a logging mode (enabled via `Retrofit.Builder.debug(true)`)
that logs each factory's name and whether it provided a converter,
helping developers diagnose converter registration issues.

### N10: Fix Utils.getParameterUpperBound not handling recursive type variables

The `Utils.getParameterUpperBound()` method in `Utils.java` resolves
the upper bound of wildcard types in parameterized types (e.g.,
`Call<? extends Foo>`), but does not handle recursive type variables
where a type variable's bound references itself. This can cause
`StackOverflowError` in edge cases with complex generic hierarchies.
Add cycle detection to `getParameterUpperBound()` and
`resolve()`.

### M1: Implement request/response interceptor annotations

Add `@Intercept` method-level and interface-level annotations that
specify interceptor classes to apply to specific service methods.
Interceptors registered via annotations are added to the per-method
OkHttp client clone. Requires a new `@Intercept` annotation in `http/`,
annotation processing in `RequestFactory.Builder`, interceptor
instantiation in `HttpServiceMethod`, and OkHttp client cloning in
`OkHttpCall`.

## Medium

### M2: Add retry policy support with backoff configuration

Implement a `@Retry` annotation that configures automatic retry for
failed requests. Support configurable max attempts, exponential
backoff with jitter, retryable status codes, and retryable exception
types. Requires a new `@Retry` annotation in `http/`, annotation
parsing in `RequestFactory.Builder`, retry logic wrapping in
`HttpServiceMethod.adapt()`, and integration with `OkHttpCall` for
request re-execution.

### M3: Implement response caching with converter-aware serialization

Add a `CachingCallAdapterFactory` that caches responses in memory
or disk, keyed by request URL and parameters. Support TTL
configuration, cache invalidation via `@CacheControl` annotations,
and converter-specific serialization for cached bodies. Requires a
new adapter factory, a cache store abstraction, integration with
`Retrofit.Builder`, and annotation processing in `RequestFactory`.

### M4: Add multipart upload progress tracking

The `ParameterHandler.Part` and `RawPart` handlers in
`ParameterHandler.java` create multipart request bodies but provide
no upload progress feedback. Implement a `ProgressRequestBody`
wrapper that intercepts `writeTo()` and reports progress via a
callback. Add a `@Progress` parameter annotation that accepts a
progress listener. Requires changes to `ParameterHandler` for the
new handler, `RequestBuilder` for body wrapping, `RequestFactory`
for annotation parsing, and a new `ProgressListener` interface.

### M5: Implement typed error body conversion

When a request returns an error response (4xx/5xx), `Response.errorBody()`
returns a raw `ResponseBody`. Implement automatic error body conversion
using the registered converter factories. Add a `@ErrorBody(Type.class)`
annotation or a `Retrofit.Builder.errorConverter()` option that
specifies the error body type. Parse the error body in
`HttpServiceMethod.adapt()` and include the typed error in
`HttpException`. Requires changes to `HttpServiceMethod`,
`Retrofit.Builder`, `HttpException`, and `Response`.

### M6: Add request deduplication for identical in-flight calls

When multiple callers make identical requests simultaneously, each
creates a separate OkHttp call. Implement request deduplication in a
new `DeduplicatingCallAdapterFactory` that groups identical in-flight
requests (same method, URL, headers, body) and shares the response.
Requires a new adapter factory, a request identity key computation,
concurrent call tracking with `ConcurrentHashMap`, and cleanup on
completion/cancellation.

### M7: Implement service method mocking without retrofit-mock

The `retrofit-mock/` module provides mock adapters but requires a
separate dependency. Add built-in mock support to the core module
via `Retrofit.Builder.mock(Class, Object)` that registers mock
implementations for service interfaces. Mock responses should support
delays, error simulation, and response sequencing. Requires changes
to `Retrofit.Builder`, `Retrofit.create()` for mock delegation, and
a `MockResponse` builder class.

### M8: Add request batching for multiple service calls

Implement request batching where multiple `Call<T>` instances are
combined into a single HTTP request (for APIs that support batch
endpoints). Add a `@Batch` annotation and a `BatchCall<T>` type that
collects individual calls and executes them as a single request.
Requires a new `@Batch` annotation, a `BatchCallAdapter` factory,
batch request serialization in `RequestBuilder`, and response
demultiplexing in `OkHttpCall`.

### M9: Implement service interface versioning support

Add API versioning support via `@ApiVersion("v2")` interface-level
annotations that automatically prefix all endpoint URLs with the
version. Support version inheritance in interface hierarchies and
per-method version overrides via `@ApiVersion` on methods. Requires
a new annotation in `http/`, version resolution in
`RequestFactory.Builder`, URL prefix construction in
`RequestBuilder`, and `Retrofit.Builder.defaultApiVersion()` for
global defaults.

### M10: Add request/response logging with configurable detail levels

Implement built-in request/response logging as a call adapter that
wraps `OkHttpCall`. Support detail levels: NONE, BASIC (method, URL,
status, timing), HEADERS (+ headers), BODY (+ body content).
Configure via `Retrofit.Builder.logLevel()`. Requires a new
`LoggingCallAdapterFactory`, body buffering for logging without
consuming the stream, sensitive header redaction, and integration
with Java `java.util.logging` or SLF4J.

## Wide

### W1: Implement code generation for service interfaces

Add an annotation processor that generates concrete implementations
of service interfaces at compile time, eliminating runtime reflection
and dynamic proxies. Generate type-safe `RequestFactory` instances,
inlined parameter handlers, and direct converter calls. Support
incremental compilation and source-level error reporting. Changes
span a new `retrofit-compiler/` module, annotation processing for
all `http/` annotations, code generation for `RequestFactory` and
`ParameterHandler`, `Retrofit.create()` fallback to generated classes,
and build system integration.

### W2: Add reactive streams support with backpressure

Implement native Reactive Streams (`java.util.concurrent.Flow`)
support without requiring RxJava. Add a `FlowCallAdapterFactory`
that adapts `Call<T>` to `Flow.Publisher<Response<T>>`, support
backpressure-aware streaming for large response bodies, and implement
`Flow.Processor` for request/response transformation chains. Changes
span a new `retrofit-adapters/flow/` module, a `FlowCallAdapter`
factory, streaming body conversion in `BuiltInConverters`,
backpressure integration with OkHttp response streams, and
`Retrofit.Builder` registration.

### W3: Implement client-side load balancing and failover

Add client-side load balancing that distributes requests across
multiple base URLs. Support round-robin, weighted, and least-
connections strategies. Include health checking, circuit breaker
per endpoint, automatic failover on connection errors, and
configurable retry policies per endpoint. Changes span a new
`retrofit-loadbalancer/` module, `Retrofit.Builder.baseUrls()` for
multiple endpoints, request routing in `OkHttpCall`, health check
scheduling, `CallAdapter` integration for retry/failover, and
`Converter` integration for health check response parsing.

### W4: Add comprehensive testing framework

Implement a testing module that provides mock server integration,
response recording/replay, request assertion builders, and service
stub generators. Include a JUnit 5 extension (`@RetrofitTest`) that
auto-configures a mock server and injects service instances. Support
response sequencing, conditional responses, and request verification.
Changes span a new `retrofit-testing/` module, `MockWebServer`
integration, response cassette recording in `OkHttpCall`, request
matchers for verification, JUnit extension implementation, and
assertion DSL for request/response validation.

### W5: Implement request signing and authentication framework

Add a pluggable authentication framework that goes beyond OkHttp
interceptors. Support OAuth 2.0 (authorization code, client
credentials, PKCE), AWS Signature V4, HMAC signing, and custom
authentication schemes. Include token storage, automatic refresh,
and per-endpoint authentication configuration. Changes span a new
`retrofit-auth/` module, `@Authenticated` annotation in `http/`,
annotation processing in `RequestFactory`, auth provider resolution
in `HttpServiceMethod`, token management with `CallAdapter`
integration, and `Retrofit.Builder.authenticator()` registration.

### W6: Add gRPC-to-REST bridge for protobuf services

Implement a converter and adapter that allows defining gRPC-style
service interfaces using Retrofit annotations and automatically
handling protobuf serialization, gRPC status codes, server streaming,
and metadata propagation over HTTP/2. Support both unary and
server-streaming RPCs. Changes span a new `retrofit-grpc/` module,
protobuf `Converter.Factory` integration, streaming `CallAdapter`
for server push, gRPC status-to-HTTP status mapping, metadata-to-
header conversion, and `Retrofit.Builder` configuration for gRPC
transport.

### W7: Implement API contract validation and documentation generation

Add a module that validates service interface definitions against
an OpenAPI specification and generates API documentation from
annotated interfaces. Detect mismatches between the declared interface
and the spec (missing endpoints, type mismatches, undocumented
parameters). Generate OpenAPI specs from interfaces. Changes span a
new `retrofit-openapi/` module, annotation scanning for all `http/`
annotations, type-to-schema conversion in `Utils`, validation engine
comparing interfaces to specs, spec generation from `RequestFactory`
metadata, and Gradle/Maven plugin integration.

### W8: Add request pipeline with transformation and validation

Implement a request/response pipeline that supports pluggable stages:
request validation (schema-based), request transformation (header
injection, URL rewriting), response validation, and response
transformation (envelope unwrapping, error normalization). Support
pipeline configuration per-interface and per-method. Changes span a
new pipeline abstraction, `Retrofit.Builder.pipeline()` configuration,
`HttpServiceMethod` integration for pipeline execution, `OkHttpCall`
for pipeline stage wrapping, `ParameterHandler` for validation stage
parameter access, and `Converter` integration for typed validation.

### W9: Implement multi-protocol support with WebSocket and SSE

Extend Retrofit to support WebSocket and Server-Sent Events endpoints
alongside REST. Add `@WebSocket` and `@SSE` method annotations, return
types for streaming connections (`WebSocketSession<T>`,
`EventSource<T>`), and lifecycle management (connect, message, close
callbacks). Changes span new annotations in `http/`, annotation
parsing in `RequestFactory`, new call adapters for streaming protocols,
OkHttp WebSocket integration in `OkHttpCall`, SSE response parsing,
converter integration for message types, and `Retrofit.Builder`
configuration.

### W10: Add compile-time annotation validation with error reporting

Implement a compile-time annotation processor that validates service
interface definitions before runtime. Check for common mistakes:
missing `@Body` on POST without `@FormUrlEncoded`, `@Path` parameters
not in URL template, conflicting annotations (`@Query` + `@QueryMap`
on same parameter), return type compatibility with registered adapters,
and `@GET` with `@Body`. Report errors with source locations.
Changes span a new `retrofit-validator/` annotation processor module,
validation rules for all `http/` annotations, type checking utilities
mirroring `Utils.java`, error message formatting, compiler plugin
integration, and test infrastructure for compile-time testing.
