# caddyserver/caddy

| Field | Value |
|-------|-------|
| **URL** | https://github.com/caddyserver/caddy |
| **License** | Apache-2.0 |
| **Language** | Go |
| **Scale** | Medium (multi-module project) |
| **Category** | Web server with plugin architecture |
| **Set** | ranker-gate |
| **Commit** | `ffb6ab0644f24c5ee6542aca6bd59b7a1b0a8f91` |

## Why this repo

- **Multi-module with clear boundaries**: HTTP server, TLS automation (ACME),
  reverse proxy, file server, Caddyfile config parser, JSON config API,
  logging, metrics — each a distinct subsystem requiring navigation between
  them for cross-cutting changes.
- **Well-structured**: Clean Go package layout. `modules/` contains pluggable
  components (caddyhttp, caddytls, caddylog). Core admin API and config
  loading are separate from module implementations.
- **Rich history**: 5K+ commits, regular major feature PRs (HTTP/3, ACME
  improvements, new modules). Strong code review culture.
- **Permissive**: Apache-2.0.

## Structure overview

```
.
├── caddy.go             # Core Caddy type, module registry
├── admin.go             # Admin API server
├── config.go            # Configuration loading
├── modules/
│   ├── caddyhttp/       # HTTP server, handlers, middleware
│   │   ├── server.go    # HTTP server implementation
│   │   ├── routes.go    # Route matching
│   │   ├── reverseproxy/ # Reverse proxy module
│   │   ├── fileserver/  # Static file serving
│   │   └── headers/     # Header manipulation
│   ├── caddytls/        # TLS automation, ACME client
│   ├── caddyevents/     # Event system
│   ├── caddypki/        # PKI management
│   └── logging/         # Structured logging
├── caddyconfig/         # Caddyfile parser and adapters
│   ├── caddyfile/       # Caddyfile syntax parser
│   └── httpcaddyfile/   # HTTP-specific Caddyfile directives
└── cmd/                 # CLI entry point
```

## Scale indicators

- ~200 Go source files
- ~60K lines of code
- Clear plugin/module boundaries
- 2-3 levels of package nesting

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix Caddyfile `handle_path` not stripping prefix on error routes

When using `handle_path /api/*` with an error handler inside, the error
handler receives the original un-stripped path instead of the stripped
path. The path prefix is restored before the error handler runs. Fix
the error handling to preserve the stripped path context within
`handle_path` blocks.

### N2: Add `retry_delay` option to `reverse_proxy` for backoff between retries

The reverse proxy in `modules/caddyhttp/reverseproxy/reverseproxy.go`
retries failed requests on other upstreams but retries immediately
without any delay. When an upstream fails, instant retries to the
remaining upstreams create burst load that can cascade failures.
Add a `retry_delay` field to the `Handler` struct and a corresponding
Caddyfile option that introduces a configurable pause between retry
attempts, with optional exponential backoff. Update the `README.md`
to document the new `retry_delay` option in the reverse proxy
configuration examples.

### N3: Fix reverse proxy health check not respecting `tls_server_name`

When a reverse proxy upstream uses TLS with a custom `tls_server_name`,
the active health check sends requests without the configured SNI value.
The health check TLS handshake uses the upstream's IP address as the
server name, causing certificate verification failures. Fix the health
checker to use the configured `tls_server_name`.

### N4: Add `max_concurrent_requests` option to the HTTP server

The `Server` struct in `modules/caddyhttp/server.go` configures
timeouts (`ReadTimeout`, `WriteTimeout`, `IdleTimeout`) but has no
way to limit the number of concurrently-processed requests. Under
high load, unbounded concurrency can exhaust memory and goroutines.
Add a `max_concurrent_requests` field that, when set, uses a
semaphore to limit active handler invocations and returns
`503 Service Unavailable` when the limit is reached.

### N5: Fix `file_server` browse template unescaped canonical link path

