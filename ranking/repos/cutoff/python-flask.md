# pallets/flask

| Field | Value |
|-------|-------|
| **URL** | https://github.com/pallets/flask |
| **License** | BSD-3-Clause |
| **Language** | Python |
| **Scale** | Medium |
| **Category** | Web microframework |
| **Set** | Cutoff |

## Why this repo

- **Layered WSGI architecture**: The `Flask` class in `app.py` inherits
  from `sansio/app.py`'s `App` base (protocol-agnostic routing, error
  handling, and decorator registration) and adds WSGI-specific request
  handling. `sansio/scaffold.py` provides the common `Scaffold` base
  for both `App` and `Blueprint`, while `sansio/blueprints.py` supplies
  the protocol-agnostic blueprint logic. This sansio split enables
  clean separation of I/O concerns.
- **Comprehensive surface area**: Request/response wrappers
  (`wrappers.py`), context locals (`ctx.py`, `globals.py`), session
  management (`sessions.py`), JSON handling (`json/`), configuration
  (`config.py`), CLI integration (`cli.py`), blueprint modularity
  (`blueprints.py`), class-based views (`views.py`), template
  integration (`templating.py`), test client (`testing.py`), signal
  system (`signals.py`), and helper utilities (`helpers.py`).
- **Permissive**: BSD-3-Clause license.

## Structure overview

```
src/flask/
├── __init__.py            # Public API re-exports
├── __main__.py            # Entry point for `python -m flask`
├── app.py                 # Flask class — WSGI app, request dispatch
├── blueprints.py          # Blueprint class — modular route grouping
├── cli.py                 # Click-based CLI — `flask run`, `flask shell`, etc.
├── config.py              # Config class, ConfigAttribute descriptor
├── ctx.py                 # AppContext, RequestContext, _AppCtxGlobals (g)
├── debughelpers.py        # Debug-mode helpers and error explanations
├── globals.py             # Context-local proxies — request, session, g, current_app
├── helpers.py             # url_for, redirect, flash, send_file, abort, etc.
├── logging.py             # Default logging configuration
├── sessions.py            # Session interface, SecureCookieSessionInterface
├── signals.py             # Blinker-based signal definitions
├── templating.py          # Jinja2 Environment and template rendering
├── testing.py             # FlaskClient and FlaskCliRunner
├── typing.py              # Type aliases — RouteCallable, ErrorHandlerCallable
├── views.py               # View and MethodView class-based views
├── wrappers.py            # Request and Response (extend Werkzeug)
├── json/
│   ├── __init__.py        # dumps, loads with app-aware defaults
│   ├── provider.py        # DefaultJSONProvider class
│   └── tag.py             # Tagged JSON serializer for session data
└── sansio/
    ├── app.py             # App base — routing, error handlers, decorators
    ├── blueprints.py      # Blueprint base — deferred setup state
    └── scaffold.py        # Scaffold base — route, before/after request hooks
```

## Scale indicators

- ~24 Python source files (including json/ and sansio/ sub-packages)
- ~9.5K lines of code
- Shallow structure (main package + 2 small sub-packages)
- Dependencies: `werkzeug`, `jinja2`, `itsdangerous`, `click`, `blinker`

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add `etag` parameter to `send_file` for custom ETag generation

The `send_file` function in `helpers.py` sets the `Last-Modified`
header but does not generate an `ETag` header for cache validation.
Clients cannot use conditional requests (`If-None-Match`) for static
files served via `send_file`. Add an `etag` parameter that, when
`True`, generates an ETag from the file's size and modification time,
and returns a `304 Not Modified` response when the request's
`If-None-Match` header matches.

### N2: Fix `Config.from_mapping` not accepting `ChainMap` instances

The `Config.from_mapping` method in `config.py` accepts any mapping
type but internally iterates with `.items()`, which works for `dict`
but fails silently with `ChainMap` instances that shadow keys across
multiple maps — the method does not deduplicate keys, potentially
setting a value and then overwriting it with a shadowed value. Fix
`from_mapping` to resolve `ChainMap` into a flat dict before iteration.

### N3: Add `response_class` parameter to `jsonify` for custom response types

The `jsonify` function in `json/__init__.py` always returns a
`Response` instance, but applications that use custom response classes
(set via `Flask.response_class`) expect `jsonify` to use their class.
Fix `jsonify` to use `current_app.response_class` instead of the
hard-coded `Response` import when constructing the response object.

### N4: Fix `FlaskCliRunner` not propagating environment variables to commands

