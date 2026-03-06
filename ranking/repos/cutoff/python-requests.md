# psf/requests

| Field | Value |
|-------|-------|
| **URL** | https://github.com/psf/requests |
| **License** | Apache-2.0 |
| **Language** | Python |
| **Scale** | Medium |
| **Category** | HTTP library |
| **Set** | Cutoff |
| **Commit** | `0e4ae38f0c93d4f92a96c774bd52c069d12a4798` |

## Why this repo

- **Well-structured**: Clean `src/requests/` layout with clearly separated
  concerns — session management (`sessions.py`), request/response models
  (`models.py`), transport adapters (`adapters.py`), authentication
  (`auth.py`), cookie handling (`cookies.py`), utilities (`utils.py`),
  and exception hierarchy (`exceptions.py`). A developer can trace the
  full lifecycle from `requests.get()` through `Session.request()` to
  `HTTPAdapter.send()`.
- **Rich history**: 6K+ commits, 52K+ stars. The most widely used
  Python HTTP library. PRs cover redirect handling, encoding edge cases,
  proxy support, and security fixes.
- **Permissive**: Apache-2.0 license.

## Structure overview

```
src/requests/
├── __init__.py            # Public API re-exports
├── __version__.py         # Version string
├── _internal_utils.py     # Internal string/encoding helpers
├── adapters.py            # HTTPAdapter, BaseAdapter — transport layer over urllib3
├── api.py                 # Module-level get/post/put/delete/head/options/patch
├── auth.py                # AuthBase, HTTPBasicAuth, HTTPDigestAuth, HTTPProxyAuth
├── certs.py               # Default CA bundle location
├── compat.py              # Python version compatibility shims
├── cookies.py             # RequestsCookieJar, cookie extraction/merging
├── exceptions.py          # Exception hierarchy — Timeout, ConnectionError, etc.
├── help.py                # Debug/environment info helper
├── hooks.py               # Event hook dispatch system
├── models.py              # Request, PreparedRequest, Response
├── packages.py            # Vendored package re-exports
├── sessions.py            # Session, SessionRedirectMixin, merge_setting
├── status_codes.py        # HTTP status code lookup table
├── structures.py          # CaseInsensitiveDict, LookupDict
└── utils.py               # URL parsing, proxy resolution, encoding detection
```

## Scale indicators

- ~18 Python source files
- ~5.6K lines of code
- Flat structure (single package under `src/requests/`)
- Primary dependency: `urllib3`

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add elapsed time breakdown to Response object

The `Response` object in `models.py` exposes `elapsed` as a single
`timedelta` covering the entire request, but does not break it down into
DNS resolution, connection establishment, TLS handshake, and
time-to-first-byte. Add `elapsed_connect` and `elapsed_ttfb` attributes
to `Response` and populate them in `HTTPAdapter.build_response()` in
`adapters.py` by extracting timing data from the urllib3 response.

### N2: Fix merge_setting not deep-merging nested proxy dictionaries

The `merge_setting()` function in `sessions.py` merges session-level
and request-level settings, but when both `session.proxies` and
per-request `proxies` contain nested keys (e.g., `no_proxy`), only the
top-level keys are merged. Values from the session dict are silently
dropped when the request dict has the same top-level key. Fix
`merge_setting()` to recursively merge nested `Mapping` values.

### N3: Add Bearer token authentication class

The `auth.py` module provides `HTTPBasicAuth`, `HTTPDigestAuth`, and
`HTTPProxyAuth`, but has no built-in support for Bearer token
authentication (RFC 6750). Add an `HTTPBearerAuth(AuthBase)` class that
sets the `Authorization: Bearer <token>` header, supporting both static
tokens and a callable that returns a token for dynamic refresh.

### N4: Add Retry-After header parsing to TooManyRedirects exception

When a server responds with `429 Too Many Requests` and a `Retry-After`
header, the `SessionRedirectMixin.resolve_redirects()` method in
`sessions.py` raises `TooManyRedirects` without exposing the retry
delay. Add a `retry_after` attribute to the `TooManyRedirects` exception
in `exceptions.py` and populate it by parsing the `Retry-After` header
(both delta-seconds and HTTP-date formats) in `resolve_redirects()`.

### N5: Fix CaseInsensitiveDict.copy() returning a plain dict-like copy

The `CaseInsensitiveDict.copy()` method in `structures.py` returns a
new `CaseInsensitiveDict` constructed from its own items, but it does
not preserve the internal `_store` ordering correctly when keys were
set with different casings. Fix `copy()` to clone the internal `_store`
`OrderedDict` directly so the copy preserves original insertion order
and casing.

