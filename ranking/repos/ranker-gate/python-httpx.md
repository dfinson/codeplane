# encode/httpx

| Field | Value |
|-------|-------|
| **URL** | https://github.com/encode/httpx |
| **License** | BSD-3-Clause |
| **Language** | Python |
| **Scale** | Small (focused library) |
| **Category** | HTTP client library |

## Why this repo

- **Single-purpose**: Async/sync HTTP client for Python. One developer can hold
  the entire codebase in their head.
- **Well-structured**: Clean separation between transport layer, client API,
  auth, middleware, decoders. Source in `httpx/` with ~50 core modules.
- **Rich history**: Active development since 2019, ~3K commits, regular PRs
  with meaningful code review. Used by FastAPI and many production systems.
- **Permissive**: BSD-3-Clause, fully usable for training data.

## Structure overview

```
httpx/
├── _api.py              # Top-level convenience functions
├── _client.py           # Client and AsyncClient classes
├── _auth.py             # Authentication flows
├── _transports/         # HTTP/1.1, HTTP/2, mock transports
├── _decoders.py         # Content decoders
├── _models.py           # Request/Response models
├── _urls.py             # URL parsing
├── _content.py          # Request body encoding
└── _config.py           # SSL, timeout, proxy config
```

## Scale indicators

- ~50 Python source files
- ~15K lines of code
- Clear module boundaries, no deep nesting
- Minimal dependencies (httpcore, anyio)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `Content-Type` header not stripped when POST becomes GET on redirect

When a POST request receives a 303 See Other (or a 302/301 that converts to
GET), `_redirect_headers` in `_client.py` correctly strips `Content-Length`
and `Transfer-Encoding`, but leaves the `Content-Type` header intact. A
redirected GET request that carries `Content-Type: application/json` with no
body is semantically incorrect and confuses some servers. Add `Content-Type`
to the set of headers stripped in `_redirect_headers` when the method changes
to GET.

### N2: Add `reason_phrase` fallback for non-standard status codes

`codes.get_reason_phrase()` in `_status_codes.py` returns an empty string
for any status code not in the `codes` IntEnum (e.g., 499, 520). When such
a response is printed via `Response.__repr__` or used in error messages, the
empty reason phrase produces output like `<Response [499 ]>`. Add a
class-based fallback in `get_reason_phrase` — e.g., `"Informational"` for
1xx, `"Success"` for 2xx, `"Redirection"` for 3xx, `"Client Error"` for
4xx, `"Server Error"` for 5xx — instead of returning an empty string.

### N3: Fix `Client.__exit__` not ensuring proxy transport cleanup on error

In both `Client.__exit__` and `AsyncClient.__aexit__`, transport cleanup
calls are not wrapped in `try/finally`. If `self._transport.__exit__()` (or
its async variant) raises an exception, the proxy transports in
`self._mounts` are never closed, leaking connections. Wrap the cleanup
sequence so that all transports are closed even if one of them raises during
shutdown.

### N4: Add `max_redirects` per-request override

The `max_redirects` setting is client-level only (set in the `Client`
constructor). Unlike `follow_redirects` and `timeout`, which can be
overridden per-request through `client.get(url, follow_redirects=...)` or
`client.get(url, timeout=...)`, there is no per-request `max_redirects`
parameter. Add `max_redirects` as an optional keyword argument on `send()`,
`request()`, `stream()`, and the convenience methods (`get`, `post`, etc.)
in both `Client` and `AsyncClient`. When provided, it should override the
client-level default for that single request.

### N5: Implement `auth-int` Quality of Protection in `DigestAuth`

`DigestAuth._resolve_qop` in `_auth.py` raises `NotImplementedError` when
the server requires `qop=auth-int`. Implement `auth-int` support per
RFC 7616 §3.4.3 by changing the A2 computation to
`method:uri:H(entity-body)` (where `H` is the digest hash of the request
body). The code currently has a `# TODO: implement auth-int` comment and
computes A2 as `method:uri` for `qop=auth` only. The request body is
available via `request.content`.

### N6: Add `BearerAuth` authentication class

The auth module (`_auth.py`) provides `BasicAuth`, `DigestAuth`,
`FunctionAuth`, and `NetRCAuth`, but has no built-in class for OAuth 2.0
Bearer token authentication. Add a `BearerAuth(Auth)` class that sets the
`Authorization: Bearer <token>` header on outgoing requests. Support an
optional `token_getter` callable parameter: when provided, if the initial
request gets a 401 response, `BearerAuth` should call `token_getter()` to
obtain a fresh token and retry the request. Export `BearerAuth` in
`__init__.py`.

### N7: Add `default_encoding` parameter to top-level convenience functions

The `Client` and `AsyncClient` constructors accept a `default_encoding`
parameter that controls how response text is decoded when the Content-Type
header doesn't specify a charset. The top-level convenience functions
(`httpx.get()`, `httpx.post()`, `httpx.request()`, `httpx.stream()`, etc.)
in `_api.py` create a temporary `Client` but do not pass `default_encoding`
through. Add `default_encoding` as an optional parameter on all functions
in `_api.py` and forward it to the `Client` constructor.

