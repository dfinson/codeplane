# puma/puma

| Field | Value |
|-------|-------|
| **URL** | https://github.com/puma/puma |
| **License** | BSD-3-Clause |
| **Language** | Ruby |
| **Scale** | Medium |
| **Category** | Web server |
| **Set** | Cutoff |
| **Commit** | `138ba14bedd1bce0307804d3b3f2c3f3db73012c` |

## Why this repo

- **Well-structured**: Clear separation of concerns — HTTP parsing in C
  extension (`ext/puma_http11/`), connection handling (`client.rb`),
  request/response processing (`server.rb`, `response.rb`), IO
  multiplexing (`reactor.rb`), thread pool management (`thread_pool.rb`),
  multi-process clustering (`cluster.rb`), configuration DSL (`dsl.rb`),
  and TLS (`minissl.rb`).
- **Rich history**: 4K+ commits, 7K+ stars. The default Rack server
  for Rails. Issues span concurrency edge cases, SSL handshake handling,
  chunked transfer encoding, and multi-worker lifecycle management.
- **Permissive**: BSD-3-Clause license.

## Structure overview

```
lib/puma/
├── server.rb                  # Core HTTP server — accept loop, request dispatch
├── client.rb                  # Connection wrapper — HTTP parsing, buffering, timeouts
├── client_env.rb              # Rack env hash construction from parsed headers
├── response.rb                # HTTP response writing — status line, headers, body
├── thread_pool.rb             # Worker thread pool — spawn, reap, shutdown
├── reactor.rb                 # IO multiplexer via nio4r — timeout tracking, wakeup
├── binder.rb                  # Socket binding — TCP, Unix, SSL listener setup
├── cluster.rb                 # Multi-process mode — fork, phase restart, worker lifecycle
├── cluster/worker.rb          # Individual worker process — boot, signal handling
├── cluster/worker_handle.rb   # Parent-side worker tracking — pid, phase, status
├── launcher.rb                # Entry point — config resolution, runner selection
├── dsl.rb                     # Configuration DSL — threads, workers, bind, ssl_bind
├── configuration.rb           # Config file loading and option merging
├── minissl.rb                 # Pure-Ruby TLS wrapper — context, socket, cert verification
├── minissl/context_builder.rb # SSL context construction from DSL options
├── plugin.rb                  # Plugin registry and lifecycle
├── plugin/systemd.rb          # systemd notify integration
├── plugin/tmp_restart.rb      # tmp/restart.txt touch-based restart
├── const.rb                   # HTTP constants — status codes, header names
├── log_writer.rb              # Logging abstraction — stdout, custom loggers
├── error_logger.rb            # Error formatting and output
├── events.rb                  # Event hooks — on_booted, on_stopped, on_restart
├── detect.rb                  # Platform detection — JRuby, Windows, MRI
├── util.rb                    # Utility methods — purge_interrupt_queue, nakayoshi_fork
├── state_file.rb              # PID/state file management
├── control_cli.rb             # pumactl command-line control client
├── json_serialization.rb      # Minimal JSON serializer (no gem dependency)
├── commonlogger.rb            # Rack::CommonLogger middleware
├── null_io.rb                 # /dev/null IO stand-in
├── io_buffer.rb               # Write buffer for response assembly
├── rack/builder.rb            # Rack app builder for config.ru
├── rack/urlmap.rb             # URL-based app routing
└── rack_default.rb            # Default Rack handler registration
ext/puma_http11/
├── puma_http11.c              # MRI C extension — Ragel-based HTTP parser
├── http11_parser.c            # Generated Ragel HTTP/1.1 parser
├── http11_parser.h            # Parser state machine header
└── mini_ssl.c                 # OpenSSL binding for MRI
```

## Scale indicators

- ~44 Ruby source files, ~10 C/Java extension files
- ~11K lines of Ruby, ~4K lines of C
- Moderate depth (2 levels under `lib/puma/`)
- Runtime dependency on `nio4r` only

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix Client not enforcing MAX_CHUNK_HEADER_SIZE for chunk extensions

