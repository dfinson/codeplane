# gabime/spdlog

| Field | Value |
|-------|-------|
| **URL** | https://github.com/gabime/spdlog |
| **License** | MIT |
| **Language** | C++ |
| **Scale** | Medium (header-only with modular sinks and details) |
| **Category** | Logging library |
| **Set** | Cutoff |

## Why this repo

- **De facto C++ logging standard**: The most popular C++ logging
  library with 24K+ stars, combining a clean header-only API with
  high performance via lock-free queues and fmt-based formatting.
  Navigation requires understanding sink hierarchies, async thread
  pools, and compile-time log-level stripping.
- **Well-structured**: Despite being header-only, the source is
  organized into `sinks/` (~20 sink implementations), `details/`
  (internal machinery for OS abstraction, threading, formatting),
  and `cfg/` (runtime configuration loading) — each with clear
  responsibilities and consistent patterns.
- **Rich feature surface**: Supports synchronous and async logging,
  pattern-based formatting, rotating/daily file sinks, colored
  console output, syslog/systemd/Windows Event Log integration,
  custom sinks via CRTP base classes, backtrace ring buffers,
  and runtime configuration via environment variables or files.
- **Permissive**: MIT license.

## Structure overview

```
include/spdlog/
├── spdlog.h                          # Main public API (default logger shortcuts)
├── logger.h                          # logger class (log, flush, set_level, sinks)
├── async_logger.h                    # Async logger subclass (thread pool based)
├── common.h                          # Enums (level, pattern_time), macros, typedefs
├── tweakme.h                         # Compile-time configuration knobs
├── version.h                         # Version macros
├── formatter.h                       # formatter interface (abstract base)
├── pattern_formatter.h               # Pattern-based formatter (%Y-%m-%d %H:%M:%S, etc.)
├── async.h                           # Async helpers (init/create thread pool)
├── stopwatch.h                       # Elapsed-time stopwatch utility
├── cfg/
│   ├── argv.h                        # Config from command-line arguments
│   ├── env.h                         # Config from environment variables (SPDLOG_LEVEL)
│   ├── helpers.h                     # Parsing helpers for level strings
│   └── log_levels.h                  # Level map container
├── details/
│   ├── registry.h                    # Global logger registry (singleton)
│   ├── registry-inl.h               # Registry inline implementation
│   ├── thread_pool.h                 # Thread pool for async logging
│   ├── thread_pool-inl.h            # Thread pool inline implementation
│   ├── log_msg.h                     # log_msg struct (payload passed to sinks)
│   ├── log_msg_buffer.h             # Owning version of log_msg
│   ├── log_msg-inl.h                # log_msg inline implementation
│   ├── os.h                          # OS abstraction (file ops, time, console)
│   ├── os-inl.h                      # OS inline implementation
│   ├── fmt_helper.h                  # fmt formatting utilities
│   ├── backtracer.h                  # Backtrace ring buffer for dump_backtrace()
│   ├── circular_q.h                  # Lock-free circular queue
│   ├── mpmc_blocking_q.h            # Multi-producer multi-consumer blocking queue
│   ├── periodic_worker.h            # Periodic flush worker thread
│   ├── null_mutex.h                  # No-op mutex for single-threaded sinks
│   ├── synchronous_factory.h        # Factory for synchronous logger creation
│   ├── file_helper.h                 # File open/close/write helpers
│   ├── file_helper-inl.h            # File helper inline implementation
│   ├── tcp_client.h                  # TCP socket client for tcp_sink
│   ├── tcp_client-inl.h             # TCP client inline implementation
│   ├── udp_client.h                  # UDP socket client for udp_sink
│   └── windows_include.h            # Windows header management
├── sinks/
│   ├── base_sink.h                   # CRTP base class for all sinks
│   ├── sink.h                        # Abstract sink interface
│   ├── basic_file_sink.h            # Simple file sink
│   ├── rotating_file_sink.h         # Size-based rotating file sink
│   ├── daily_file_sink.h            # Time-based daily rotating file sink
│   ├── hourly_file_sink.h           # Hourly rotating file sink
│   ├── stdout_color_sinks.h         # Colored stdout/stderr sinks
│   ├── stdout_sinks.h               # Plain stdout/stderr sinks
│   ├── ansicolor_sink.h             # ANSI color formatting sink
│   ├── wincolor_sink.h              # Windows console color sink
│   ├── null_sink.h                   # Discard-all sink (benchmarking)
│   ├── ostream_sink.h               # std::ostream-backed sink
│   ├── dist_sink.h                   # Fan-out distribution sink
│   ├── dup_filter_sink.h            # Duplicate message filter sink
│   ├── ringbuffer_sink.h            # In-memory ring buffer sink
│   ├── callback_sink.h              # User callback sink
│   ├── syslog_sink.h                # POSIX syslog sink
│   ├── systemd_sink.h               # systemd journal sink
│   ├── tcp_sink.h                    # TCP socket sink
│   ├── udp_sink.h                    # UDP socket sink
│   ├── mongo_sink.h                  # MongoDB sink
│   ├── qt_sinks.h                    # Qt signal-based sink
│   └── msvc_sink.h                   # MSVC OutputDebugString sink
└── fwd.h                             # Forward declarations
```