The browse template in `modules/caddyhttp/fileserver/browse.html` is
rendered with Go's `text/template` (imported in `browse.go`) rather
than `html/template`, which relies on explicit escaping throughout.
While filename display uses `{{html .Name}}` consistently, the
canonical link at line 309 of `browse.html` outputs the directory path
directly: `<link rel="canonical" href="{{.Path}}/" />`. A directory
whose path contains `"` or `<` would produce malformed HTML. Fix
`browse.go`'s `makeBrowseTemplate` to switch from `text/template` to
`html/template`, which provides automatic context-aware escaping and
makes the explicit `{{html ...}}` calls throughout `browse.html`
redundant (they become no-ops since `html/template` escapes
automatically). Remove the now-redundant `html` function calls in
`browse.html` after the switch.

### N6: Add built-in `threshold` circuit breaker for `reverse_proxy`

The `CircuitBreaker` interface is defined in
`modules/caddyhttp/reverseproxy/healthchecks.go` and the `Handler`
struct in `reverseproxy.go` accepts a circuit breaker via `CBRaw`,
but Caddy ships no built-in implementation. Add a threshold-based
circuit breaker module registered as
`http.reverse_proxy.circuit_breakers.threshold` that trips after a
configurable number of consecutive failures and resets after a
cooldown period. Add Caddyfile `circuit_breaker` sub-directive.

### N7: Add `Proxy-Status` response header (RFC 9209) to `reverse_proxy`

The reverse proxy in `modules/caddyhttp/reverseproxy/reverseproxy.go`
does not generate the `Proxy-Status` HTTP response header defined by
RFC 9209. When an upstream returns an error, times out, or the
connection fails, the client has no structured way to understand the
proxy's role in the failure. Add `Proxy-Status` header generation for
upstream errors, timeouts, and connection failures, with an option to
enable/disable it via `proxy_status` in the Caddyfile.

### N8: Fix `log` directive custom format not applying to error responses

Custom access log formats configured via `log { format ... }` are not
applied to responses generated by Caddy's error handler (404 pages,
redirect responses). The error handler writes its own log entry bypassing
the configured format. Fix error logging to use the configured format.

### N9: Add `request_id` directive for correlation

Add a `request_id` directive that generates a unique ID per request,
adds it to the response headers (`X-Request-ID`), injects it into the
request context for use by other handlers, and includes it in access
log entries. Support accepting an existing request ID from an incoming
header.

### N10: Add JWT bearer token authentication provider to `caddyauth`

The authentication module in `modules/caddyhttp/caddyauth/` supports
only HTTP Basic Auth via `HTTPBasicAuth` in `basicauth.go`. Add a
JWT bearer token authentication provider that validates `Authorization:
Bearer <token>` headers, supports configurable JWKS endpoints for key
resolution, RS256/ES256 algorithm validation, claims-based user
identity extraction, and token expiry checks. Register it under
`http.authentication.providers.jwt_bearer`.

## Medium

### M1: Implement request rate limiting module

Add a `rate_limit` HTTP handler module that supports per-client (by IP)
and per-route token bucket rate limiting. Support configurable burst
size, refill rate, and distributed rate limiting via a shared storage
interface (with an embedded implementation for single-instance). Return
proper `429` responses with `Retry-After` headers. Add Caddyfile
shortcut syntax.

### M2: Add WebSocket reverse proxy improvements

Implement WebSocket-specific features in the reverse proxy module:
connection timeout (separate from HTTP timeout), ping/pong health
monitoring for idle connections, automatic reconnection on upstream
failure, and WebSocket compression (permessage-deflate) negotiation
passthrough. Add per-connection metrics to the Prometheus endpoint.

### M3: Implement HTTP response caching handler module