### N8: Add `secure`, `expires`, and `httponly` parameters to `Cookies.set()`

`Cookies.set()` in `_models.py` accepts only `name`, `value`, `domain`, and
`path`. The underlying `Cookie` kwargs hardcode `secure=False`,
`expires=None`, and `rest={"HttpOnly": None}` with no way for users to
control these attributes. Add optional `secure: bool = False`,
`expires: int | None = None`, and `httponly: bool = False` parameters to
`Cookies.set()`, wiring them through to the `Cookie` constructor call.

### N9: Fix `WSGITransport` not forwarding reason phrase from WSGI status line

`WSGITransport.handle_request()` in `_transports/wsgi.py` parses the WSGI
status string (e.g., `"200 OK"`) and extracts only the integer status code
via `int(seen_status.split()[0])`, discarding the reason phrase. The
returned `Response` has no `extensions` dict, so `response.reason_phrase`
falls back to the built-in lookup in `_status_codes.py` rather than using
the actual phrase from the WSGI app. Parse the reason phrase from the status
string and include it (along with `http_version`) in
`Response(extensions={"reason_phrase": ..., "http_version": ...})`.

### N10: Fix `_guess_content_type` returning `None` for files without extensions

In `_multipart.py`, `_guess_content_type(filename)` returns `None` when
`mimetypes.guess_type()` cannot determine the type — for example when the
filename has no extension (like `"upload"` or `"Makefile"`). When this
happens, `FileField` omits the `Content-Type` header entirely from that
multipart part, which is non-conformant with RFC 2388 §3 (which specifies
a default of `application/octet-stream`). Add a fallback to
`application/octet-stream` in `_guess_content_type` when the file content
is binary and the type cannot be guessed from the filename.

## Medium

### M1: Add HTTP/2 server push support

Implement support for HTTP/2 server push in the async client. When the server
sends a PUSH_PROMISE frame, the client should accept the pushed response and
make it available through a new `pushed_responses` attribute on the response
object. Include configuration to disable server push and to set a maximum
number of concurrent pushes.

### M2: Implement response streaming with backpressure

The current streaming implementation (`aiter_bytes`, `aiter_lines`) does not
apply backpressure to the underlying transport when the consumer is slow.
Add flow control so that when the async iterator consumer pauses, the
transport layer stops reading from the socket. This requires changes to the
transport interface, the response streaming API, and the HTTP/1.1 and HTTP/2
transport implementations.

### M3: Extend event hooks with `on_error` and `on_redirect` lifecycle events

The existing event hook system in `_client.py` supports only `request` and
`response` hooks (registered via `event_hooks={"request": [...], "response":
[...]}`). Add two new hook types: `on_error` hooks that fire when a request
fails with a `TransportError` or other exception (receiving the request and
the exception), and `on_redirect` hooks that fire when a redirect is followed
(receiving the original request, the redirect response, and the new URL).
Update the `_event_hooks` dict initialization, the `event_hooks` property
setter validation, the redirect handling loop in
`_send_handling_redirects`, and the error paths in `_send_handling_auth`.
Support async hook callables in `AsyncClient`.

### M4: Implement connection pool warm-up

Add a `client.warm(urls)` method that pre-establishes connections to a
list of hosts without sending requests. The warmed connections should
be available in the pool for subsequent requests. Support both HTTP/1.1
and HTTP/2, including TLS handshake completion. Add async variant.

### M5: Add response caching with conditional requests

Implement a `CacheTransport` wrapper that caches responses and
automatically sends conditional requests (If-None-Match/If-Modified-Since)
on subsequent requests. Respect Cache-Control directives for cache
validity. Support both memory and disk storage backends. The cache
should work with both `Client` and `AsyncClient`.

### M6: Implement cookie persistence to disk

Add a `PersistentCookieJar` that saves and loads cookies to/from a file
(Netscape cookie format or JSON). Support expiration handling, domain
scoping, and secure-only cookies. The jar should auto-save on changes
and load on initialization. Add a `clear_session_cookies()` method.

### M7: Add transport-level request/response logging wrapper

Implement a `LoggingTransport` that wraps any `BaseTransport` or
`AsyncBaseTransport` and logs structured information about each
request/response cycle using Python's `logging` module. Log the request
method, URL, headers (with sensitive headers obfuscated using the existing
`_obfuscate_sensitive_headers` helper from `_models.py`), response status,
response headers, and elapsed time. Support configurable log level and
logger name. Add a `log_transport: bool = False` option to `Client` and
`AsyncClient` that automatically wraps the configured transport with
`LoggingTransport`. Place the new transport in `_transports/logging.py`.

### M8: Implement request signing for AWS Signature V4

Add an `AWS4Auth` authentication class that signs requests using AWS
Signature Version 4. Support all HTTP methods, query string signing,
chunked upload signing, and presigned URLs. The implementation should
handle the canonical request construction, string-to-sign generation,
and signature calculation per the AWS specification.