### N6: Add cookie domain validation to RequestsCookieJar.set()

The `RequestsCookieJar.set()` method in `cookies.py` accepts arbitrary
domain values without validating them against public suffix rules. A
cookie set with `domain=".com"` is silently accepted and sent to every
`.com` host. Add domain validation to `set()` that rejects overly broad
domains (effective TLDs) and raises `CookieConflictError`.

### N7: Fix check_header_validity not detecting non-ASCII header names

The `check_header_validity()` function in `utils.py` validates header
names and values using regex patterns, but the name pattern allows
non-ASCII bytes that violate RFC 7230 Section 3.2.6. When a header
name contains characters like `ü` or `é`, no `InvalidHeader` exception
is raised. Tighten the `_CLEAN_HEADER_REGEX_BYTE` and
`_CLEAN_HEADER_REGEX_STR` patterns to reject non-ASCII characters in
header names.

### N8: Add SOCKS proxy authentication support to resolve_proxies

The `resolve_proxies()` function in `utils.py` resolves proxy URLs from
environment variables and the request, but does not extract embedded
credentials from `socks5://user:pass@host:port` URLs for passing to the
SOCKS proxy manager. Fix `resolve_proxies()` to parse credentials from
SOCKS proxy URLs and set them as proxy authentication, similar to how
HTTP proxy credentials are handled via `get_auth_from_url()`.

### N9: Fix extract_zipped_paths silently failing on corrupted ZIP entries

The `extract_zipped_paths()` function in `utils.py` handles paths inside
ZIP archives (e.g., for certifi bundles in frozen applications), but
when a ZIP entry is corrupted or truncated, the function silently
returns the original path instead of raising a meaningful error. Add
explicit error handling that raises `IOError` with a descriptive message
when ZIP extraction fails.

### N10: Add __reduce__ support for PreparedRequest pickling

The `PreparedRequest` class in `models.py` does not implement
`__reduce__` or `__getstate__`/`__setstate__`, which means pickling
fails when the body is an open file object or a generator. Add
`__getstate__()` and `__setstate__()` methods that handle non-picklable
body types by reading file-like bodies into bytes and raising
`TypeError` for generator bodies, matching the approach used by
`Response.__getstate__()`.

## Medium

### M1: Implement request event hooks for lifecycle monitoring

The `hooks.py` module defines a minimal `dispatch_hook()` system that
only supports a `response` event. Extend the hook system to support
`pre_request`, `pre_send`, `post_send`, and `error` events. Fire
`pre_request` in `Session.request()` before preparation, `pre_send` in
`Session.send()` before adapter dispatch, `post_send` after receiving
the response, and `error` when an exception occurs. Each hook receives
a dictionary with the request, response (if available), and exception
(if any). Update `hooks.py`, `sessions.py`, and the `default_hooks()`
function.

### M2: Add HTTP/2 transport adapter

The `adapters.py` module only provides `HTTPAdapter` backed by urllib3
for HTTP/1.1. Implement an `HTTP2Adapter(BaseAdapter)` that uses
`httpcore` or `h2` to handle HTTP/2 connections. The adapter should
implement `send()` and `close()`, support connection pooling
configuration (`pool_connections`, `pool_maxsize`), integrate with
`Session.mount()` for URL prefix matching, and build `Response` objects
with the same interface as `HTTPAdapter.build_response()`.

### M3: Implement response caching with ETag/Last-Modified support

Add a `CachingAdapter` that wraps `HTTPAdapter` and implements HTTP
caching semantics. On responses with `ETag` or `Last-Modified` headers,
store the response in a configurable cache. On subsequent requests to
the same URL, send conditional requests with `If-None-Match` or
`If-Modified-Since` and return the cached response on `304 Not Modified`.
Requires changes to `adapters.py` for the new adapter class, integration
with `sessions.py` for cache configuration, and a cache storage
abstraction (in-memory dict and file-based).

### M4: Add structured logging throughout the request lifecycle

The requests library uses no logging by default, making debugging
difficult. Add structured logging to `sessions.py` (request preparation,
redirect following), `adapters.py` (connection pooling, SSL
verification), and `auth.py` (authentication challenges). Use Python's
`logging` module with a `requests` logger namespace. Log at DEBUG for
normal operations and WARNING for retry/redirect events. Include
request method, URL, status code, and timing in log records.

### M5: Implement asynchronous session using asyncio

