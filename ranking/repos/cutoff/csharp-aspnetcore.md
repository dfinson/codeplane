# dotnet/aspnetcore

| Field | Value |
|-------|-------|
| **URL** | https://github.com/dotnet/aspnetcore |
| **License** | MIT |
| **Language** | C# |
| **Scale** | Large |
| **Category** | Web framework |
| **Set** | Cutoff |
| **Commit** | `0b12e6f18f1f22a103d09d254c8579f9d5d47422` |

## Why this repo

- **Full-stack web framework**: ASP.NET Core spans the entire web stack
  — HTTP abstractions, routing, MVC, Razor Pages, Blazor components,
  SignalR real-time, minimal APIs, middleware pipeline, authentication,
  authorization, CORS, caching, health checks, OpenAPI, diagnostics,
  hosting, and server abstractions. Each module is a distinct project
  with clear boundaries.
- **Rich history**: 35K+ stars, maintained by Microsoft with extensive
  community contributions. Covers security fixes, performance
  optimizations, API design evolution, and cross-platform support.
- **Permissive**: MIT license.
- **Scale anchor**: Large codebase (10,000+ C# source files across
  50+ projects) anchoring the "Large" end of scale assessment.

## Structure overview

```
src/
├── Http/
│   ├── Http/src/                # DefaultHttpContext, FormCollection, HeaderDictionary
│   ├── Http.Abstractions/       # HttpContext, HttpRequest, HttpResponse interfaces
│   ├── Http.Features/           # Feature interfaces (IHttpRequestFeature, etc.)
│   ├── Http.Extensions/         # Extension methods, RequestDelegate helpers
│   ├── Http.Results/            # IResult implementations (Ok, NotFound, Json, etc.)
│   ├── Routing/src/             # Endpoint routing, route matching, link generation
│   ├── Routing.Abstractions/    # IEndpointRouteBuilder, RouteValueDictionary
│   ├── Headers/                 # Typed header parsing and generation
│   ├── WebUtilities/            # Query string helpers, multipart parsing
│   ├── Authentication.Abstractions/  # Auth schemes, claims, tickets
│   ├── Authentication.Core/     # AuthenticationService, handler infrastructure
│   └── Metadata/                # Endpoint metadata (attributes, conventions)
├── Mvc/
│   ├── Mvc.Core/src/            # Controllers, action selection, model binding,
│   │                            #   filters, formatters, routing integration
│   ├── Mvc.Abstractions/        # ActionResult, IActionFilter interfaces
│   ├── Mvc.ApiExplorer/         # API description for documentation generation
│   ├── Mvc.DataAnnotations/     # Validation attribute integration
│   ├── Mvc.Cors/                # CORS policy per-action/controller
│   └── Mvc.Formatters.Json/     # JSON input/output formatters
├── Middleware/
│   ├── CORS/src/                # CORS middleware and policy builder
│   ├── Diagnostics/src/         # Developer exception page, status code pages
│   ├── HttpLogging/src/         # HTTP request/response logging, W3C logging
│   ├── HttpsPolicy/             # HTTPS redirection middleware
│   ├── HostFiltering/           # Host header validation
│   ├── HttpOverrides/           # Forwarded headers middleware
│   ├── Localization/            # Request localization middleware
│   └── HealthChecks/            # Health check middleware and endpoints
├── Hosting/
│   ├── Hosting/                 # WebHost builder, startup, server integration
│   ├── Abstractions/            # IWebHost, IWebHostBuilder interfaces
│   ├── Server.Abstractions/     # IServer, IHttpApplication interfaces
│   └── TestHost/                # In-process test server
├── SignalR/
│   ├── common/                  # Protocol definitions, hub protocol
│   ├── clients/csharp/          # .NET SignalR client
│   ├── clients/ts/              # TypeScript SignalR client
│   └── clients/java/            # Java SignalR client
├── Components/                  # Blazor components, rendering, JSInterop
├── Identity/                    # ASP.NET Core Identity (users, roles, tokens)
├── OpenApi/                     # OpenAPI document generation
├── Caching/                     # Distributed caching (Redis, SQL Server)
├── Antiforgery/                 # Anti-CSRF token middleware
├── DataProtection/              # Data protection (encryption key management)
├── Grpc/                        # gRPC integration
└── DefaultBuilder/              # WebApplication.CreateBuilder() defaults
```

## Scale indicators

- 10,000+ C# source files across 50+ projects
- Deep hierarchy: HTTP stack → middleware → MVC → Blazor → SignalR
- Complex build system (MSBuild with shared SDK, source generators)
- Extensive cross-project dependencies

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add request body size limit validation to DefaultHttpContext