Add a `cache` HTTP handler module that caches upstream responses for
subsequent matching requests. Support RFC 7234 cache-control directives,
conditional requests (`If-None-Match`, `If-Modified-Since`) with ETag
and Last-Modified validation, configurable memory storage with LRU
eviction, per-route TTL overrides, and cache key computation from
method, path, query, and selected headers. Add Vary header handling
for content negotiation. Add `cache` Caddyfile directive and JSON
config. This involves a new handler module, cache storage layer,
and Caddyfile adapter integration in `caddyconfig/httpcaddyfile/`.
Update `.github/CONTRIBUTING.md` to document the caching module's
test patterns and add a caching-specific Caddyfile example in the
`README.md`.

### M4: Add CORS handler module with preflight handling

Add a `cors` HTTP handler module that manages Cross-Origin Resource
Sharing headers. Support origin allowlists with wildcard patterns
(e.g. `*.example.com`), automatic preflight (`OPTIONS`) response
handling with `Access-Control-Allow-Methods` and
`Access-Control-Allow-Headers`, configurable `Access-Control-Max-Age`
for preflight caching, credential support
(`Access-Control-Allow-Credentials`), exposed headers configuration,
and per-route CORS policies. Add `cors` Caddyfile directive. This
spans a new handler module under `modules/caddyhttp/`, Caddyfile
parsing in `caddyconfig/httpcaddyfile/`, and directive ordering.

### M5: Add gRPC reverse proxy support

Implement gRPC-aware reverse proxy handling. Support gRPC metadata
(headers/trailers) forwarding, gRPC status code mapping, bidirectional
streaming, gRPC-Web protocol translation, and gRPC health checking
for load balancing. Add Caddyfile syntax `reverse_proxy grpc://backend`.

### M6: Implement IP geolocation handler

Add a `geoip` handler module that resolves client IP addresses to
geographic data (country, city, ASN) using a MaxMind GeoIP2 database.
Make geolocation data available as placeholders (`{geo.country_code}`)
for use in matchers, headers, and log entries. Support auto-updating
the GeoIP database.

### M7: Add canary deployment support in reverse proxy

Implement traffic splitting in the reverse proxy for canary deployments.
Support weight-based routing (`canary weight 10%` sends 10% of traffic
to the canary upstream), cookie-based stickiness (once a user hits the
canary, they stay), and header-based routing for internal testing.

### M8: Implement structured access log with rich fields

Extend the access log with per-request structured fields: request body
size, response body size, TLS version and cipher, upstream response time
(for proxy), cache hit/miss status, compression ratio, and client
certificate subject (for mTLS). Support JSON and Common Log Format output.

### M9: Add connection limiting per client

Implement `connection_limit` handler that limits concurrent connections
per client IP. Support configurable limit, custom response on limit
exceeded, whitelisting for trusted IPs, and metrics for rejected
connections. Add Caddyfile syntax and JSON config.

### M10: Add Consul service discovery for reverse proxy dynamic upstreams

The reverse proxy supports `SRVUpstreams`, `AUpstreams`, and
`MultiUpstreams` in `modules/caddyhttp/reverseproxy/upstreams.go` for
dynamic upstream discovery. Add a Consul-based upstream source that
queries the Consul service catalog for healthy instances. Support
configurable Consul address, datacenter, service name, tag filtering,
refresh interval, and health-aware discovery that excludes failing
nodes. Register it as `http.reverse_proxy.upstreams.consul`. Add
Caddyfile syntax within `dynamic` upstream blocks.

## Wide

### W1: Add a web-based admin dashboard

Implement a web UI for Caddy's admin API. The dashboard should display
the current configuration, active sites, TLS certificate status,
upstream health for reverse proxies, request metrics, and access logs.
Support live configuration editing with validation and rollback.
The UI should be a single embedded module served from the admin
endpoint.

### W2: Implement request audit trail with persistence and admin API

