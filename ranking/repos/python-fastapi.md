# fastapi/fastapi

| Field | Value |
|-------|-------|
| **URL** | https://github.com/fastapi/fastapi |
| **License** | MIT |
| **Language** | Python |
| **Scale** | Medium (multi-module project) |
| **Category** | Web framework |

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

8 tasks (3 narrow, 3 medium, 2 wide) for the Python web framework.

## Narrow

### N1: Fix `Depends()` with `yield` not closing on WebSocket disconnect

When a WebSocket connection drops unexpectedly, dependencies that use
`yield` (generator dependencies) do not have their cleanup code executed.
The `finally` block of the dependency generator is never reached because
the WebSocket disconnect exception propagates before the dependency
manager can finalize. Fix the WebSocket route handling to ensure generator
dependencies are always cleaned up on disconnect.

### N2: Add `deprecated` parameter to `APIRouter`

FastAPI supports marking individual routes as deprecated via
`@app.get("/path", deprecated=True)`, but there's no way to mark an
entire router as deprecated. Add a `deprecated` parameter to `APIRouter`
that applies to all routes registered on that router. Routes should
inherit the router's deprecated status unless they explicitly override it.

### N3: Fix OpenAPI schema for `Optional[List[str]]` query parameter

When a query parameter is typed as `Optional[List[str]]`, the generated
OpenAPI schema incorrectly sets `required: true` instead of `required: false`.
The optionality is lost during schema generation. Fix the parameter schema
generation to correctly handle `Optional` wrapping around collection types.

## Medium

### M1: Add rate limiting middleware

Implement a built-in rate limiting middleware that supports per-client
(by IP) and per-route rate limits using a sliding window algorithm. The
middleware should support configurable backends (in-memory for single-process,
with a protocol for external stores like Redis). Include proper `429 Too Many
Requests` responses with `Retry-After` headers. Add the rate limit
configuration to the OpenAPI schema as a vendor extension.

### M2: Implement dependency overrides scoped to test context

The current `app.dependency_overrides` is a global dict that affects all
concurrent requests, making it unsafe for parallel test execution. Implement
a context-scoped override mechanism (using contextvars) so that dependency
overrides in one test don't leak into another test running concurrently.
Provide both a context manager and a pytest fixture for test authors.

### M3: Add response model validation in debug mode

Currently, FastAPI validates request inputs but does not validate response
bodies against the declared `response_model`. Add an optional debug mode
that validates outgoing responses against the response model and raises a
detailed error if the response doesn't match. This should be off by default
and configurable via an application setting. Include the validation error
details in the error response.

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
