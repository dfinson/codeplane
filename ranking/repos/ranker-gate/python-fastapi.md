# fastapi/fastapi

| Field | Value |
|-------|-------|
| **URL** | https://github.com/fastapi/fastapi |
| **License** | MIT |
| **Language** | Python |
| **Scale** | Medium (multi-module project) |
| **Category** | Web framework |
| **Set** | ranker-gate |
| **Commit** | `11614be9021aa4ac078d4d0693a8b5250a1010d8` |

## Why this repo

- **Multi-module with clear boundaries**: Routing, dependency injection,
  security (OAuth2, API keys), middleware, background tasks, WebSockets,
  OpenAPI schema generation — each a distinct subsystem requiring navigation
  between them.
- **Well-structured**: All source under `fastapi/` with logical subpackages
  (`security/`, `middleware/`, `openapi/`). Not a monorepo.
- **Rich history**: 10K+ commits, very active PR flow, strong code review
  culture. One of the most popular Python frameworks.
- **Permissive**: MIT license.

## Structure overview

```
fastapi/
├── applications.py      # FastAPI app class
├── routing.py           # APIRouter, route handling
├── dependencies/        # Dependency injection system
├── security/            # OAuth2, HTTP Basic/Bearer, API keys
├── middleware/           # CORS, GZip, HTTPSRedirect
├── openapi/             # OpenAPI schema generation
├── params.py            # Query, Path, Header, Cookie params
├── responses.py         # Response classes
├── encoders.py          # JSON encoding
├── exceptions.py        # HTTP exception handling
├── background.py        # Background tasks
├── websockets.py        # WebSocket support
└── testclient.py        # Test utilities
```

## Scale indicators

- ~80 Python source files
- ~25K lines of code
- Requires navigating between routing, DI, security, and schema subsystems
- Moderate dependency tree (Starlette, Pydantic)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `generate_unique_id` nondeterminism when route has multiple HTTP methods

`generate_unique_id` in `utils.py` builds the operation ID using
`list(route.methods)[0]`, but `route.methods` is a `set` and sets have
no guaranteed iteration order. When a route is registered with multiple
methods via `add_api_route(methods=["GET", "POST"])`, the generated
operation ID varies across process restarts, producing nondeterministic
OpenAPI schemas. Fix by sorting the methods before selecting one for the
operation ID.

### N2: Fix `OpenIdConnect.__call__` returning raw `Authorization` header with scheme prefix

`OpenIdConnect.__call__` in `security/open_id_connect_url.py` returns the
entire `Authorization` header value (e.g., `"Bearer abc123"`). Unlike
`HTTPBearer` and `HTTPBase`, which use `get_authorization_scheme_param`
from `security/utils.py` to split the scheme from the credentials,
`OpenIdConnect` returns the unsplit string. Fix `OpenIdConnect.__call__`
to parse the header and return only the credentials portion, consistent
with the other security scheme classes.

### N3: Fix `format_sse_event` not validating newlines in `id` and `event` fields

`format_sse_event` in `sse.py` correctly splits `data` and `comment`
fields across multiple lines via `splitlines()`, but it places `id` and
`event` values on a single line without any newline handling. If either
value contains a `\n`, the SSE wire framing breaks — the premature
newline terminates the current field and corrupts the event stream.
The SSE specification forbids newlines in `id` and `event` fields.
Fix by validating that these fields do not contain newline characters
and raising `ValueError` if they do. Update the SSE documentation in
`docs/en/docs/tutorial/server-sent-events.md` to document the validation
behavior and the error raised for invalid field values.

### N4: Fix `_endpoint_context_cache` unbounded memory growth

`_extract_endpoint_context` in `routing.py` caches endpoint context
information in a module-level `dict` keyed by `id(func)`. In
applications that create endpoints dynamically (e.g., using factory
functions or closures in a loop), the cache grows without bound.
Additionally, Python can reuse `id()` values after an object is
garbage-collected, causing the cache to return stale context for a
different function. Replace the plain dict with a bounded LRU cache
or use `weakref` to allow eviction of unused entries.

### N5: Add `description` parameter to `APIWebSocketRoute` and WebSocket decorators

