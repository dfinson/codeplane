# Tasks — guzzle/guzzle

8 tasks (3 narrow, 3 medium, 2 wide) for the PHP HTTP client.

## Narrow

### N1: Fix `RedirectMiddleware` not preserving fragment in redirect URL

When following a redirect, the fragment (`#section`) from the original
request URL is dropped. Per RFC 7231, if the redirect target does not
include a fragment, the fragment from the original request should be
preserved. Fix the redirect middleware to carry forward the fragment.

### N2: Add `connect_timeout` separate from `timeout`

Currently `timeout` applies to the entire request lifecycle. Add a
separate `connect_timeout` option that limits only the TCP connection
phase. This allows setting a short connection timeout (2s) with a
longer transfer timeout (60s) for large downloads. Pass the value
through to cURL's `CURLOPT_CONNECTTIMEOUT`.

### N3: Fix `MockHandler` not resetting request history on reuse

When a `MockHandler` is reused across multiple test methods, the
request history from previous tests leaks into subsequent tests. The
history array is never cleared. Add a `reset()` method to
`MockHandler` and clear history between test invocations.

## Medium

### M1: Implement retry middleware with circuit breaker

Add a `CircuitBreakerMiddleware` that stops sending requests to a
failing upstream after a configurable failure threshold. Track failures
per host. Transition between closed (normal), open (failing — fast
fail), and half-open (test one request) states. Combine with the
existing retry middleware so retries respect the circuit state. Add
configurable failure detection (status codes, timeouts, exceptions).

### M2: Add request/response body streaming with progress callbacks

Implement streaming body support with progress reporting. For uploads,
add a `progress` callback option that receives bytes sent / total bytes.
For downloads, add a streaming response body that yields chunks with
progress tracking. Support pause/resume for large transfers. Add a
`StreamHandler` that wraps the existing cURL handler with stream support.

### M3: Implement connection pooling with keep-alive management

Add explicit connection pool management to Guzzle. Track persistent
connections per host, enforce maximum connections per host, and close
idle connections after a configurable timeout. Surface pool statistics
(active, idle, total connections per host) through a `PoolStats`
interface. Add `warm()` to pre-establish connections to known hosts.

## Wide

### W1: Add PSR-18 async client support

Implement `Psr\Http\Client\ClientInterface` with async support using
PHP 8.1 Fibers. Add `sendAsync()` that returns a `Promise` object
compatible with the existing Guzzle promise interface. Support
concurrent request execution with configurable concurrency limits.
Add async middleware support where middlewares can yield control.
Update all existing middleware to work in both sync and async modes.

### W2: Implement comprehensive HTTP debugging toolkit

Add a debugging toolkit for API development: request/response
recording (`RecorderMiddleware` that saves to HAR format),
request playback (replay recorded requests for regression testing),
request diffing (compare two requests/responses side by side),
traffic inspection UI (browser-based), and mock server generation
(auto-generate `MockHandler` stacks from recorded traffic). Each
tool should be usable independently or together.
