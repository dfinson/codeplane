# tokio-rs/axum

| Field | Value |
|-------|-------|
| **URL** | https://github.com/tokio-rs/axum |
| **License** | MIT |
| **Language** | Rust |
| **Scale** | Medium-large |
| **Category** | Web framework for Tokio |
| **Set** | eval |
| **Commit** | `39eda3c6be7ad34687dc50d9f11a3cb4c3f9521e` |

## Why this repo

- **Multi-crate workspace**: axum (main), axum-core (traits), axum-extra (extensions), axum-macros (proc macros)
- **Tower-based**: Built on tower `Service` trait with composable middleware layers
- **Type-driven extractors**: Request decomposition via `FromRequest`/`FromRequestParts` traits, compile-time handler validation

## Structure overview

```
axum/
├── axum/src/
│   ├── routing/          # Router, MethodRouter, path_router, method_filter, route, nest
│   ├── extract/          # Path, Query, State, WebSocket, multipart, rejection
│   ├── handler/          # Handler trait, HandlerService, future
│   ├── middleware/        # from_fn, from_extractor, map_request, map_response
│   ├── response/          # Redirect, SSE (Server-Sent Events)
│   ├── serve/             # serve(), Listener, connection handling
│   ├── json.rs            # Json extractor/response
│   ├── form.rs            # Form extractor
│   ├── extension.rs       # Extension extractor
│   └── boxed.rs           # Boxed handlers
├── axum-core/src/
│   ├── extract/           # FromRequest, FromRequestParts, DefaultBodyLimit
│   ├── response/          # IntoResponse, IntoResponseParts, AppendHeaders
│   ├── body.rs            # Body type alias
│   └── error.rs           # Error type
├── axum-extra/src/
│   ├── extract/           # Cookie, CachedExtractor, WithRejection, Form, Multipart
│   ├── response/          # Attachment, ErasedJson, FileStream, Multiple
│   ├── routing/           # Resource, TypedPath
│   ├── handler/           # Or handler combinator
│   └── typed_header.rs    # TypedHeader extractor
└── axum-macros/src/       # #[debug_handler], #[derive(FromRequest)], #[derive(TypedPath)]
```

## Scale indicators

- ~290 Rust source files across 4 crates (excluding tests)
- ~39K lines of code
- Tower service middleware stack
- Type-safe extractor and response system

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `Router::nest` stripping the prefix from `MatchedPath`

When a router is nested under a prefix via `Router::nest("/api", inner)`, the `MatchedPath` extractor returns only the inner route pattern (e.g., `/:id`) instead of the full nested path (`/api/:id`). The `strip_prefix` module removes the prefix before matching but does not prepend it back.

### N2: Fix `WebSocketUpgrade` not rejecting non-GET upgrade requests

The `WebSocketUpgrade` extractor in `extract/ws.rs` checks for the `Upgrade: websocket` header but does not validate that the HTTP method is GET. A POST request with upgrade headers is accepted, violating RFC 6455 which requires the opening handshake to be GET.

### N3: Add `Router::has_routes` check for empty method routers

`Router::has_routes` returns `true` if any paths are registered, but does not account for paths that have only a fallback and no actual method handlers. A `Router::new().route("/health", MethodRouter::new())` registers a path with no methods but `has_routes()` returns `true`.

### N4: Fix `Path` extractor percent-decoding not handling `%2F` in segments

The `Path` extractor in `extract/path/mod.rs` percent-decodes path parameters, but `%2F` (forward slash) is decoded into `/`, which can break downstream logic that splits on `/`. The extractor should preserve `%2F` as-is in individual path segments.

### N5: Fix `MethodRouter::merge` silently dropping conflicting method handlers

When merging two `MethodRouter` instances that both have a handler for GET, `merge` currently panics. However, the panic message does not indicate which method conflicts. Add the conflicting HTTP method name to the error message.

### N6: Add `Redirect::see_other` convenience constructor

`Redirect` provides `to`, `permanent`, and `temporary` constructors but not `see_other` (303). POST-redirect-GET flows commonly need 303 status. Add `Redirect::see_other(uri)` that returns a 303 redirect response. Update `CHANGELOG.md` with the new API addition and add a usage example to `README.md`.

### N7: Fix SSE `Event::json_data` not setting event content type

