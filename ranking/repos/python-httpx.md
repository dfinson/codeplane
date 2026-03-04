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

8 tasks (3 narrow, 3 medium, 2 wide) for the Python HTTP client library.

## Narrow

### N1: Fix timeout not propagated on redirect

When a request with a custom timeout follows a 3xx redirect, the timeout
configuration from the original request is not carried forward to the
redirected request. The redirected request falls back to the client's default
timeout instead. Fix the redirect handling to preserve the per-request timeout
across redirects.

### N2: Add support for reading `.netrc` credentials

httpx should be able to read `~/.netrc` files for automatic authentication
when no explicit auth is provided. Implement a `.netrc` lookup that runs
after explicit auth checks but before sending unauthenticated requests.
Respect the `NETRC` environment variable for custom file paths.

### N3: Fix Content-Length header not removed on 303 redirect

When a POST request gets a 303 See Other redirect, the method changes to GET
and the body is dropped — but the `Content-Length` header from the original
POST is still present in the redirected GET request. Strip `Content-Length`
(and `Content-Type`) headers when the method changes due to a 303 redirect.

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

### M3: Add request/response event hooks

Add an event hook system that fires callbacks at key points in the request
lifecycle: before-request, after-response, on-error, on-redirect. Hooks
should be configurable at both the client level and per-request level.
Per-request hooks should run in addition to (not replacing) client-level
hooks. Async hooks should be supported in the async client.

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