Add a comprehensive request auditing system that captures and persists
request/response metadata across the request lifecycle. For each
request, record: client IP, TLS details (cipher, protocol, client
certificate from `modules/caddytls/connpolicy.go`), matched route
(from `modules/caddyhttp/routes.go`), handler chain execution times,
upstream selection and response (from `modules/caddyhttp/reverseproxy/`),
response status and body size, and errors (from
`modules/caddyhttp/errors.go`). Store audit entries to configurable
backends (file, webhook). Add admin API endpoints at `/audit/` to
query and export records. Register event handlers via
`modules/caddyevents/` for audit-relevant events. This spans the
HTTP server, route matching, reverse proxy, TLS module, admin API,
event system, and file I/O.

### W3: Implement request rate limiting with distributed state

Add a comprehensive rate limiting system spanning the HTTP handler
chain, reverse proxy, and admin API. Support per-client (by IP via
`modules/caddyhttp/ip_matchers.go`), per-route, and global rate limits
using a token bucket algorithm. Return proper `429` responses with
`Retry-After` headers (via `modules/caddyhttp/staticresp.go` patterns).
Include distributed rate limiting via a shared storage backend (using
Caddy's `storage.go` interface, with an embedded single-instance
implementation). Add adaptive throttling that adjusts limits based on
upstream health from `modules/caddyhttp/reverseproxy/healthchecks.go`.
Expose rate limit status via the admin API (`admin.go`). Add a
`rate_limit` Caddyfile directive and Prometheus metrics for rate limit
hits. This spans handler chain, storage, admin API, reverse proxy
health integration, metrics, and Caddyfile parsing.

### W4: Implement edge-side includes (ESI) handler

Add an ESI handler that processes `<esi:include src="..."/>` tags in
HTML responses. The handler fetches included fragments from upstream
services, caches them independently (per-fragment TTL), handles errors
per fragment (fallback content), and supports parallel fragment
fetching. Changes span the response pipeline (body interception),
caching module, upstream fetching, and error handling.

### W5: Add WAF (Web Application Firewall) module

Implement a WAF handler module with: OWASP Core Rule Set support, SQL
injection detection, XSS detection, path traversal detection, rate-based
rules (too many 404s from one IP), and custom rule definitions in
Caddyfile syntax. Support allow/deny/log actions per rule. Include a
learning mode that logs violations without blocking. This spans request
parsing, response inspection, rule evaluation, and adds a WAF module.

### W6: Implement configuration version control and rollback

Add configuration versioning to the admin API. Every config change is
persisted with a version number and timestamp. Support `GET /config/versions`
(list versions), `GET /config/versions/:id` (get specific version),
`POST /config/rollback/:id` (roll back to a version), and automatic
rollback on startup failure. Add a config diff endpoint. This spans
the admin API, config management, storage, and lifecycle management.

### W7: Implement request deduplication and coalescing for reverse proxy

Add request deduplication (thundering herd protection) to the reverse
proxy handler in `modules/caddyhttp/reverseproxy/reverseproxy.go`.
When multiple clients request the same cacheable resource simultaneously,
only one request is forwarded to the upstream; other identical requests
wait for the first response and share it. Support configurable cache
keys (path, query, selected headers), per-route dedup policies via
Caddyfile, timeout for coalesced requests, and dedup bypass for
non-idempotent methods. Add metrics for dedup hits integrated with
the existing Prometheus metrics in `modules/caddyhttp/metrics.go`.
Expose coalescing status in the admin API. This spans the reverse
proxy handler, request matching, response buffering
(`modules/caddyhttp/responsewriter.go`), connection tracking, metrics,
admin API, and Caddyfile parsing.

### W8: Implement zero-downtime config reload with connection draining

Add graceful configuration reloading that: loads and validates the new
config, starts new listeners, drains existing connections with a
configurable timeout, migrates persistent connections (WebSocket, SSE)
to the new config, and rolls back if the new config fails health checks.
Currently config reload is instantaneous but drops in-flight requests.
This spans the config loader, listener management, connection tracking,
and admin API. Update `.goreleaser.yml` to document graceful reload
behavior in the generated man pages, and update
`.github/workflows/ci.yml` to add a reload integration test that
validates zero dropped requests during config transitions.