The `FlaskCliRunner` in `testing.py` wraps click's `CliRunner` but
does not pass the `env` parameter through to the underlying runner
when invoking Flask CLI commands. Tests that need to set environment
variables (e.g., `FLASK_ENV`, `DATABASE_URL`) for CLI command testing
cannot do so through the runner. Fix `FlaskCliRunner.invoke` to accept
and forward an `env` parameter.

### N5: Add timestamp precision parameter to `SecureCookieSessionInterface`

The `SecureCookieSessionInterface` in `sessions.py` uses
`URLSafeTimedSerializer` from `itsdangerous` for session signing but
does not expose the timestamp precision. The default precision is
seconds, but for high-frequency session updates, sub-second precision
would reduce signature collisions. Add a `timestamp_precision`
parameter to the session interface that configures the serializer's
timestamp resolution.

### N6: Fix `url_for` not encoding unicode path segments correctly

The `url_for` function in `helpers.py` delegates to Werkzeug's URL
building but does not handle unicode path segments that contain
characters requiring percent-encoding beyond ASCII. When a route
parameter contains characters like em-dashes or smart quotes,
`url_for` may produce URLs with un-encoded characters that fail in
strict HTTP clients. Fix `url_for` to apply proper IRI-to-URI encoding
to the generated path.

### N7: Add `flash` message expiration support

The `flash` function in `helpers.py` stores messages in the session
with a category but no expiration mechanism. Messages persist until
retrieved by `get_flashed_messages`, even across multiple requests if
not consumed. Add a `ttl` parameter to `flash` that stores a timestamp
alongside the message, and filter expired messages in
`get_flashed_messages` before returning them.

### N8: Fix `Blueprint` static file serving not respecting `url_prefix`

When a `Blueprint` is registered with a `url_prefix` and has a
`static_folder` configured, the static file URL does not consistently
include the blueprint's URL prefix in all routing scenarios. This
occurs because the static URL rule is added during `Blueprint.register`
in `blueprints.py` before the prefix is fully resolved from the
`BlueprintSetupState` in `sansio/blueprints.py`. Fix the static route
registration order to ensure the prefix is applied.

### N9: Add structured logging format to Flask's default logger

The `create_logger` function in `logging.py` configures a basic
`StreamHandler` with a simple format, but production applications need
structured (JSON) logging. Add a `structured_logging` config key that,
when `True`, replaces the default formatter with a JSON formatter that
includes timestamp, level, logger name, request ID (if available), and
message as structured fields.

### N10: Fix `TaggedJSONSerializer` not handling `set` type correctly

The `TaggedJSONSerializer` in `json/tag.py` handles `dict`, `tuple`,
`bytes`, `Markup`, `UUID`, `datetime`, and `date` types for session
data, but does not handle `set` objects. When a set is stored in the
session, it is serialized as a list and deserialized as a list, losing
the type information. Add a `TagSet` class to `tag.py` that serializes
sets as tagged lists and reconstructs them as sets on deserialization.

## Medium

### M1: Implement request rate limiting middleware

Add a rate limiting system that integrates with Flask's request
lifecycle. Support per-route limits via a `@app.rate_limit("10/minute")`
decorator, per-client tracking using IP or a configurable key function,
in-memory and pluggable storage backends, and proper `429 Too Many
Requests` responses with `Retry-After` headers. Changes touch
`sansio/scaffold.py` (decorator registration), `app.py` (middleware
integration in request dispatch), `helpers.py` (rate limit helpers),
and `wrappers.py` (rate limit response headers).

### M2: Add CORS support as built-in middleware

Implement Cross-Origin Resource Sharing (CORS) support without
requiring a third-party extension. Support per-route CORS configuration
via decorator parameters, preflight `OPTIONS` request handling, and
global CORS configuration via `Flask.config`. Handle
`Access-Control-Allow-Origin`, `Access-Control-Allow-Methods`,
`Access-Control-Allow-Headers`, and `Access-Control-Max-Age` headers.
Changes touch `app.py` (CORS processing in `full_dispatch_request`),
`sansio/scaffold.py` (CORS decorator), `wrappers.py` (CORS response
headers), and `config.py` (CORS configuration keys).

### M3: Implement health check endpoint with dependency probes

Add a built-in `/health` endpoint system that reports application
health with configurable dependency probes (database, cache, external
service). Support `liveness` and `readiness` endpoints following
Kubernetes conventions. Return structured JSON responses with per-probe
status and latency. Changes touch `app.py` (health endpoint
registration), `sansio/app.py` (probe registry), `helpers.py` (probe
execution), and `json/provider.py` (health response formatting).

### M4: Add request context-aware caching for view responses