The `Client` class in `client.rb` defines `MAX_CHUNK_HEADER_SIZE = 4096`
and `MAX_CHUNK_EXCESS = 16 * 1024` for chunked transfer encoding
validation, but chunk size lines with very long extensions (between
those two limits) are buffered without bound checking against the
header-size constant. Fix the chunk header parsing loop to reject chunk
headers exceeding `MAX_CHUNK_HEADER_SIZE` immediately, before accounting
for excess data.

### N2: Add request ID generation to Server for tracing

The `Server` class in `server.rb` processes requests but does not
generate or propagate a unique request ID. Add automatic `X-Request-Id`
header injection (UUID v4) when the incoming request does not already
carry one, making the ID available in the Rack env as
`HTTP_X_REQUEST_ID` and in log output via `LogWriter`.

### N3: Fix ThreadPool not logging thread death through LogWriter

The `ThreadPool` in `thread_pool.rb` spawns worker threads that catch
exceptions in the processing block via `rescue Exception => e`, but if a
thread dies from an unexpected error (e.g., `NoMemoryError`), the exception
message is written directly to `STDERR` via `STDERR.puts` rather than
routed through the server's `LogWriter`, and the thread death is not
reflected in the pool's `stats`. Add a thread-death callback that logs the
exception via the server's `LogWriter` and increments a dead-thread counter
accessible through `stats`.

### N4: Add DSL option for configuring TCP keepalive on listener sockets

The `Binder` in `binder.rb` creates TCP listener sockets but does not
set `SO_KEEPALIVE` or the associated parameters (`TCP_KEEPIDLE`,
`TCP_KEEPINTVL`, `TCP_KEEPCNT`). Add `tcp_keepalive(idle:, interval:,
count:)` to the DSL in `dsl.rb`, pass the values through
`Configuration`, and apply them in `Binder#add_tcp_listener` using
`Socket#setsockopt`. Also add a `tcp_keepalive` section to
`docs/deployment.md` documenting the new DSL option with
example values, and update `History.md` with an entry noting the
new feature.

### N5: Fix Reactor timeout list not being sorted after client timeout update

The `Reactor` in `reactor.rb` maintains a `@timeouts` array sorted by
expiration time. When a client's timeout is updated after partial data
arrives, the client is not re-sorted in the timeout list, causing the
reactor to either wake up too early or miss expired clients. Fix the
timeout update path to re-insert the client at the correct sorted
position.

### N6: Add structured JSON access log format option

The `CommonLogger` in `commonlogger.rb` writes access logs in Apache
combined format. Add a `json_access_log` DSL option that outputs
structured JSON with fields for timestamp, method, path, status,
duration, and client IP. Integrate with `LogWriter` in `log_writer.rb`
so the format is configurable per-listener.

### N7: Fix MiniSSL context not validating certificate chain completeness

The `MiniSSL::ContextBuilder` in `minissl/context_builder.rb` loads
the server certificate and key but does not verify that the certificate
chain is complete (i.e., intermediate CAs are present). When an
incomplete chain is configured, clients fail with opaque TLS errors.
Add a chain-completeness check during context creation that warns via
`LogWriter` if intermediates are missing.

### N8: Add worker memory limit to Cluster for automatic restart

The `Cluster` in `cluster.rb` manages worker lifecycles but has no
mechanism to restart workers that exceed a memory threshold. Add a
`worker_memory_limit(bytes)` DSL option in `dsl.rb` that causes the
cluster to send `SIGTERM` to workers whose RSS exceeds the limit,
checked via `/proc/[pid]/status` or `Process.getrusage` at configurable
intervals.

### N9: Fix StateFile not atomically writing the state to disk

The `StateFile` in `state_file.rb` writes the YAML state directly to
the target path. If puma crashes mid-write, the state file is corrupted
and `pumactl` cannot connect. Fix the write to use a temporary file
followed by `File.rename()` for atomic replacement.

### N10: Add connection draining timeout to Server#stop

The `Server#stop` method in `server.rb` signals shutdown but does not
provide a configurable grace period for in-flight requests to complete.
Add a `drain_timeout` parameter (defaulting to 30 seconds) that waits
for active `ThreadPool` tasks to finish before forcibly closing
connections, and expose it via the DSL.