Add an `AsyncSession` class to a new `async_sessions.py` module that
provides the same API as `Session` but with `async`/`await` support.
`AsyncSession.request()`, `send()`, and `resolve_redirects()` should be
coroutines. Requires an async transport adapter interface, integration
with `asyncio.get_event_loop()`, async cookie jar operations, and
async hook dispatch. The existing `Session` API must remain unchanged.

### M6: Add multipart upload progress callbacks

The `PreparedRequest.prepare_body()` method in `models.py` encodes
multipart file uploads via `encode_multipart_formdata()` from urllib3,
but provides no way to monitor upload progress. Add a `progress_callback`
parameter to `Session.send()` and `HTTPAdapter.send()` that receives
`(bytes_sent, total_bytes)` on each chunk write. Implement a wrapper
around the request body stream in `adapters.py` that calls the callback,
and expose `total_bytes` by computing the content length in
`prepare_content_length()` in `models.py`.

### M7: Implement connection pool health monitoring

The `HTTPAdapter` in `adapters.py` manages urllib3 `PoolManager`
instances but provides no visibility into pool state (active
connections, idle connections, pool exhaustion). Add a `pool_stats()`
method to `HTTPAdapter` that returns a dictionary of per-host connection
statistics, and a `pool_health_callback` parameter that fires when
pools approach capacity. Requires reading internal urllib3 pool state
and surfacing it through the adapter interface.

### M8: Add request/response interceptor chain

Implement an interceptor pattern for `Session` that allows registering
ordered interceptors that can modify requests before sending and
responses before returning. Each interceptor is a callable receiving
`(request, session)` or `(response, request, session)`. Add
`Session.add_request_interceptor()` and
`Session.add_response_interceptor()` methods. Run request interceptors
in `prepare_request()` and response interceptors in `send()` after
`build_response()`. Changes span `sessions.py` and optionally a new
`interceptors.py` module.

### M9: Implement per-host TLS certificate configuration

The `HTTPAdapter.cert_verify()` method in `adapters.py` applies a
single `verify` and `cert` configuration to all connections. Add support
for per-host TLS settings via a `tls_config` dictionary mapping hostnames
to `(verify, cert)` tuples. Override `cert_verify()` to look up
host-specific settings before falling back to defaults. Add
`Session.tls_config` for convenient configuration and update
`build_connection_pool_key_attributes()` to include TLS config in pool
keys so different TLS configs use different pools.

### M10: Add DNS-over-HTTPS resolution support

The `HTTPAdapter` relies on system DNS via urllib3. Implement a custom
DNS resolver that queries a configurable DoH endpoint (e.g.,
`https://dns.google/dns-query`) before connecting. Add a
`doh_resolver` parameter to `HTTPAdapter.__init__()` and override
`send()` to resolve hostnames via DoH, cache results with TTL, and
modify the connection to use the resolved IP while preserving the
original `Host` header. Requires changes to `adapters.py` and a new
`resolvers.py` module.

## Wide

### W1: Implement a full retry framework with pluggable strategies

Add a retry system that goes beyond urllib3's built-in `Retry`. Support
configurable retry strategies (exponential backoff, jitter, circuit
breaker), per-status-code retry policies, retry budgets, and retry
event hooks. Integrate retry logic into `Session.send()` so it works
across adapter types. Include retry state tracking (attempt count,
last error, backoff delay) accessible from hooks. Changes span
`sessions.py`, `adapters.py`, a new `retry.py` module, the
exception hierarchy in `exceptions.py` (for `RetryError` enhancement),
and the hook system in `hooks.py`.

### W2: Add comprehensive request/response serialization and replay

Implement a record/replay system for testing. Add a `RecordingAdapter`
that wraps any `BaseAdapter` and serializes request/response pairs to
a YAML/JSON cassette file. Add a `ReplayAdapter` that reads cassettes
and returns recorded responses for matching requests, with configurable
matching on method, URL, headers, and body. Support cassette
composition and request-not-found error modes. Changes span
`adapters.py`, a new `recording.py` module, `sessions.py` for
convenient `Session.record()`/`Session.replay()` methods, and the
model serialization in `models.py`.

### W3: Implement HTTP Strict Transport Security (HSTS) enforcement

Add HSTS support that automatically upgrades HTTP URLs to HTTPS when
the target host has previously sent an HSTS header. Implement an HSTS
policy store (in-memory and persistent file-based), header parsing with
`max-age`, `includeSubDomains`, and `preload` support, integration into
`Session.prepare_request()` for URL rewriting, and HSTS preload list
loading. Changes span `sessions.py`, `models.py` (URL preparation),
`utils.py` (domain matching), a new `hsts.py` module, and
`adapters.py` (header extraction from responses).