`DefaultHttpContext` in `src/Http/Http/src/DefaultHttpContext.cs` does
not enforce request body size limits by default. When
`MaxRequestBodySize` is configured on the server, the limit is checked
at the server layer but not surfaced in `HttpContext` for middleware
inspection. Add a `RequestBodySizeLimit` property to
`DefaultHttpContext` that exposes the configured limit and throws
`BadHttpRequestException` when the body exceeds it during buffered
reads.

### N2: Fix HeaderDictionary not validating header name characters

`HeaderDictionary` in `src/Http/Http/src/HeaderDictionary.cs` accepts
arbitrary string keys without validating that they conform to RFC 7230
token rules (no spaces, control characters, or delimiters). Setting
`headers["Invalid Header!"] = "value"` silently succeeds. Add
validation in the indexer setter that rejects non-token characters
with an `ArgumentException`.

### N3: Add ETag support to HttpResponse extension methods

The `Http.Results` module provides `Results.File()` and
`Results.Stream()` but does not support ETag-based conditional
responses. Add `Results.File()` overloads that accept an `EntityTagHeaderValue`
and check `If-None-Match` / `If-Match` request headers, returning
`304 Not Modified` when appropriate. Implement in
`src/Http/Http.Results/`.

### N4: Fix CorsPolicyBuilder not validating origin URL format

`CorsPolicyBuilder.WithOrigins()` in `src/Middleware/CORS/src/Infrastructure/CorsPolicyBuilder.cs`
accepts origin strings without validating their format. Passing
`"not-a-url"` or `"http://example.com/path"` (origins should not have
paths) is silently accepted and produces a policy that never matches.
Add URI validation that checks for scheme + authority format and
rejects origins with paths or query strings.

### N5: Add structured logging fields to HttpLoggingMiddleware

`HttpLoggingMiddleware` in `src/Middleware/HttpLogging/src/HttpLoggingMiddleware.cs`
logs request/response details as formatted strings. Add structured
logging using `LoggerMessage.Define` with named fields (`RequestMethod`,
`RequestPath`, `StatusCode`, `Duration`) so log aggregators can index
and query individual fields without parsing.

### N6: Fix DeveloperExceptionPageMiddleware not escaping HTML in exception details

The developer exception page in `src/Middleware/Diagnostics/src/DeveloperExceptionPage/`
renders exception messages in HTML without escaping special characters.
If an exception message contains `<script>`, it could execute in the
developer's browser. Apply `HtmlEncoder.Default.Encode()` to all
user-controlled content in the exception page HTML template.

### N7: Add QueryCollection.TryGetValues() for multi-value query parameters

`QueryCollection` in `src/Http/Http/src/QueryCollection.cs` provides
`TryGetValue()` that returns a `StringValues`, but there is no method
that returns `false` when the key is missing while also providing
empty default handling. Add `TryGetValues(string key, out string[] values)`
that splits `StringValues` into a typed array and returns `false` for
missing keys.

### N8: Fix FormFile.CopyToAsync not respecting cancellation token promptly

`FormFile.CopyToAsync()` in `src/Http/Http/src/FormFile.cs` passes the
cancellation token to the underlying stream copy but does not check
for cancellation before starting the copy. For large files, the
cancellation may not take effect until the next buffer read. Add an
early `cancellationToken.ThrowIfCancellationRequested()` check and
reduce the default buffer size for more responsive cancellation.

### N9: Add BindingAddress.TryParse() for safe address parsing

`BindingAddress.Parse()` in `src/Http/Http/src/BindingAddress.cs`
throws `FormatException` for invalid addresses. Add a
`BindingAddress.TryParse(string address, out BindingAddress result)`
method that returns `false` instead of throwing, matching the .NET
`TryParse` pattern for configuration scenarios where invalid addresses
should be logged rather than crash the application.

### N10: Fix SendFileFallback not setting Content-Length header

`SendFileFallback` in `src/Http/Http/src/SendFileFallback.cs` streams
file content to the response but does not set the `Content-Length`
header when the file length is known. This forces chunked transfer
encoding even for small files. Set `Content-Length` from the file
length when the response has not yet started and no other transfer
encoding is configured.

## Medium

### M1: Implement request rate limiting middleware

Add a `RateLimitingMiddleware` that enforces per-client rate limits
using configurable policies (fixed window, sliding window, token
bucket). Support policy selection by route, client IP, or
authenticated user. Include rate limit headers (`X-RateLimit-Limit`,
`X-RateLimit-Remaining`, `Retry-After`) in responses. Touches
`src/Middleware/` (new project), routing integration for per-endpoint
policies, and DI registration extensions.