### M9: Implement multipart upload with progress tracking

Add progress callbacks for multipart file uploads. The callback should
receive bytes sent so far and total bytes. Support per-part progress
and overall progress. Work with both sync and async clients. The
progress reporting should not significantly impact upload throughput.

### M10: Add automatic retry with configurable policy

Implement request retry support with a `RetryTransport` that wraps the
base transport. Support configurable retry count, backoff strategy
(fixed, exponential, exponential with jitter), retryable status codes,
retryable exception types, and per-request retry override. Respect
`Retry-After` headers.

## Wide

### W1: Migrate from httpcore to native transport layer

Replace the dependency on `httpcore` with a native transport implementation
built on top of `anyio` directly. This affects the HTTP/1.1 transport,
HTTP/2 transport, connection pooling, proxy handling, and the transport
interface itself. The public API should remain unchanged — this is an
internal implementation change.

### W2: Add comprehensive request tracing and diagnostics

Implement a request tracing system that captures detailed timing information
for each phase of a request: DNS resolution, TCP connect, TLS handshake,
request send, TTFB (time to first byte), and content transfer. Surface
this through a `request.extensions["trace"]` dict. Add structured logging
integration that emits trace events. Update the mock transport to support
trace simulation for testing.

### W3: Add HTTP/3 (QUIC) transport support

Implement an HTTP/3 transport using the QUIC protocol. Support 0-RTT
connection resumption, connection migration on network change, and
Alt-Svc header parsing for HTTP/3 discovery. Fall back to HTTP/2 when
QUIC is unavailable. This requires a new transport implementation,
connection pool changes for QUIC connections, and Alt-Svc response
processing in the response handling pipeline.

### W4: Implement a comprehensive middleware system

Add a middleware layer between the client API and transport. Middleware
can intercept requests before sending and responses after receiving.
Built-in middleware: logging, metrics (timing, status counts), rate
limiting, circuit breaker. Support both sync and async middleware.
Middleware ordering should be explicit. This touches the client,
transport interface, and adds a new middleware package.

### W5: Add WebSocket client support

Implement WebSocket support in httpx. Add `client.ws_connect(url)` that
returns a `WebSocketConnection` with `send_text()`, `send_bytes()`,
`receive()`, `close()`, and async iteration. Support ping/pong,
compression (permessage-deflate), subprotocol negotiation, and
connection upgrade from HTTP/1.1. Handle both sync and async clients.

### W6: Implement HAR (HTTP Archive) export

Add the ability to record all HTTP traffic through the client and
export it as HAR format. Implement a `HARTransport` wrapper that
captures request/response details including timing, headers, body
content (with size limits), TLS info, and connection reuse status.
Support both recording and playback (useful for testing). Changes
span the transport layer, response model (timing metadata), and
a new HAR serialization module.

### W7: Add per-request SSL context selection and TLS diagnostics

The current SSL configuration in `_config.py` uses a single
`create_ssl_context()` at client construction time, shared by all requests.
Implement per-request SSL context override via a new
`request.extensions["ssl_context"]` key, allowing different client
certificates and verification settings for different endpoints. Add a
`CertificatePool` class in `_config.py` that maps URL patterns to SSL
contexts, integrated with the existing `_mounts` routing system in
`_client.py`. Expose TLS connection metadata (peer certificate, cipher
suite, TLS version) on responses via `response.extensions["tls_info"]` by
extending the `HTTPTransport` and `AsyncHTTPTransport` wrappers in
`_transports/default.py`. Update the `WSGITransport` and `ASGITransport`
to populate stub TLS info for testing. This crosses `_config.py`,
`_types.py`, `_client.py`, `_transports/default.py`, `_transports/wsgi.py`,
`_transports/asgi.py`, and `_models.py`.

### W8: Implement HTTP caching proxy mode

Add a mode where httpx acts as a caching HTTP proxy server. The proxy
receives requests from other applications, forwards them to the upstream
server (using httpx's transport), caches responses, and serves cached
responses for subsequent matching requests. This requires a proxy server
component, the caching transport, request matching logic, and
cache invalidation through the admin API.

### W9: Add distributed tracing integration (OpenTelemetry)

Implement automatic OpenTelemetry span creation for every HTTP request.
Create spans with standard HTTP semantic conventions (url, method,
status_code, etc.). Propagate trace context via W3C traceparent headers.
Support context injection for outgoing requests and extraction for
incoming responses. Include spans for DNS resolution, TCP connect, TLS
handshake, and content transfer sub-operations. This crosses the
transport layer, client API, and adds a tracing integration module.

### W10: Implement connection pool monitoring and diagnostics

Add a comprehensive connection pool monitoring system. Track per-host
metrics: active/idle/connecting counts, connection ages, request
queue depths, TLS session resumption rates, and HTTP/2 stream
utilization. Expose via a `client.pool_status()` method returning
structured data. Add an async event stream for pool state changes.
Support export to Prometheus format. This touches the connection pool,
transport layer, client API, and adds a monitoring module.