### W4: Add OAuth 2.0 authentication flow support

Implement OAuth 2.0 authentication as an extension of the `auth.py`
module. Support Authorization Code, Client Credentials, and Resource
Owner Password flows. Include automatic token refresh when access
tokens expire, PKCE support for Authorization Code flow, token storage
abstraction (in-memory, keyring, file), and integration with
`Session.auth` for transparent token management. Changes span `auth.py`
for new `OAuth2Auth` and `OAuth2Session` classes, `sessions.py` for
token lifecycle integration, `exceptions.py` for OAuth-specific errors,
and a new `oauth2.py` module for token endpoints and PKCE utilities.

### W5: Implement request signing and verification

Add support for HTTP Message Signatures (RFC 9421). Implement request
signing in a new `signing.py` module with configurable algorithms
(HMAC-SHA256, RSA-PSS, Ed25519), component selection (method, URL,
headers), and key management. Add an `HTTPSignatureAuth(AuthBase)` that
signs outgoing requests and a `verify_signature()` utility for incoming
responses. Integrate with the hook system for automatic verification.
Changes span `auth.py`, a new `signing.py` module, `models.py` for
signature header access, `utils.py` for canonicalization, and
`sessions.py` for verification hook integration.

### W6: Add connection lifecycle management with health checks

Implement proactive connection health monitoring. Add a background
connection validator that periodically checks idle connections in the
urllib3 pool via HEAD or OPTIONS requests, evicts unhealthy connections,
and pre-warms pools for configured hosts. Include circuit-breaker logic
that temporarily marks hosts as unavailable after repeated failures.
Changes span `adapters.py` (pool lifecycle hooks), a new
`connection_health.py` module (validator thread, circuit breaker),
`sessions.py` (configuration and lifecycle management),
`exceptions.py` (new `CircuitBreakerError`), and `hooks.py` (health
event hooks).

### W7: Implement content negotiation and automatic format handling

Add intelligent content negotiation that automatically sets `Accept`
headers based on registered response parsers and selects the
appropriate parser based on `Content-Type`. Support JSON, XML, form
data, msgpack, and custom formats via a pluggable parser registry.
Integrate with `Response` to add typed accessors (`response.data` that
returns parsed content). Changes span `models.py` (Response parser
dispatch), `sessions.py` (Accept header generation), a new
`content.py` module (parser registry and format handlers), `utils.py`
(content-type parsing enhancement), and `structures.py` (quality-value
header construction).

### W8: Add comprehensive proxy chain support

Implement multi-hop proxy chaining where requests traverse a sequence
of proxies. Support mixed proxy types (HTTP CONNECT, SOCKS5, SOCKS4)
in a single chain, per-hop authentication, and proxy-chain-aware
redirect handling. Add proxy health checking and automatic failover
to backup proxy chains. Changes span `adapters.py` (chained proxy
manager), `sessions.py` (proxy chain configuration), `utils.py`
(proxy URL parsing for chains), `auth.py` (per-hop proxy auth),
`exceptions.py` (proxy chain errors), and `models.py` (proxy chain
metadata on Response).

### W9: Implement streaming multipart response parser

Add support for parsing multipart responses (e.g.,
`multipart/mixed`, `multipart/related`) that are common in batch APIs
and email protocols. Implement a streaming parser that yields individual
parts with their headers and body as they arrive, supports nested
multipart boundaries, and handles both string and binary parts.
Integrate with `Response.iter_content()` and add a `Response.parts()`
iterator. Changes span `models.py` (response part iteration), a new
`multipart.py` module (boundary parser, part class), `utils.py`
(content-type parameter extraction), `structures.py` (part header
container), and `adapters.py` (streaming configuration).

### W10: Add request tracing with OpenTelemetry integration

Implement distributed tracing throughout the request lifecycle. Add
span creation for DNS resolution, connection establishment, TLS
handshake, request send, and response receive. Support context
propagation via W3C Trace Context headers. Include a built-in console
exporter for debugging and integration with OpenTelemetry SDK for
production use. Changes span `sessions.py` (trace context propagation),
`adapters.py` (connection-level spans), a new `tracing.py` module
(span management, exporters), `models.py` (trace metadata on
Request/Response), `hooks.py` (trace event hooks), and `utils.py`
(trace header parsing).