In `response/sse.rs`, `Event::json_data` serializes the data field as JSON but the SSE `content-type` header for the overall response is already `text/event-stream`. The issue is that `json_data` silently drops serialization errors by converting them to `Event::default()`—it should propagate the error.

### N8: Fix `ConnectInfo` not available in middleware added via `Router::layer`

When middleware added via `Router::layer` tries to extract `ConnectInfo<SocketAddr>`, it fails because `ConnectInfo` is inserted by `into_make_service_with_connect_info` which runs after the layer. Document this limitation and add a compile-time-friendly error message in the rejection.

### N9: Add `State` extractor debug assertion for missing state

When `State<T>` extraction fails because the state was not provided via `Router::with_state`, the error message is a generic "missing extension." Add a more descriptive rejection message that names the state type `T` and suggests calling `with_state`.

### N10: Fix `serve` not propagating `TcpListener` SO_KEEPALIVE setting

The `serve` function in `serve/mod.rs` accepts a `TcpListener` but does not configure TCP keepalive on accepted connections. Long-lived idle connections (e.g., SSE, WebSocket) can be silently dropped by intermediate proxies without keepalive probes.

### N11: Fix `CHANGELOG.md` not documenting breaking changes from axum-core extractor trait signature updates

The `CHANGELOG.md` at the repository root does not include entries for breaking changes introduced in axum-core's `FromRequest` and `FromRequestParts` trait modifications. Contributors updating extractors have no changelog guidance. Add a changelog policy section to `CONTRIBUTING.md` and backfill missing breaking-change entries in `CHANGELOG.md` referencing the relevant axum-core commits.

## Medium

### M1: Implement request body size limiting middleware

Add middleware that enforces a maximum request body size, returning 413 Payload Too Large when exceeded. Integrate with `DefaultBodyLimit` from axum-core but allow per-route override via `Router::route_layer`. Handle streaming bodies by counting bytes as they are read. Add a body-limit example to the `examples/` directory and document configuration in `ECOSYSTEM.md`.

### M2: Add typed multipart form extractor with struct derivation

Implement a `#[derive(TypedMultipart)]` macro in axum-macros that generates `FromRequest` for structs representing multipart form uploads. Map struct fields to form fields by name, support `Vec<u8>` for file contents and `String` for text parts. Changes span axum-macros and axum-extra's multipart module.

### M3: Implement graceful shutdown with connection draining

Add `Serve::with_graceful_shutdown(signal)` that stops accepting new connections when the signal fires, sends HTTP connection close to active connections, and waits for in-flight requests to complete with a configurable timeout. Changes span `serve/mod.rs` and `serve/listener.rs`.

### M4: Add response caching middleware with ETag support

Implement middleware that caches response bodies by route pattern, computes weak ETags from body hashes, and returns 304 Not Modified for matching `If-None-Match` requests. Support configurable TTL and cache invalidation. Changes touch middleware module and response handling.

### M5: Implement extractor for validated JSON with custom error responses

Add a `ValidatedJson<T>` extractor that deserializes and validates the request body using a validation trait. Return structured error responses with field-level error details instead of axum's default rejection. Changes span a new extractor in axum-extra and rejection handling in axum-core.

### M6: Add request timeout middleware with per-route configuration

Implement timeout middleware that wraps handler futures with `tokio::time::timeout`. Support per-route timeout values via route-layer. When the timeout expires, return 504 and cancel the handler future. Handle cleanup for WebSocket upgrades. Changes touch middleware module, routing, and handler future.

### M7: Implement session middleware with pluggable storage

Add cookie-based session middleware that stores session data server-side. Support in-memory and Redis storage backends. Implement session ID rotation, expiration, and secure cookie attributes. Changes span axum-extra's cookie module, a new session module, and middleware integration.

### M8: Add health check endpoint with readiness/liveness separation

Implement a health check framework with `HealthCheck` trait, readiness vs. liveness probe distinction, and aggregate health status. Support async health checks with timeout. Add `Router` extension method for registering health endpoints. Changes touch routing and add a new health module in axum-extra.

### M9: Implement form-data extractor with file streaming to disk

Add a `StreamingMultipart` extractor that streams uploaded files directly to disk without buffering the entire file in memory. Support configurable temp directory, max file size, and filename sanitization. Changes span axum's multipart extractor and axum-extra.

### M10: Add metrics middleware collecting request duration and status histograms

