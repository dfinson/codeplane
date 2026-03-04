# caddyserver/caddy

| Field | Value |
|-------|-------|
| **URL** | https://github.com/caddyserver/caddy |
| **License** | Apache-2.0 |
| **Language** | Go |
| **Scale** | Medium (multi-module project) |
| **Category** | Web server with plugin architecture |

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

8 tasks (3 narrow, 3 medium, 2 wide) for the Go web server.

## Narrow

### N1: Fix Caddyfile `handle_path` not stripping prefix on error routes

When using `handle_path /api/*` with an error handler inside, the error
handler receives the original un-stripped path instead of the stripped
path. The path prefix is restored before the error handler runs. Fix
the error handling to preserve the stripped path context within
`handle_path` blocks.

### N2: Add `max_header_size` directive to HTTP server

The HTTP server does not expose a way to configure the maximum HTTP
header size. Large headers from corporate proxies or OAuth tokens can
exceed Go's default 1MB limit. Add a `max_header_size` Caddyfile
directive and JSON config option that sets `http.Server.MaxHeaderBytes`.

### N3: Fix reverse proxy health check not respecting `tls_server_name`

When a reverse proxy upstream uses TLS with a custom `tls_server_name`,
the active health check sends requests without the configured SNI value.
The health check TLS handshake uses the upstream's IP address as the
server name, causing certificate verification failures. Fix the health
checker to use the configured `tls_server_name`.

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

### M3: Implement on-demand TLS certificate rotation

Add support for on-demand TLS certificate rotation that doesn't require
a server restart. When a certificate is about to expire, the ACME client
should obtain a new certificate and hot-swap it into the TLS config
without dropping any active connections. Add logging and metrics for
certificate rotation events.

## Wide

### W1: Add a web-based admin dashboard

Implement a web UI for Caddy's admin API. The dashboard should display
the current configuration, active sites, TLS certificate status,
upstream health for reverse proxies, request metrics, and access logs.
Support live configuration editing with validation and rollback.
The UI should be a single embedded module served from the admin
endpoint.

### W2: Implement request tracing with OpenTelemetry

Add native OpenTelemetry support across the request lifecycle. Create
spans for the listener accept, TLS handshake, route matching, each
handler in the chain, reverse proxy upstream selection, upstream request,
and response writing. Propagate trace context across reverse proxy
hops. Add a `tracing` Caddyfile directive and JSON config. Support
OTLP export (gRPC and HTTP).