### N11: Fix docs/signals.md not documenting single-mode signal behavior

The `docs/signals.md` file documents signal handling only under a
"Puma cluster responds to these signals" section, with no corresponding
section for single mode (workers = 0). In single mode, `TTIN`, `TTOU`,
`CHLD`, and `URG` are not registered; `USR2` triggers a full server
restart reloading configuration, `USR1` triggers a hot restart without
config reload, `TERM` and `INT` trigger graceful shutdown, and `HUP`
reopens log files defined in `stdout_redirect` or triggers shutdown if
none are configured. This divergence is not documented anywhere.
The `CONTRIBUTING.md` file also does not mention the requirement to
update `docs/signals.md` when adding or modifying signal handlers.
Fix `docs/signals.md` to add a "Single mode signals" section
documenting which signals apply and their behavior, and update
`CONTRIBUTING.md` to include a checklist item for updating the signals
documentation when modifying signal handling code.

## Medium

### M1: Implement HTTP/2 cleartext upgrade support

Add HTTP/2 cleartext (h2c) upgrade handling in `Server` and `Client`.
When a client sends an `Upgrade: h2c` header with `HTTP2-Settings`,
the server should respond with `101 Switching Protocols` and hand off
to an HTTP/2 frame parser. Requires changes to `client.rb` for upgrade
detection, `server.rb` for protocol switching, `response.rb` for HTTP/2
frame writing, and `const.rb` for HTTP/2 constants.

### M2: Implement per-route thread pool isolation

Add support for routing specific URL prefixes to dedicated thread pools,
preventing slow endpoints from starving fast ones. Requires a routing
table in `Server`, multiple `ThreadPool` instances, request classification
in the accept loop, DSL configuration via `thread_pool_for '/api/slow',
min: 1, max: 4`, and stats reporting per pool.

### M3: Add graceful rolling restart for zero-downtime deploys

Implement a rolling restart mode in `Cluster` where workers are
restarted one at a time, waiting for the new worker to signal readiness
before stopping the next old worker. Requires changes to
`Cluster#start_phased_restart`, a readiness-check protocol between
worker and master via `WorkerHandle`, DSL configuration for
`rolling_restart_delay`, and integration with the `Events` hooks.

### M4: Implement request body streaming with back-pressure

Currently `Client` buffers the entire request body before dispatching
to the Rack app. Add a streaming mode where the Rack input is an IO
object that reads from the socket incrementally, with back-pressure
when the app consumes slowly. Requires changes to `Client` body
handling, `Server` dispatch, `Reactor` interest management, and a new
`StreamingBody` class that implements the Rack input interface.

### M5: Add Prometheus metrics endpoint as a built-in plugin

Implement a `prometheus` plugin that exposes metrics at a configurable
endpoint: request count, request duration histogram, thread pool
utilisation, connection queue depth, and worker memory usage. Requires
a new `plugin/prometheus.rb`, metric collection hooks in `Server`,
`ThreadPool`, and `Cluster`, a minimal Prometheus text format emitter,
DSL wiring for `plugin :prometheus, path: '/metrics'`, a new
`docs/stats.md` section documenting the metrics endpoint and
available metric names, and an update to `README.md` adding the
prometheus plugin to the built-in plugins list.

### M6: Implement hot-reload for SSL certificates without restart

Add support for reloading SSL certificates when the cert files change
on disk, without restarting workers or dropping connections. Requires a
file-watcher in `MiniSSL::ContextBuilder`, an `SSLContext` swap
mechanism in `MiniSSL::Server`, signal-based reload triggering in
`Cluster`, and DSL configuration for `ssl_auto_reload`.

### M7: Add circuit breaker for upstream Rack application errors

Implement a circuit breaker in `Server` that tracks consecutive
application errors (5xx responses or exceptions) and temporarily returns
`503 Service Unavailable` when the error rate exceeds a threshold.
Requires error counting in the request dispatch loop, a circuit state
machine (`closed → open → half-open`), configurable thresholds via DSL,
and recovery logic tied to the `Events` system.

### M8: Implement connection coalescing for HTTP/1.1 keep-alive