`APIRoute` accepts a `description` parameter that appears in the
generated OpenAPI operation, but `APIWebSocketRoute` in `routing.py`
only accepts `path`, `endpoint`, `name`, and `dependencies`. The
`@app.websocket()` and `add_api_websocket_route` methods likewise lack
a `description` parameter. Add `description` to `APIWebSocketRoute.__init__`
and propagate it through the decorator and `include_router` WebSocket
branch, so WebSocket routes carry descriptive metadata for documentation
generators and introspection tools.

### N6: Improve `DependencyScopeError` to include the full dependency chain

`DependencyScopeError` raised in `dependencies/utils.py` includes only
the name of the dependency that triggered the scope violation (e.g.,
`'The dependency "get_db" has a scope of "request"...'`). In deeply
nested dependency graphs, this makes it hard to trace which parent
dependency path led to the invalid scope combination. Enhance the error
message to include the full chain of dependency calls from the endpoint
down to the violating dependency.

### N7: Fix `websocket_request_validation_exception_handler` passing list as close reason

The default WebSocket validation error handler in `exception_handlers.py`
calls `websocket.close(reason=jsonable_encoder(exc.errors()))`. This
passes a Python list as the close `reason`, but RFC 6455 requires the
close reason to be a UTF-8 string of at most 123 bytes. Large validation
error lists either fail silently or get truncated unpredictably. Fix the
handler to serialize errors to a JSON string and truncate to fit within
the WebSocket close reason size limit.

### N8: Add `status_code` parameter to dependency injection error responses

When a dependency raises `HTTPException`, the status code is always
what the dependency specifies. Add a way for the route decorator to
specify a default error status code for dependency failures:
`@app.get("/", dependency_error_status=503)`.

### N9: Fix `ValidationException.__str__` producing unreadable error output

`ValidationException.__str__` in `exceptions.py` formats each error with
a bare `f"  {err}\n"`, which prints raw error dicts like
`{'loc': ('query', 'name'), 'msg': '...', 'type': '...'}`. This makes
server logs hard to scan when validation errors occur. Improve the
formatting to display each error's location path (e.g., `query -> name`),
message, and error type in a human-readable layout, similar to Pydantic's
own `ValidationError.__str__`.

### N10: Fix duplicate `422` response in OpenAPI when custom error handler is set

When a custom exception handler is registered for `RequestValidationError`,
the OpenAPI schema still includes the default 422 response schema
alongside any custom error schema. Fix the OpenAPI generator to use the
custom handler's response schema when one is registered.

## Medium

### M1: Add rate limiting middleware

Implement a built-in rate limiting middleware that supports per-client
(by IP) and per-route rate limits using a sliding window algorithm. The
middleware should support configurable backends (in-memory for single-process,
with a protocol for external stores like Redis). Include proper `429 Too Many
Requests` responses with `Retry-After` headers. Add the rate limit
configuration to the OpenAPI schema as a vendor extension. Add a tutorial
page at `docs/en/docs/tutorial/rate-limiting.md` covering configuration
examples and update `docs/en/mkdocs.yml` to include the new page in the
Tutorial nav section.

### M2: Implement dependency overrides scoped to test context

The current `app.dependency_overrides` is a global dict that affects all
concurrent requests, making it unsafe for parallel test execution. Implement
a context-scoped override mechanism (using contextvars) so that dependency
overrides in one test don't leak into another test running concurrently.
Provide both a context manager and a pytest fixture for test authors.

### M3: Add per-route middleware support via route decorators

Currently, middleware in FastAPI is applied globally via
`app.add_middleware()`. There is no way to scope middleware to a single
route or router. Add a `middleware` parameter to `@app.get()`,
`@app.post()`, and other HTTP method decorators (and to `APIRoute`) that
applies middleware only to that route. Route-level middleware should
execute between the global middleware stack and the endpoint handler.
Requires changes to `routing.py` (`APIRoute.__init__`,
`get_request_handler`, and the HTTP method decorators) and
`applications.py` (middleware stack integration).

### M4: Implement lazy dependency resolution with `Depends(lazy=True)`

Add a `lazy=True` parameter to `Depends()` that defers dependency
resolution until the result is actually accessed in the endpoint
function. Currently, all dependencies in the graph are resolved eagerly
before the endpoint runs, even if some are only needed in certain code
paths. A lazy dependency should return a lightweight proxy that calls
the real dependency on first attribute access or invocation. This
requires changes to `params.py` (add `lazy` to `Depends`),
`dependencies/models.py` (track lazy flag in `Dependant`), and
`dependencies/utils.py` (`solve_dependencies` must skip lazy deps
and inject proxy objects instead).