### W9: Add multi-site deployment templating

Implement a Caddyfile templating system for managing many similar sites.
Support `site_template` definitions with parameterized blocks, `import`
with variable substitution, conditional blocks based on environment
variables, and template inheritance (base template + per-site overrides).
Add validation that template expansion produces valid Caddyfile syntax.
This spans the Caddyfile parser, adapter, and adds a template module.

### W10: Implement request recording and replay for testing

Add a record/replay system. A `record` handler captures full request/
response pairs to disk in HAR format. A `replay` handler serves
previously recorded responses without contacting upstreams, matching
by URL, method, and headers. Support fuzzy matching (ignore timestamps
in requests), response modification (update dates), and a management
API for listing and managing recordings. This spans the handler chain,
file I/O, request matching, and adds record/replay modules.

## Non-code focused

### N11: Fix outdated `.pre-commit-config.yaml` hook versions

The `.pre-commit-config.yaml` pins `golangci/golangci-lint` at
`v1.52.2` but the `.golangci.yml` uses the v2 config format
(`version: "2"`), making the `golangci-lint-config-verify`
pre-commit hook fail on validation. The `gitleaks` hook is pinned
at `v8.16.3` and `jumanjihouse/pre-commit-hooks` (for `shellcheck`)
at `3.0.0` — the `jumanjihouse` organization is deprecated and no
longer maintained. Update all hook versions to current releases,
replace the `jumanjihouse` shellcheck hook with the official
`shellcheck-py/shellcheck-py` repository, and verify the
`golangci-lint-config-verify` hook passes with the v2
`.golangci.yml` format.

### M11: Improve GoReleaser release pipeline and add download verification docs

The `.goreleaser.yml` generates cosign signatures (`.sig`) and SBOMs
for binaries, and the release `before.hooks` generate man pages
(`go run cmd/caddy/main.go manpage`) and bash completions but omit
zsh and fish completions. Add zsh and fish completion generation to
the GoReleaser `before.hooks` section. The `README.md` does not
document how users should verify downloaded binaries using the
cosign signatures and SBOM artifacts attached to releases — add a
"Verifying Downloads" section with cosign verification commands.
Update `.github/CONTRIBUTING.md` to reference the tag-based release
pipeline described in `.github/workflows/release.yml` (which uses
a verify-tag job with GPG signature and approval checks). Update
`.github/workflows/release.yml` to add a post-release step that
comments on the associated release proposal issue with published
artifact checksums.

### W11: Comprehensive CI hardening and developer documentation overhaul

The `.github/workflows/ci.yml` runs tests on linux/mac/windows with
Go 1.26 and build tags `nobadger,nomysql,nopgx` but has no
integration test job that starts Caddy with a Caddyfile and verifies
actual HTTP serving. The `.github/CONTRIBUTING.md` covers PR
etiquette thoroughly but does not document local development setup:
required build tags, the vendor workflow described in
`.goreleaser.yml`, or the `.pre-commit-config.yaml` hooks. Add a
`.github/workflows/integration.yml` that starts Caddy with sample
Caddyfiles and runs curl-based smoke tests for reverse proxy, file
server, and TLS termination. Restructure `.github/CONTRIBUTING.md`
to add a "Development Environment" section covering build tags,
vendor workflow, pre-commit setup, and the `.editorconfig` settings.
Update `.golangci.yml` to add `gocritic`, `revive`, and `nakedret`
linters (currently 20+ linters are enabled but these three are
missing). Add a `SECURITY.md` at the repository root for GitHub's
security advisory feature (currently only `.github/SECURITY.md`
exists). Update `README.md` with a "Building from Source" section.
