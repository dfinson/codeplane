# pallets/flask

| Field | Value |
|-------|-------|
| **URL** | https://github.com/pallets/flask |
| **License** | BSD-3-Clause |
| **Language** | Python |
| **Scale** | Medium |
| **Category** | Web microframework |
| **Set** | Cutoff |
| **Commit** | `4cae5d8e411b1e69949d8fae669afeacbd3e5908` |

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

### N1: Extend `send_file` `etag` parameter to accept a callable

The `send_file` function in `helpers.py` accepts `etag: bool | str = True`,
where `True` auto-generates an ETag from the file's modification time and
size, and a string literal is used as the ETag value directly. There is no
way to compute an ETag dynamically — for example by hashing file content or
combining the path with an application version — without calling `send_file`
with `etag=False` and manually setting the header on the returned response.
Extend the `etag` parameter to also accept a `Callable[[str], str]` that
receives the resolved absolute file path and returns the ETag string. Update
`_prepare_send_file_kwargs` in `helpers.py` to evaluate the callable and
resolve it to a string before delegating to Werkzeug's `send_file`.

### N2: Add `**load_kwargs` forwarding to `Config.from_file`

The `Config.from_file` method in `config.py` accepts a `load` callable
but provides no mechanism to pass additional keyword arguments to it.
Users who need loader options — for example, `json.load` with
`parse_float=decimal.Decimal` for precise numeric handling, or a custom
TOML parser that accepts an `encoding` argument — must wrap their
callable with `functools.partial` before passing it to `from_file`. Add
a `**load_kwargs` parameter to `Config.from_file` that is forwarded
verbatim to the `load` callable at invocation time.

### N3: Add configurable `indent` to `DefaultJSONProvider` pretty-printing

The `DefaultJSONProvider.response()` method in `json/provider.py`
hard-codes an indentation of `2` spaces when non-compact (pretty-printed)
JSON output is produced. Applications that prefer a different indentation
depth — for example `4` spaces for readability, or `0` to produce
single-line output without compact separators — cannot customize this
without subclassing `DefaultJSONProvider` and overriding the entire
`response()` method. Add an `indent` class attribute to
`DefaultJSONProvider` defaulting to `2`, and replace the literal `2` in
`response()` with a reference to `self.indent`.

### N4: Add `use_script_info` parameter to `FlaskCliRunner.invoke`

The `FlaskCliRunner` in `testing.py` automatically injects a
`ScriptInfo` instance as the `obj` parameter of every `invoke` call,
even when testing a plain Click command that was attached to the app's
`cli` group but does not use `ScriptInfo` as its context object. When
such a command supplies its own `obj` via `@pass_context` or
`standalone_mode=False`, the automatic injection silently overwrites
the caller-supplied value. Add a `use_script_info` keyword argument
(defaulting to `True`) to `FlaskCliRunner.invoke` that, when `False`,
skips the automatic `ScriptInfo` injection so the caller-supplied `obj`
— or `None` — is used as-is.

### N5: Add `SESSION_COOKIE_SALT` config key to `SecureCookieSessionInterface`

The `SecureCookieSessionInterface` in `sessions.py` derives its signing
salt from the `salt` class attribute (`'cookie-session'` by default).
While the attribute can be overridden by subclassing, there is no way to
configure the salt via `app.config` without deploying a custom session
interface subclass. This matters for applications that share a
`SECRET_KEY` across multiple services and need distinct per-service
salts to prevent session tokens from being replayed cross-service. Add
support for a `SESSION_COOKIE_SALT` configuration key: in
`get_signing_serializer`, read `app.config.get('SESSION_COOKIE_SALT')`
and use it as the salt when present, falling back to the class-level
`salt` attribute.

### N6: Add `_blueprint` parameter to `url_for` for out-of-request endpoint resolution

The `url_for` function in `helpers.py` and `app.url_for` in `app.py`
support dot-prefixed endpoints (e.g., `url_for('.index')`) that are
resolved relative to the current request's blueprint. Outside a request
context — for example, in background tasks or email generation — there is
no active blueprint, so dot-prefixed endpoints raise a `BuildError` with
no useful indication that a blueprint prefix is required. Add a `_blueprint`
keyword parameter to both `helpers.url_for` and `Flask.url_for` that, when
provided, is prepended to a dot-prefixed endpoint name to resolve it, making
blueprint-relative URL generation usable outside of a request context.

### N7: Add `flash` message expiration support

The `flash` function in `helpers.py` stores messages in the session
with a category but no expiration mechanism. Messages persist until
retrieved by `get_flashed_messages`, even across multiple requests if
not consumed. Add a `ttl` parameter to `flash` that stores a timestamp
alongside the message, and filter expired messages in
`get_flashed_messages` before returning them.

### N8: Add `host`/`subdomain` conflict validation to `BlueprintSetupState`