### M5: Add automatic request ID propagation

Implement a built-in request ID system. Generate a UUID for each request
(or accept from `X-Request-ID` header), make it available via dependency
injection, include it in all response headers, propagate it through
background tasks, and include it in log records via a custom log filter.

### M6: Implement API endpoint deprecation with sunset headers

Add `sunset` parameter to route decorators that sets the `Sunset` HTTP
header (RFC 8594) with the deprecation date. Include sunset information
in the OpenAPI schema. Add a middleware that logs warnings for requests
to endpoints past their sunset date. Support gradual deprecation with
warning periods.

### M7: Add OpenAPI schema customization hooks

Add hooks that allow modifying the generated OpenAPI schema before it's
served. Support `schema_extra` at the app level (modify the root schema),
route level (modify operation schemas), and parameter level. Add a
`SchemaTransformer` protocol for reusable schema modifications.

### M8: Implement circuit breaker pattern for dependencies

Add a `CircuitBreaker` dependency wrapper that stops calling a failing
dependency after a threshold of failures. Track failure counts per
dependency. Support half-open state for testing recovery. Return
configurable fallback responses when the circuit is open.

### M9: Add request body size limiting with streaming support

Implement configurable request body size limits. Reject oversized
requests with 413 before fully reading the body (use Content-Length
for pre-check, enforce during streaming). Support per-route limits
via route decorator parameters. Add rate limiting for request body
throughput.

### M10: Implement structured audit logging

Add an audit logging middleware that records who did what: endpoint
path, method, authenticated user, request parameters (with configurable
redaction of sensitive fields), response status, and duration. Support
pluggable audit sinks (file, database, external service). Add a
decorator for marking routes as audit-exempt.

## Wide

### W1: Add first-class support for API versioning

Implement API versioning support across the framework. Support URL-prefix
versioning (`/v1/`, `/v2/`), header-based versioning (`Accept-Version`),
and query parameter versioning (`?version=1`). This requires changes to
the router to support version-qualified route registration, the dependency
injection system to make the active version available as a dependency,
OpenAPI schema generation to produce per-version schemas, and the test
client to support specifying a version.

### W2: Implement structured error responses with problem details (RFC 9457)

Replace the current `HTTPException` error handling with a system that
produces RFC 9457 (Problem Details for HTTP APIs) compliant error responses.
This affects the exception classes, exception handlers, validation error
formatting, OpenAPI schema generation (error response schemas), and
documentation generation. Provide migration helpers for existing
`HTTPException` usage.

### W3: Add GraphQL support alongside REST

Implement a `FastAPI` extension that adds GraphQL endpoints using the
Strawberry library. Auto-generate GraphQL types from Pydantic models,
support queries/mutations/subscriptions, integrate with FastAPI's
dependency injection for resolvers, and share authentication/permission
logic between REST and GraphQL endpoints. Includes schema stitching
for combining multiple GraphQL schemas.

### W4: Implement multi-tenancy support

Add tenant-aware request handling. Support tenant resolution from
subdomain, header, or URL path prefix. Make the current tenant
available as a dependency. Add tenant-scoped database connections
(via dependency injection), tenant-scoped caching, and tenant-specific
configuration. Include OpenAPI schema generation per tenant.

### W5: Expose WebSocket routes in the OpenAPI schema with full metadata

Currently, `get_openapi_path` in `openapi/utils.py` silently skips
`APIWebSocketRoute` instances — only `APIRoute` (HTTP) routes appear
in the generated OpenAPI schema. Implement WebSocket operation
advertising so that WebSocket endpoints are documented alongside REST
endpoints. This requires: adding metadata parameters (`tags`,
`summary`, `description`, `deprecated`, `responses`) to
`APIWebSocketRoute` in `routing.py`; defining WebSocket-specific
schema models in `openapi/models.py`; extending `get_openapi_path`
and `get_openapi` in `openapi/utils.py` to generate operation objects
for WebSocket routes; and wiring the new schemas into the
`FastAPI.openapi()` method in `applications.py`.

### W6: Implement request/response transformation pipeline