Implement middleware that measures request processing time, records HTTP method, route pattern, and status code, and exposes metrics via a Prometheus-compatible endpoint. Changes touch middleware module, routing (for matched-path extraction), and add a metrics response endpoint.

### M11: Add CI workflow for automated API compatibility checking

Extend `.github/workflows/CI.yml` to include an API compatibility check step using `cargo-semver-checks` that validates all four crates (axum, axum-core, axum-extra, axum-macros) against their previous published versions. Update `CONTRIBUTING.md` with semver policy, add a `deny.toml` rule for detecting accidental public API removals, and document the CI pipeline in `.github/PULL_REQUEST_TEMPLATE.md`.

## Wide

### W1: Implement API versioning framework

Add URL-prefix, header-based, and content-type versioning strategies. Support version-specific routers that share state. Implement version negotiation, deprecation warnings in response headers, and automatic routing to the closest matching version. Changes span routing, middleware, extractors, response headers, and a new versioning module.

### W2: Add end-to-end OpenTelemetry tracing

Implement distributed tracing integration: create spans for each request, propagate trace context via headers, annotate spans with route pattern and status code, trace extractor execution. Changes span middleware (span creation), extractors (context propagation), handler (span attributes), response (trace headers), and a new tracing module in axum-extra.

### W3: Implement WebSocket broadcast framework

Add a WebSocket hub pattern with named rooms, broadcast/unicast messaging, connection lifecycle hooks, and backpressure handling. Support serialized message types with automatic JSON encoding. Changes span `extract/ws.rs` (connection management), routing (WS route builder), a new `ws/` module, and state management integration.

### W4: Add automatic OpenAPI documentation generation

Implement OpenAPI 3.1 spec generation from route definitions, extractor types, and response types. Derive JSON Schema from Rust types via a proc macro. Serve Swagger UI at a configurable endpoint. Changes span axum-macros (schema derivation), routing (route metadata), extractors (parameter docs), responses (schema inference), and a new openapi module.

### W5: Implement multi-tenant request isolation

Add tenant-aware middleware that identifies tenants from request headers/subdomains, provides tenant-scoped state via a new `TenantState<T>` extractor, enforces per-tenant rate limits and resource quotas. Changes span middleware, extractors, state management, routing (tenant-scoped routes), and a new tenant module.

### W6: Add static file serving with directory listings and caching

Implement a static file service with directory listing, range requests, conditional requests (ETag/Last-Modified), content-type detection, and Brotli/gzip precompressed file support. Changes span routing (nest_service integration), response (range/conditional logic), middleware (compression), and a new static-files module in axum-extra.

### W7: Implement request/response transformation pipeline

Add composable request and response transformers: body rewriting, header injection, URL rewriting, request/response logging with body capture. Support per-route transformer chains. Changes span middleware (from_fn extensions), handler layer, request/response types, routing (transformer registration), and a new transform module.

### W8: Add GraphQL integration layer

Implement a GraphQL handler framework with schema registration, query/mutation routing, subscription support via WebSocket, and DataLoader integration for batched data fetching. Changes span routing (GraphQL endpoint), extractors (query parsing), WebSocket (subscriptions), middleware (query complexity limiting), and a new graphql module.

### W9: Implement background task queue with handler integration

Add an in-process task queue that handlers can submit work to via a `TaskQueue` extractor. Support async task execution, retries with backoff, task status tracking, and graceful shutdown draining. Changes span state management, extractors, serve (shutdown coordination), middleware (task context), and a new tasks module.

### W10: Add request replay and testing framework

Implement a testing framework with recorded request replay, response assertion helpers, mock extractors, and integration test utilities. Support capturing handler chains as test fixtures. Changes span a new testing module, handler (test mode), extractors (mock implementations), routing (test router builder), and response (assertion helpers).

### W11: Implement comprehensive contributor onboarding and ecosystem documentation

Overhaul the project's non-code documentation: restructure `CONTRIBUTING.md` with step-by-step build instructions, PR checklist, and coding conventions; update `ECOSYSTEM.md` with a curated list of middleware and extractor crates; add an architecture overview to `README.md` explaining the crate dependency graph; create `.github/DISCUSSION_TEMPLATE/` forms for feature requests vs. questions; and add a `deny.toml` audit policy section. Changes span `CONTRIBUTING.md`, `ECOSYSTEM.md`, `README.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `.github/DISCUSSION_TEMPLATE/`, `deny.toml`, and `Cargo.toml` workspace metadata.