Implement a `@app.cache(timeout=300)` view decorator that caches
response objects keyed by URL path, query parameters, and configurable
request headers. Support cache invalidation via `app.cache.clear()`
and `app.cache.delete(key)`. Integrate with `before_request` and
`after_request` hooks for cache lookup and storage. Changes touch
`sansio/scaffold.py` (cache decorator), `app.py` (cache integration
in request dispatch), `ctx.py` (cache binding to app context), and
`helpers.py` (cache invalidation API).

### M5: Implement structured error responses for API applications

Add an `APIErrorHandler` that replaces Flask's default HTML error pages
with structured JSON error responses for API endpoints. Support custom
error schemas, error code registration, and content negotiation to
serve HTML or JSON based on the `Accept` header. Changes touch `app.py`
(error handler selection), `sansio/app.py` (API error handler
registration), `helpers.py` (error response helpers), and
`json/provider.py` (error serialization).

### M6: Add WebSocket support via ASGI bridge

Implement basic WebSocket support by adding an ASGI adapter alongside
the existing WSGI interface. Add `@app.websocket("/ws")` decorator for
WebSocket route registration, a `WebSocket` request wrapper with
`send`/`receive`/`close` methods, and integration with Flask's context
system (`g`, `session`). Changes touch `app.py` (ASGI handler),
`sansio/app.py` (WebSocket route registration), `sansio/scaffold.py`
(WebSocket decorator), `wrappers.py` (WebSocket wrapper class),
`ctx.py` (WebSocket request context), and `globals.py` (WebSocket
proxy).

### M7: Implement blueprint versioning for API evolution

Add versioning support to blueprints: `Blueprint("users", __name__,
version="v2")` that automatically prefixes routes with `/v2/`. Support
version negotiation via URL prefix, `Accept` header, or custom header.
Allow multiple blueprint versions to coexist and route to the
appropriate version. Changes touch `blueprints.py` (version parameter),
`sansio/blueprints.py` (versioned registration), `sansio/app.py`
(version negotiation), `helpers.py` (versioned `url_for`), and
`testing.py` (version-aware test client).

### M8: Add request/response lifecycle profiling

Implement a profiling system that measures time spent in each phase of
request handling: routing, before_request hooks, view function,
after_request hooks, template rendering, and response serialization.
Expose results via `Server-Timing` headers and an optional profiling
dashboard endpoint. Changes touch `app.py` (timing instrumentation in
`full_dispatch_request`), `sansio/app.py` (hook timing), `templating.py`
(render timing), `wrappers.py` (Server-Timing header), and `helpers.py`
(profiling configuration).

### M9: Implement database-backed session storage

Add a `DatabaseSessionInterface` as an alternative to the cookie-based
default. Store session data in a database table via a pluggable
backend (SQLAlchemy, Redis, Memcached). Support session expiration,
server-side session size limits (bypassing cookie size restrictions),
and session locking for concurrent requests. Changes touch `sessions.py`
(new session interface), `app.py` (session interface configuration),
`json/tag.py` (session data serialization), and `config.py` (session
storage configuration keys).

### M10: Add async view function support with sync/async detection

Implement automatic detection and handling of `async def` view
functions. When an async view is registered, Flask should run it in
an event loop (or the existing loop if running under an ASGI server).
Support async `before_request`, `after_request`, and `teardown_request`
hooks. Changes touch `app.py` (async dispatch in `ensure_sync` and
`full_dispatch_request`), `sansio/scaffold.py` (async decorator
handling), `sansio/app.py` (async hook registration), `ctx.py` (async
context management), and `views.py` (async `MethodView`).

## Wide

### W1: Implement a full ASGI adapter alongside WSGI

Add first-class ASGI support to Flask so it can run under ASGI servers
(uvicorn, daphne) with full feature parity. Implement an ASGI
application callable alongside the WSGI callable, async request/
response handling, async context management, async signal dispatching,
server-sent events, and long-polling support. Changes span `app.py`
(ASGI application callable), `wrappers.py` (async Request/Response),
`ctx.py` (async-safe context stack), `globals.py` (async context
vars), `sessions.py` (async session loading), `signals.py` (async
signal dispatch), `helpers.py` (async stream helpers), and `testing.py`
(async test client).

### W2: Implement a comprehensive API framework layer

Add built-in REST API functionality: resource classes with automatic
CRUD routing, request body validation with marshmallow-style schemas,
response serialization, pagination helpers, filtering and sorting
query parameter parsing, HATEOAS link generation, and OpenAPI schema
auto-generation. Changes span `views.py` (Resource base class),
`sansio/app.py` (resource registration), `sansio/scaffold.py`
(resource decorators), `wrappers.py` (request parsing), `helpers.py`
(pagination and HATEOAS), `json/provider.py` (API serialization), and
new `api.py` and `openapi.py` modules.

### W3: Implement a plugin system with lifecycle management