Improve keep-alive connection handling in `Server` and `Client` by
implementing connection coalescing: track per-client request counts and
bytes, enforce `Max-Requests` and `Max-Idle` limits, and proactively
close connections approaching the limits with a `Connection: close`
header. Requires changes to `Client` state tracking, `Server` dispatch
loop, `Response` header injection, and DSL configuration.

### M9: Add request queuing metrics with wait-time tracking

Instrument the request lifecycle in `ThreadPool` to track the time
each request spends waiting in the queue before a worker picks it up.
Expose percentile statistics (p50, p95, p99) via the `stats` endpoint.
Requires timestamping in `ThreadPool#<<`, duration calculation in the
worker block, a sliding-window statistics collector, and integration
with `ServerPluginControl`.

### M10: Add per-worker log file routing in cluster mode

The `stdout_redirect` DSL option in `runner.rb` routes all cluster
workers to the same log file path. When multiple workers write to a
single file concurrently, log lines interleave without worker
identification. Add a `per_worker_stdout_redirect(pattern)` DSL option
in `dsl.rb` where a `%d` placeholder in the pattern is replaced by the
worker index (e.g., `"log/puma-worker-%d.log"`). When configured, each
worker process after forking opens its own log file, reopens `STDOUT`
and `STDERR` to it, and creates a worker-specific `LogWriter` instance
for the `Server`. Requires changes to `dsl.rb` for the new DSL method,
`configuration.rb` to register the default, `runner.rb` for the
per-worker redirect logic, `cluster/worker.rb` to apply the redirect
and create the worker-specific `LogWriter` after fork, `cluster.rb` to
pass the per-worker log option when spawning workers, and `log_writer.rb`
to support instantiation with a custom output path.

### M11: Overhaul docs/deployment.md and docs/kubernetes.md with modern deployment guidance

The `docs/deployment.md` guide references Capistrano-based deployment
workflows but does not cover container-based deployments with Docker
multi-stage builds. The `docs/kubernetes.md` guide does not include
readiness and liveness probe configuration examples that work with
puma's built-in control server. The `6.0-Upgrade.md` upgrade guide
does not mention the removal of `daemonize` and its impact on systemd
unit files documented in `docs/systemd.md`. The
`.github/pull_request_template.md` has no checklist item for updating
`History.md` with a changelog entry, which is expected for non-trivial
changes per project conventions. Update `docs/deployment.md`
with Docker deployment patterns, update `docs/kubernetes.md` with
probe configuration examples, reconcile `6.0-Upgrade.md` with
`docs/systemd.md` regarding daemonization removal, and add a
`History.md` changelog checklist item to `.github/pull_request_template.md`.

## Wide

### W1: Implement full HTTP/2 support with multiplexed streams

Add a complete HTTP/2 implementation: HPACK header compression, stream
multiplexing, flow control, server push, and GOAWAY. Requires a new
`Http2::Connection` and `Http2::Stream` module, frame parser and
serialiser, integration with `Server` for protocol negotiation (ALPN
via `MiniSSL`), changes to `Client` for multi-stream state, `Response`
for HTTP/2 framing, `ThreadPool` for per-stream dispatch, and `Reactor`
for multiplexed IO.

### W2: Implement a WebSocket upgrade handler with pub/sub support

Add built-in WebSocket support: upgrade handshake in `Server`, frame
parsing/generation in a new `WebSocket` module, per-connection message
dispatch, channel-based pub/sub, ping/pong keepalive, and backpressure.
Requires changes to `Client` for upgrade detection, `Server` for
protocol branching, `Reactor` for WebSocket IO monitoring, `ThreadPool`
for message processing, a new `WebSocket::Connection` class, and DSL
configuration for `websocket_path`.

### W3: Implement distributed cluster management across multiple hosts

Extend `Cluster` to coordinate across multiple machines: a leader-election
protocol, shared configuration via a coordinator (Redis or etcd),
cross-host phased restarts, aggregated health checks, and centralised
logging. Requires a new `Cluster::Distributed` module, a coordinator
client, changes to `Launcher` and `ControlCLI` for remote commands,
`StateFile` for distributed state, and `Events` for cross-host
notifications.