## Scale indicators

- ~60 header files under `include/spdlog/`
- ~15K lines of C++ header-only code
- ~20 sink types following CRTP `base_sink` pattern
- Zero required dependencies (optional fmt, header-only)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix rotating_file_sink not handling max_files=0 correctly

The `rotating_file_sink` in `sinks/rotating_file_sink.h` accepts
`max_files=0` in its constructor without validation. When rotation
triggers, the sink attempts to rename files with a modular arithmetic
expression that produces incorrect filenames when `max_files` is zero.
Fix the constructor to reject `max_files=0` with an
`spdlog_ex` exception.

### N2: Fix pattern_formatter leaking user-provided custom flags

`pattern_formatter` in `pattern_formatter.h` stores custom flag
formatters provided via `add_flag()` in a map of raw pointers cloned
during `clone()`. When a formatter is replaced by calling `add_flag()`
with the same character a second time, the previously registered
formatter is leaked. Fix the replacement path to delete the existing
entry before inserting the new one.

### N3: Fix daily_file_sink calculating next rotation time incorrectly across DST

The `daily_file_sink` in `sinks/daily_file_sink.h` computes the next
rotation time by adding 24 hours to the current rotation point. When
a DST transition occurs, this causes the rotation to fire one hour
early or late. Fix `next_rotation_tp_()` to compute the target
wall-clock time directly using `std::mktime` rather than adding a
fixed duration.

### N4: Fix log_msg_buffer not copying source location string data

`log_msg_buffer` in `details/log_msg_buffer.h` copies the `payload`
string but stores `source_location` fields as `string_view` pointing
into the original `log_msg`'s memory. When the original `log_msg`
is destroyed, the source location fields become dangling. Fix the
buffer to own copies of the filename and function name strings.

### N5: Fix circular_q::overwrite_push not updating count on full queue

`circular_q::overwrite_push()` in `details/circular_q.h` always
increments the count after pushing an element, even when the queue
is already full and the oldest element is being overwritten. This
causes `size()` to return a value larger than capacity. Fix the
method to keep count unchanged when the queue is full.

### N6: Fix backtracer not respecting logger level when dumping

The `backtracer` in `details/backtracer.h` stores all messages
regardless of the logger's current level and dumps all stored
messages during `dump_backtrace()`. Messages below the current
log level that were stored when the level was more permissive
should still be dumped, but the backtrace does not stamp messages
with the level at capture time, making it impossible to replay
them with correct filtering. Fix the backtrace to store the
logger level at capture time alongside each message.