Add a plugin architecture that discovers, loads, and manages Flask
extensions with proper lifecycle hooks. Support plugin discovery via
entry points, dependency ordering between plugins, conflict detection,
configuration validation per plugin, and a CLI command to list and
manage plugins. Changes span `app.py` (plugin loading and lifecycle),
`sansio/app.py` (plugin registry), `config.py` (per-plugin
configuration namespaces), `cli.py` (plugin management commands),
`blueprints.py` (plugin-provided blueprints), `signals.py` (plugin
lifecycle signals), and a new `plugins.py` module.

### W4: Add comprehensive request validation framework

Implement a request validation system that validates request bodies,
query parameters, headers, and path parameters against declarative
schemas. Support JSON Schema validation, type coercion, nested object
validation, file upload validation, and detailed error responses with
field-level errors. Changes span `wrappers.py` (validation methods on
Request), `sansio/scaffold.py` (validation decorator), `app.py`
(validation error handling), `helpers.py` (validation helpers),
`json/provider.py` (error response formatting), `debughelpers.py`
(validation debug info), and a new `validation.py` module.

### W5: Implement background task execution system

Add a built-in background task system that allows view functions to
enqueue work for asynchronous execution. Support task queuing with
configurable backends (in-memory, Redis, database), task status
tracking, result retrieval, task retry with backoff, periodic task
scheduling, and a CLI command for running workers. Changes span
`app.py` (task queue initialization), `helpers.py` (task enqueue API),
`ctx.py` (task context propagation from request), `cli.py` (worker
command), `config.py` (task queue configuration), `signals.py` (task
lifecycle signals), and a new `tasks.py` module for task infrastructure.

### W6: Add multi-tenancy support with tenant isolation

Implement multi-tenant support where a single Flask application serves
multiple tenants with isolated configuration, database connections,
templates, and static files. Support tenant detection from subdomain,
URL prefix, or custom header. Add per-tenant middleware, configuration
overlays, and template loader namespacing. Changes span `app.py`
(tenant middleware in request dispatch), `config.py` (tenant config
overlays), `ctx.py` (tenant context), `globals.py` (tenant proxy),
`templating.py` (tenant-aware template loader), `sessions.py`
(tenant-scoped sessions), `blueprints.py` (tenant-aware blueprints),
and a new `tenancy.py` module.

### W7: Implement a real-time event system with SSE and WebSocket

Add a real-time event broadcasting system that supports both Server-
Sent Events (SSE) and WebSocket transports. Include event channels with
pub/sub semantics, client connection management, event replay from
history, authentication integration, and scale-out via Redis pub/sub.
Changes span `app.py` (event system initialization), `sansio/app.py`
(event route registration), `sansio/scaffold.py` (event decorators),
`wrappers.py` (SSE response class), `helpers.py` (event publishing
API), `ctx.py` (event context), `testing.py` (event testing client),
and a new `events.py` module.

### W8: Add internationalization and localization framework

Implement comprehensive i18n/l10n support: translatable strings via
gettext integration, locale detection from Accept-Language header and
user preferences, locale-aware URL routing, date/time/number
formatting, timezone handling, and translation extraction CLI commands.
Changes span `app.py` (locale middleware), `helpers.py` (translation
helpers and locale-aware formatters), `cli.py` (extraction commands),
`templating.py` (Jinja2 i18n extension integration), `wrappers.py`
(locale property on Request), `config.py` (i18n configuration), and
a new `i18n.py` module for locale management and translation loading.

### W9: Implement a comprehensive test utilities framework

Extend `testing.py` into a full testing framework: test fixtures for
app factory patterns, database transaction rollback between tests,
mock service injection, request recording and replay, snapshot testing
for JSON responses and HTML templates, authentication helpers, and a
pytest plugin with Flask-specific fixtures and markers. Changes span
`testing.py` (enhanced client and fixtures), `app.py` (test mode
configuration), `ctx.py` (test context management), `sessions.py`
(test session manipulation), `json/provider.py` (snapshot
serialization), and a new `pytest_plugin.py` module.

### W10: Add automatic admin interface generation

Implement an auto-generated admin interface that discovers registered
models and generates CRUD views. Support list views with filtering and
sorting, detail views with form inputs, relationship navigation,
bulk actions, role-based access control, audit logging, and a dashboard
with application metrics. Changes span `app.py` (admin blueprint
registration), `blueprints.py` (admin blueprint), `views.py` (admin
`MethodView` subclasses), `templating.py` (admin templates), `helpers.py`
(admin URL generation), `json/provider.py` (admin API endpoints), and
new `admin/` sub-package with modules for views, forms, filters, and
dashboard.