### W4: Add comprehensive request/response middleware pipeline

Implement a middleware pipeline in `Server` with ordered before/after
hooks: rate limiting, request validation, response compression (gzip,
br), ETag generation, CORS, and security headers. Support per-route
middleware stacks. Requires a `Middleware` module with a chainable
interface, integration points in `Server` request dispatch and
`Response` writing, DSL configuration, and `Binder`-level middleware
for connection-level concerns.

### W5: Implement a built-in reverse proxy and load balancer

Add reverse proxy support to `Server`: HTTP forwarding, header rewriting
(`X-Forwarded-For`, `X-Forwarded-Proto`), connection pooling to
upstream servers, health checking, and round-robin/least-connections
load balancing. Requires a new `Proxy` module, upstream connection
management, `Client` modifications for proxied requests, `Response`
modifications for header rewriting, DSL configuration, and integration
with `Reactor` for upstream IO.

### W6: Implement a request replay and debugging tool

Add a `pumactl replay` command and associated `Server` instrumentation
that captures request/response pairs to a binary log and replays them
against a target server. Support filtering by path, status code, and
time range. Requires a new `RequestRecorder` module hooked into
`Server`, a binary log format with efficient serialisation, a replay
client in `ControlCLI`, `DSL` options for recording, and integration
with `Client` and `Response` for capture.

### W7: Implement graceful shutdown with connection migration

Add connection migration during graceful shutdown: before a worker
exits, it serialises in-flight connection state and transfers it to a
new worker via a Unix socket. Requires serialisation of `Client` state,
a migration protocol between old and new workers in `Cluster::Worker`,
`Reactor` support for adopted connections, `Server` support for
restored connections, and `Binder` listener hand-off.

### W8: Add automatic TLS certificate provisioning via ACME

Implement automatic Let's Encrypt certificate provisioning and renewal
as a plugin. Support HTTP-01 challenge solving, certificate storage,
automatic renewal before expiry, and seamless SSL context reload.
Requires a new `plugin/acme.rb`, an ACME client for account creation
and certificate ordering, integration with `Binder` for challenge
routing, `MiniSSL::ContextBuilder` for cert installation, and DSL
configuration.

### W9: Implement a traffic shaping and rate limiting framework

Add configurable rate limiting in `Server`: per-IP, per-route, and
global request rate limits with sliding-window counters, burst
allowances, and configurable responses (429, retry headers). Support
distributed rate limiting via shared storage. Requires a `RateLimiter`
module, integration with `Client` identification, `Server` pre-dispatch
hooks, `Response` rate-limit headers, DSL configuration, and a
storage backend abstraction.

### W10: Add observability integration with distributed tracing

Implement OpenTelemetry-compatible distributed tracing: create spans
for request lifecycle phases (accept, queue, process, respond),
propagate trace context from incoming headers, support W3C Trace
Context and B3 formats, and export via OTLP. Requires instrumentation
in `Server`, `ThreadPool`, `Client`, `Reactor`, a `Tracing` module
for context propagation, span management, and DSL configuration for
exporter endpoints.

### W11: Comprehensive overhaul of all documentation and upgrade guides

The `docs/` directory contains 18 markdown files with inconsistent
formatting, broken internal cross-references, and outdated
configuration examples. The `5.0-Upgrade.md` and `6.0-Upgrade.md`
upgrade guides do not cross-reference each other or link to the
relevant `docs/` pages. The `docs/architecture.md` architectural
overview does not include the `Plugin` component in its diagram or
text. The `SECURITY.md` policy references an email address
but does not mention GitHub's private vulnerability reporting.
The `docs/java_options.md` file documents JRuby-specific system
properties and environment variables but is not linked from `README.md`
or `docs/architecture.md`, leaving JRuby users without a clear
discovery path for this configuration. Overhaul all `docs/*.md` files
for consistent formatting and cross-references, update both upgrade
guides with links to relevant documentation pages, update
`docs/architecture.md` with a complete component diagram that includes
the Plugin and Reactor components, update `SECURITY.md` with
GitHub vulnerability reporting instructions, and add a link to
`docs/java_options.md` from `README.md` and `docs/architecture.md`.