### N7: Fix dup_filter_sink comparing only payload without level

The `dup_filter_sink` in `sinks/dup_filter_sink.h` suppresses
consecutive duplicate messages by comparing only the message payload.
Two messages with identical text but different log levels (e.g.,
one warning and one error) are incorrectly collapsed into one. Fix
the duplicate detection to also compare the log level.

### N8: Fix file_helper::open not setting file permissions on POSIX

`file_helper::open()` in `details/file_helper.h` uses `std::fopen`
to create log files, inheriting the process umask. There is no way
to specify file permissions for newly created log files. Add an
optional `mode_t` parameter to `open()` that calls `fchmod` after
file creation on POSIX platforms to set the desired permissions.

### N9: Fix thread_pool destructor not draining pending messages

The `thread_pool` destructor in `details/thread_pool.h` posts a
`terminate` message and joins worker threads, but does not drain
remaining messages in the MPMC queue. Messages posted between the
last flush and the `terminate` signal are silently dropped. Fix the
destructor to drain and process all pending log messages before
posting the terminate signal.

### N10: Fix cfg::helpers not parsing level names case-insensitively

The level-parsing helper in `cfg/helpers.h` compares level name
strings using exact case matching. Setting `SPDLOG_LEVEL=WARNING`
(uppercase) via the environment is rejected because the parser
expects `warning` (lowercase). Fix the parsing to perform
case-insensitive comparison.

## Medium

### M1: Add structured JSON logging formatter

Implement a `json_formatter` class alongside `pattern_formatter` in
`pattern_formatter.h` that emits log messages as single-line JSON
objects with fields for timestamp, level, logger name, thread ID,
source location, and message. The formatter must implement the
`formatter` interface in `formatter.h`, integrate with the
`logger` class in `logger.h` for per-logger formatter selection,
and support user-configurable field inclusion/exclusion.

### M2: Implement log sampling sink with rate limiting

Create a `sampling_sink` in `sinks/` that wraps another sink and
applies configurable rate limiting: N messages per time window,
with support for per-level rate limits. The sink must integrate
with the `base_sink` CRTP pattern in `sinks/base_sink.h`, use
the `circular_q` from `details/circular_q.h` for token bucket
tracking, and be selectable through `logger.h` sink composition.

### M3: Add runtime pattern reconfiguration via registry

Extend the `registry` in `details/registry.h` with a new
`set_pattern(logger_name, pattern)` method that changes the
formatting pattern of a specific logger at runtime. The change
must propagate through the logger's sinks in `logger.h`, handle
the async logger case in `async_logger.h` by signaling the
thread pool in `details/thread_pool.h` to swap formatters
without losing messages, and update the `cfg/` layer to support
reloading patterns from environment variables.

### M4: Implement file sink with compression support

Create a `compressed_file_sink` in `sinks/` that writes log
output using gzip compression. The sink must follow the
`base_sink` CRTP pattern, integrate with `details/file_helper.h`
for file management, support rotation compatible with
`rotating_file_sink.h` semantics, and implement a flush strategy
that finalizes the gzip stream on flush calls without closing the
file.

### M5: Add conditional logging with lazy message evaluation

Implement a `log_if(condition, level, msg)` method on the `logger`
class in `logger.h` that evaluates the format string and arguments
only if both the condition is true and the level is active. Add
corresponding macros in `spdlog.h` for the default logger, update
the async path in `async_logger.h` to support conditional logging
without allocating log_msg objects when the condition is false, and
add pattern_formatter support for rendering the condition status.

### M6: Implement scoped context logging with key-value pairs

Add an `spdlog::context` RAII class that attaches key-value pairs
to all log messages within a scope via thread-local storage. The
context data must flow through `details/log_msg.h` as an optional
field, be rendered by `pattern_formatter.h` with a new `%&` flag,
and be correctly propagated across the async boundary in
`details/thread_pool.h` by copying context into `log_msg_buffer.h`.