Add a middleware system for transforming requests and responses.
Support request body transformation (decompress, decrypt, validate
signatures), response body transformation (compress, encrypt, sign),
and header transformation. Transformers should compose in a pipeline.
Add built-in transformers for JSON Schema validation, response
envelope wrapping, and field-level encryption.

### W7: Add comprehensive health check framework

Implement a health check system with dependency health probes (database
connectivity, Redis availability, external API health), aggregated
health status, startup/liveness/readiness probe separation for
Kubernetes, health history tracking, and a health dashboard endpoint.
Support async health checks with configurable timeouts and caching.
Changes span middleware, routing, and a new health module.

### W8: Implement API analytics and usage tracking

Add request analytics collection: endpoint usage counts, response time
percentiles, error rates by endpoint, payload size distributions, and
client fingerprinting (by API key, user agent). Store in a pluggable
backend (in-memory, Redis, PostgreSQL). Add an admin endpoint for
querying analytics data. Support real-time streaming of analytics events.

### W9: Add AsyncAPI schema generation for WebSocket and SSE endpoints

FastAPI generates OpenAPI schemas for HTTP endpoints, but WebSocket
and SSE endpoints have no machine-readable API documentation. Implement
AsyncAPI 3.0 schema generation that documents WebSocket message
formats (inferred from dependency-injected params and return types),
SSE event channel metadata (from `EventSourceResponse` routes and
`ServerSentEvent` models), and subscription protocols. This requires:
a new `asyncapi/` subpackage with schema models and a generation
function; changes to `routing.py` to capture message type annotations
on `APIWebSocketRoute`; changes to `sse.py` to expose channel
metadata; changes to `applications.py` to serve the AsyncAPI schema
at a configurable path; and changes to `openapi/docs.py` to add an
AsyncAPI documentation UI endpoint.

### W10: Implement plugin system for FastAPI extensions

Add a formal plugin architecture that allows third-party packages to
register routes, middleware, dependencies, exception handlers, and
OpenAPI schema extensions through a declarative plugin interface.
Support plugin lifecycle hooks (startup, shutdown), plugin
dependencies/ordering, and plugin configuration via the app's settings.
Changes span the application class, router, middleware stack, and a
new plugin registry module.

### N11: Fix incomplete CITATION.cff metadata

`CITATION.cff` declares an empty `identifiers:` key with no entries
and omits the `date-released` field. The CFF 1.2.0 specification
requires `date-released` for valid software citations, and an empty
`identifiers` list is a schema violation that causes `cffconvert
--validate` to fail. Add the missing `date-released`, populate or
remove the `identifiers` key, and consider adding `version` so
citation tooling can generate correct references.

**Gold files:** `CITATION.cff`

### M11: Align release-build Python version with CI test matrix

`.python-version` is set to `3.11`, and `.github/workflows/publish.yml`
reads it via `python-version-file: ".python-version"` to build and
publish releases. However, the test matrix in
`.github/workflows/test.yml` covers Python 3.10, 3.12, 3.13, and
3.14 — but never 3.11. The Python version used to build and publish
the package to PyPI is not tested in CI, so version-specific build
or packaging issues would go undetected until after release.

**Gold files:** `.python-version`, `.github/workflows/publish.yml`,
`.github/workflows/test.yml`

### W11: Close dependency-update and labeler coverage gaps

`.github/dependabot.yml` monitors `github-actions` (daily) and `uv`
(monthly) ecosystems but has no entry for the `pre-commit` ecosystem,
even though `.pre-commit-config.yaml` pins external repos like
`pre-commit/pre-commit-hooks` at `rev: v6.0.0` — these pinned
versions will never receive automatic update PRs. The
`.github/labeler.yml` defines rules for `docs`, `lang-all`, and
`internal` labels, but changes to root-level community-health files
(`SECURITY.md`, `CITATION.cff`, `CONTRIBUTING.md`) do not match any
rule — they fall outside both the `internal` glob (`.github/**`,
`scripts/**`) and the `docs` glob (`docs/en/docs/**`, `docs_src/**`).
Additionally, `pyproject.toml`'s `[tool.pdm.build] source-includes`
lists `tests/`, `docs_src/`, `scripts/` but excludes `CITATION.cff`
and `SECURITY.md`, so these project metadata files are omitted from
the source distribution.

**Gold files:** `.github/dependabot.yml`, `.pre-commit-config.yaml`,
`.github/labeler.yml`, `pyproject.toml`