The `BlueprintSetupState.__init__` in `sansio/blueprints.py` reads
`subdomain` from registration options and stores it as `self.subdomain`,
but does not handle the `host` keyword argument. Werkzeug URL rules
treat `host` and `subdomain` as mutually exclusive: passing both to a
`Rule` raises a cryptic `AssertionError` deep in Werkzeug's routing
layer with no indication that blueprint registration was the source of
the conflict. Add a validation check in `BlueprintSetupState.__init__`
that raises `ValueError` with a descriptive message when both `host`
and `subdomain` are present in the registration options, and extract
`host` into `self.host` (parallel to `self.subdomain`) so it is applied
consistently in `add_url_rule`.

### N9: Add structured logging format to Flask's default logger

The `create_logger` function in `logging.py` configures a basic
`StreamHandler` with a simple format, but production applications need
structured (JSON) logging. Add a `structured_logging` config key that,
when `True`, replaces the default formatter with a JSON formatter that
includes timestamp, level, logger name, request ID (if available), and
message as structured fields.

### N10: Fix `TaggedJSONSerializer` not handling `set` type correctly

The `TaggedJSONSerializer` in `json/tag.py` handles `dict`, `tuple`,
`bytes`, `Markup`, `UUID`, and `datetime` types for session
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
`json/provider.py` (error serialization). Also update `README.md` to add an API error handling example in the quickstart section.

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

### M10: Add per-method request hooks and `MethodView.method_decorators`

Add method-level `before_request` and `after_request` hooks that run
only for specific HTTP methods, and a matching `method_decorators` class
variable on `MethodView`. Currently `before_request` and `after_request`
hooks run for every HTTP method; applications that need authentication
only on write methods must inspect `request.method` inside the hook.
Add `before_method_request(methods)` and `after_method_request(methods)`
decorator factories to `sansio/scaffold.py` that register hooks scoped
to a list of HTTP methods, and store them in a new
`before_method_request_funcs` / `after_method_request_funcs` dict in
`sansio/app.py`. Apply them in `app.py`'s `full_dispatch_request` after
the unscoped hooks. Also add a `method_decorators: ClassVar[dict[str,
list[Callable]]]` attribute to `MethodView` in `views.py`, applied in
`dispatch_request` before calling the handler. Merge method-scoped hook
registries during blueprint registration in `sansio/blueprints.py`.
Changes touch `sansio/scaffold.py`, `sansio/app.py`, `app.py`,
`views.py`, and `sansio/blueprints.py`.

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

### N11: Expand deprecation notices and migration notes in `CHANGES.rst`

The `CHANGES.rst` unreleased section for version 3.2.0 contains some
deprecation announcements (`RequestContext` alias, `should_ignore_error`)
but does not document planned removal timelines, migration alternatives, or
the broader set of APIs scheduled for removal. Add entries to the existing
unreleased section documenting the full set of deprecated APIs introduced in
3.2.0 — including the old `AppContext`/`RequestContext` split, the old
dispatch method signatures that accepted no `AppContext` parameter, and any
other APIs marked with deprecation warnings in the source — specifying their
replacements and the version in which removal is planned. Use consistent
reStructuredText formatting with `:issue:` cross-references and
`:user:` contributor attribution on each entry.

### M11: Add pip-compatible extras to `pyproject.toml` and extend pre-commit hooks

The `pyproject.toml` uses `[dependency-groups]` (PEP 735, requires uv)
for `dev`, `docs`, `tests`, and `typing` groups but does not expose
corresponding pip-installable extras via `[project.optional-dependencies]`.
Users with standard pip cannot install development, documentation, or
testing dependencies without uv. Add `[project.optional-dependencies]`
entries for `dev`, `test`, and `docs` that mirror the corresponding
`[dependency-groups]` contents. Update `.readthedocs.yaml` to add a
`formats` key listing additional documentation output formats and
configure the `submodules` policy. Add a `blacken-docs` pre-commit hook
to `.pre-commit-config.yaml` to auto-format Python code blocks embedded
in reStructuredText and Markdown documentation files.

### W11: Comprehensive configuration and documentation overhaul

Perform a full non-code project hygiene refresh. Update `pyproject.toml`
classifiers to add Python version classifiers (`Programming Language ::
Python :: 3.10` through `3.13`) that reflect the supported range declared
in `requires-python`. Revise `CHANGES.rst` to apply consistent
reStructuredText formatting across all versions: entries should use
`:issue:` and `:pr:` cross-references and `:user:` attribution where
missing. Update `README.md` quickstart examples to show the modern
method-based route decorators (`app.get`, `app.post`) and add type
annotations to the example view functions. Configure `.readthedocs.yaml`
for multi-version documentation builds by adding a `versions` block that
builds both `stable` and `latest`. Add documentation-related hooks to
`.pre-commit-config.yaml` (`blacken-docs` for code blocks in docs,
`doc8` for RST style checks). Revise `.editorconfig` to add sections for
`.j2`, `.jinja`, and `.jinja2` template file extensions with indent
settings that match the existing HTML rule.