### M7: Add hot-reload support for configuration files

Extend the `cfg/` module with a `file_watcher` that monitors a
TOML or INI configuration file and applies changes at runtime.
On change, update logger levels via `cfg/log_levels.h`, update
formatting patterns via the registry in `details/registry.h`, and
add or remove sinks from loggers in `logger.h`. Use the
`periodic_worker` from `details/periodic_worker.h` for polling.

### M8: Implement correlation ID propagation across loggers

Add support for an `spdlog::correlation_id` that is attached to
a logging scope and automatically included in all log messages
across multiple loggers. The ID must be stored in thread-local
storage, carried through `details/log_msg.h`, serialized by
`pattern_formatter.h` via a new `%x` flag, preserved when
messages cross the async boundary in `details/thread_pool.h`,
and configurable via the `cfg/` layer.

### M9: Add sink filtering by log level range

Implement a `level_filter_sink` in `sinks/` that wraps another
sink and only forwards messages within a configurable level range
(e.g., only warnings and errors). The filter must compose with
`dist_sink.h` for fan-out routing, integrate with `logger.h` for
per-sink level overrides, and support runtime reconfiguration
through the `registry` in `details/registry.h`.

### M10: Implement log message enrichment with system metadata

Add an `enrichment_sink` in `sinks/` that decorates log messages
with system metadata (hostname, PID, thread name, executable path)
before forwarding to a wrapped sink. The metadata collection must
use OS abstractions from `details/os.h`, the enrichment must be
visible to `pattern_formatter.h` via new format flags, and the
sink must correctly handle the async case where metadata should
be captured at log time rather than sink time.

## Wide

### W1: Implement distributed logging with log aggregation protocol

Build a log aggregation system with a `collector_sink` that
serializes log messages into a compact binary protocol and sends
them to a remote collector over TCP. Implement a corresponding
collector server that deserializes and routes messages to local
sinks. Changes span a new `sinks/collector_sink.h` for the client
sink, `details/tcp_client.h` for protocol framing,
`details/log_msg.h` for binary serialization of log messages,
the `registry` in `details/registry.h` for collector registration,
`pattern_formatter.h` for rendering collector metadata, and
`cfg/` for collector endpoint configuration.

### W2: Add OpenTelemetry-compatible log exporter

Implement an OpenTelemetry Logs exporter that maps spdlog messages
to the OTLP log data model. Build an `otlp_sink` that batches log
records and exports them via gRPC or HTTP. Changes span a new
`sinks/otlp_sink.h` for the exporter, `details/log_msg.h` for
trace context (trace_id, span_id) fields, `pattern_formatter.h`
for trace context rendering, `details/thread_pool.h` for batched
async export, `logger.h` for trace context injection API, and
`cfg/` for OTLP endpoint and resource attribute configuration.

### W3: Implement compile-time log format validation and optimization

Build a compile-time system that validates pattern format strings
at compile time and generates optimized formatter code. Use
`constexpr` parsing to decompose patterns in `pattern_formatter.h`
into a sequence of typed formatter objects, eliminate runtime
pattern parsing overhead, detect invalid flags at compile time,
and generate specialized fast paths for common patterns. Changes
span `pattern_formatter.h` for constexpr pattern decomposition,
`common.h` for compile-time string utilities, `formatter.h` for
optimized static formatter interface, `logger.h` for templated
log methods using static formatters, `spdlog.h` for macro updates,
and `details/fmt_helper.h` for constexpr format helpers.

### W4: Add multi-process safe file logging with advisory locking

Implement file sinks that are safe for multiple processes writing
to the same log file. Build a `shared_file_sink` with advisory
file locking, atomic append support, and stale lock recovery.
Changes span a new `sinks/shared_file_sink.h` for the locking
sink, `details/file_helper.h` for advisory lock operations and
atomic append mode, `details/os.h` for cross-platform file locking
primitives, `sinks/rotating_file_sink.h` for multi-process-safe
rotation coordination, `sinks/daily_file_sink.h` for coordinated
daily rotation, and `details/registry.h` for shared file handle
management across loggers.