### M2: Add request/response caching with validation support

Implement a response caching middleware that supports `Cache-Control`
directives, `ETag` and `Last-Modified` validation, `Vary` header
awareness, and configurable cache stores (in-memory, distributed).
Include cache invalidation by key pattern and integration with
endpoint metadata for per-route cache policies. Touches
`src/Middleware/` (new or extension), `src/Http/Headers/`, and
`src/Caching/`.

### M3: Implement health check aggregation with dependency tree

Extend the health check system in `src/Middleware/HealthChecks/` to
support dependency trees: health check A depends on B and C, and
the aggregate status reflects the dependency graph. Add parallel
execution with timeouts per check, machine-readable output
(JSON with nested dependency status), and a UI endpoint for
visualization. Touches `src/Middleware/HealthChecks/`,
`src/Http/Http.Results/`, and DI extensions.

### M4: Add OpenAPI document customization hooks

The OpenAPI document generator in `src/OpenApi/` produces API
descriptions from endpoints. Add extensibility points: document-level
transformers (add security schemes, server URLs), operation-level
transformers (custom parameters, response examples), and schema-level
transformers (property descriptions, validation rules). Requires
changes to the document builder, schema generator, and DI registration.

### M5: Implement request body validation middleware

Add a validation middleware that validates request bodies against
registered schemas before they reach action handlers. Support JSON
Schema validation for minimal APIs, DataAnnotations validation for
MVC, and custom validators via DI. Return `400 Bad Request` with
a `ProblemDetails` response listing all violations. Touches
`src/Http/Http.Extensions/`, `src/Mvc/Mvc.Core/`, and a new
validation project.

### M6: Add WebSocket compression support to SignalR

SignalR in `src/SignalR/` transports messages over WebSockets but
does not support per-message compression (RFC 7692). Implement
per-message deflate negotiation, compression/decompression in the
WebSocket transport, configurable compression level, and integration
with the existing hub protocol negotiation. Touches
`src/SignalR/common/`, `src/SignalR/clients/csharp/`, and the
server-side transport.

### M7: Implement minimal API endpoint filters with dependency injection

Add `app.MapGet("/path", handler).AddEndpointFilter<MyFilter>()` that
supports DI-resolved filters with `before` and `after` hooks. Filters
should support short-circuiting (returning a result without calling the
handler), async execution, and ordering. Requires changes to
`src/Http/Http.Extensions/`, the route endpoint data source, and the
request delegate pipeline.

### M8: Add request decompression middleware

Implement middleware that automatically decompresses request bodies
with `Content-Encoding: gzip`, `deflate`, or `br`. Support
configurable size limits to prevent decompression bombs, content type
filtering, and integration with the model binding pipeline in MVC.
Touches `src/Middleware/` (new project), `src/Http/Http/`, and
`src/Mvc/Mvc.Core/src/ModelBinding/`.

### M9: Implement structured exception handling with ProblemDetails

Add a global exception handling middleware that catches unhandled
exceptions and converts them to RFC 7807 `ProblemDetails` responses.
Support exception-to-status-code mapping, development vs production
detail levels, custom problem type URIs, and integration with the
existing `IExceptionHandler` interface. Touches
`src/Middleware/Diagnostics/`, `src/Http/Http.Abstractions/`, and
`src/Mvc/Mvc.Core/`.

### M10: Add HTTP/3 QUIC connection metrics

The server abstractions support HTTP/3 but the HTTP logging and
diagnostics middleware do not capture QUIC-specific metrics (connection
migration events, 0-RTT resumption, stream multiplexing counts).
Add QUIC transport metrics to the diagnostics middleware using
`System.Diagnostics.Metrics`, with named instruments for each
metric category. Touches `src/Middleware/HttpLogging/`,
`src/Middleware/Diagnostics/`, and `src/Hosting/Server.Abstractions/`.

## Wide

### W1: Implement API versioning across minimal APIs and MVC

Add first-class API versioning: `app.MapGet("/api/v{version}/users",
handler).HasApiVersion(2.0)`. Support URL-segment, query-string,
header, and media-type versioning strategies. Include version
negotiation, sunset headers, OpenAPI document generation per version,
and deprecation warnings. Changes span `src/Http/Routing/`,
`src/Mvc/Mvc.Core/`, `src/OpenApi/`, `src/Http/Http.Abstractions/`,
and a new versioning project.

### W2: Add multi-tenant architecture support