### W5: Implement hierarchical logger namespaces with inheritance

Build a hierarchical logger system where loggers form a tree based
on dot-separated names (e.g., `app.db.query` inherits from `app.db`).
Child loggers inherit parent levels, sinks, and formatters unless
overridden. Changes span `details/registry.h` for tree-structured
logger storage and lookup, `logger.h` for parent pointer and
inheritance resolution, `async_logger.h` for inherited thread pool
configuration, `sinks/base_sink.h` for inherited sink chains,
`pattern_formatter.h` for logger hierarchy rendering, and `cfg/`
for hierarchical level configuration syntax.

### W6: Add log pipeline with filter, transform, and route stages

Build a configurable log processing pipeline where messages flow
through filter, transform, and route stages before reaching sinks.
Implement a DSL for pipeline definitions in configuration files.
Changes span a new `pipeline/` directory with `filter_stage.h`,
`transform_stage.h`, and `route_stage.h`, modifications to
`logger.h` for pipeline attachment, `details/log_msg.h` for
mutable message context through stages, `details/thread_pool.h`
for async pipeline execution, `cfg/` for pipeline DSL parsing,
and `sinks/dist_sink.h` for pipeline-aware fan-out.

### W7: Implement structured logging with type-safe fields and indexing

Build a structured logging system where log messages carry typed
key-value fields alongside the text payload. Fields are
first-class objects available for programmatic access, filtering,
and indexing. Changes span `details/log_msg.h` for field storage,
`logger.h` for field-aware log API, `pattern_formatter.h` for
field rendering with format specifiers, `sinks/base_sink.h` for
field-aware sink interface, `sinks/basic_file_sink.h` for indexed
field output, `details/thread_pool.h` for field propagation across
async boundary, and `cfg/` for field schema configuration.

### W8: Add dynamic sink routing based on message content

Implement a `routing_sink` that dispatches log messages to different
backend sinks based on configurable matching rules applied to the
message payload, level, logger name, and structured fields.
Support regex matching, glob patterns, and custom predicates.
Changes span a new `sinks/routing_sink.h` for the router,
`sinks/base_sink.h` for route-aware sink interface,
`details/log_msg.h` for routing metadata, `details/registry.h`
for route table management, `logger.h` for route override API,
`pattern_formatter.h` for route-hit logging, and `cfg/` for
routing rule configuration syntax.

### W9: Implement log replay and time-travel debugging system

Build a log capture and replay system that records all log messages
with precise timestamps into a binary journal file and supports
replaying them through arbitrary sink configurations at original
or modified speed. Changes span a new `sinks/journal_sink.h` for
binary capture, `details/log_msg.h` for nanosecond timestamp
precision, a new `replay/journal_reader.h` for binary parsing,
`replay/player.h` for timed playback, `logger.h` for replay
logger creation, `pattern_formatter.h` for replay timestamp
rendering, and `details/thread_pool.h` for time-simulated async
replay.

### W10: Add multi-tenant logging with namespace isolation and resource limits

Implement a multi-tenant logging system where separate tenants
get isolated logger namespaces, independent sink configurations,
and per-tenant resource limits (message rate, buffer size, file
quota). Changes span `details/registry.h` for tenant-scoped
registries, `logger.h` for tenant-aware logger construction,
`details/thread_pool.h` for per-tenant async queue partitioning
with backpressure, `sinks/rotating_file_sink.h` for tenant quota
enforcement, `sinks/base_sink.h` for tenant-scoped sink lifecycle,
`details/backtracer.h` for tenant-isolated backtrace buffers,
and `cfg/` for tenant configuration schema.