Implement tenant resolution middleware that identifies tenants from
subdomain, path, header, or claim. Add per-tenant service scoping
in DI, tenant-specific configuration, tenant-isolated data protection
keys, per-tenant authentication schemes, and tenant-aware routing.
Changes span `src/Hosting/`, `src/Http/Http/`, `src/Http/Routing/`,
`src/Identity/`, `src/DataProtection/`, and a new multi-tenancy
project.

### W3: Implement real-time API change notification via SignalR

Add a system where API consumers subscribe to endpoint changes via
SignalR. When routes, schemas, or versions change (detected at
startup or via hot-reload), connected clients receive structured
notifications with diffs. Include a JavaScript SDK for client
integration and webhook fallback. Changes span `src/SignalR/`,
`src/OpenApi/`, `src/Http/Routing/`, `src/Hosting/`, and a new
notification project.

### W4: Add distributed tracing integration across all middleware

Implement end-to-end distributed tracing using `System.Diagnostics.Activity`:
HTTP request/response spans, middleware execution spans, MVC action
spans, SignalR hub method spans, database query spans (via
`DbConnection` instrumentation), and external HTTP client spans.
Include trace context propagation (`traceparent` header), span
attribute enrichment, and configurable sampling. Changes span
`src/Hosting/`, `src/Middleware/`, `src/Mvc/Mvc.Core/`,
`src/SignalR/`, `src/Http/Http.Extensions/`, and
`src/HttpClientFactory/`.

### W5: Implement server-sent events with backpressure

Add `Results.ServerSentEvents(stream)` that produces SSE responses
with proper formatting, automatic keep-alive, client disconnection
detection, and backpressure support (pause producing when the client
falls behind). Include a typed `SseClient<T>` for server-side push
with serialization, retry configuration, and event filtering.
Changes span `src/Http/Http.Results/`, `src/Http/Http/`,
`src/Hosting/Server.Abstractions/`, `src/SignalR/` (for comparison),
and new SSE project files.

### W6: Add GraphQL endpoint support alongside REST

Implement a GraphQL middleware that integrates with the ASP.NET Core
pipeline: schema-first and code-first definition, query/mutation/
subscription support, DataLoader for N+1 prevention, persisted
queries, complexity analysis, and authentication/authorization
integration. Changes span `src/Http/Routing/`, `src/Http/Http.Extensions/`,
a new `src/GraphQL/` project, `src/Middleware/`, `src/Identity/`
(auth integration), and `src/OpenApi/` (parallel documentation).

### W7: Implement auto-scaling feedback loop with load metrics

Add a metrics emission system that publishes load indicators
(request queue depth, response latency percentiles, thread pool
saturation, memory pressure) in a format consumable by orchestrators
(Kubernetes HPA, Azure Autoscale). Include a health check provider
that returns `503 Service Unavailable` under load pressure and
graceful drain support during shutdown. Changes span `src/Hosting/`,
`src/Middleware/Diagnostics/`, `src/Middleware/HealthChecks/`,
`src/Http/Http/`, and a new scaling advisory project.

### W8: Add comprehensive API testing framework

Implement a testing SDK extending `TestHost`: API contract testing
(snapshot comparison of OpenAPI documents), load testing with
configurable concurrency, mutation testing for security
(authentication bypass, injection), and integration test fixtures
with database seeding. Include a test runner that generates
HTML reports. Changes span `src/Hosting/TestHost/`, `src/OpenApi/`,
a new `src/Testing/` project, `src/Mvc/Mvc.Core/`, and CI
integration.

### W9: Implement plugin-based middleware pipeline

Add a plugin system where middleware can be packaged as NuGet
packages and auto-discovered at startup. Support plugin ordering
via `[Before]`/`[After]` attributes, plugin configuration through
`appsettings.json`, versioned plugin interfaces, and a plugin
health dashboard. Changes span `src/Hosting/`, `src/Http/Http/`,
`src/Middleware/`, a new `src/Plugins/` project, DI integration,
and the `WebApplicationBuilder`.

### W10: Add WebAssembly server-side rendering integration

Extend Blazor's server-side rendering to support streaming SSR with
progressive hydration: render initial HTML on the server, stream
component updates as they complete, and hydrate interactive
components on the client. Include component-level render mode
selection (static, server, WebAssembly), prerendering cache, and
fallback for non-JavaScript clients. Changes span `src/Components/`,
`src/Hosting/`, `src/Http/Http.Results/`, `src/Mvc/Mvc.Core/`
(tag helpers), `src/SignalR/` (for server interactivity), and
JavaScript interop.
