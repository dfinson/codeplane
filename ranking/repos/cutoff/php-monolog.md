# Seldaek/monolog

| Field | Value |
|-------|-------|
| **URL** | https://github.com/Seldaek/monolog |
| **License** | MIT |
| **Language** | PHP |
| **Scale** | Medium (comprehensive logging library) |
| **Category** | Logging / Observability |
| **Set** | Cutoff |

## Why this repo

- **PSR-3 reference implementation**: The de-facto standard logging library
  for PHP, implementing the PSR-3 `LoggerInterface`. Navigation requires
  understanding handler stacks, formatter chains, processor pipelines, and
  the channel-based logging model.
- **Well-structured**: Core code under `src/Monolog/` with `Logger.php`
  (main logger class, handler stack, processor pipeline), `Handler/`
  (~30 handlers: `StreamHandler`, `RotatingFileHandler`, `SyslogHandler`,
  `SlackHandler`, `ElasticsearchHandler`, etc.), `Formatter/`
  (`LineFormatter`, `JsonFormatter`, `HtmlFormatter`, etc.), `Processor/`
  (`IntrospectionProcessor`, `WebProcessor`, `MemoryUsageProcessor`, etc.),
  `ResettableInterface.php`, and `Registry.php`.
- **Rich history**: 21K+ stars, bundled with Laravel and Symfony by default.
  Issues span handler configuration, formatter edge cases, log rotation
  strategies, and third-party service integration.
- **Permissive**: MIT license.

## Structure overview

```
src/Monolog/
├── Logger.php                      # Main logger — handler stack, processor
│                                   #   pipeline, channel name, log levels
├── Registry.php                    # Global named-logger registry
├── ResettableInterface.php         # Interface for resettable handlers/processors
├── LogRecord.php                   # Immutable log record DTO
├── Level.php                       # Log level enum (DEBUG..EMERGENCY)
├── DateTimeImmutable.php           # Extended DateTimeImmutable with microseconds
├── Handler/
│   ├── AbstractHandler.php         # Base handler — level filtering, bubble flag
│   ├── AbstractProcessingHandler.php  # Adds formatter + processor support
│   ├── AbstractSyslogHandler.php   # Base for syslog-family handlers
│   ├── StreamHandler.php           # File/stream writing handler
│   ├── RotatingFileHandler.php     # Date-based log file rotation
│   ├── FirePHPHandler.php          # FirePHP header-based debugging
│   ├── ChromePHPHandler.php        # Chrome Logger header output
│   ├── SyslogHandler.php           # Native syslog handler
│   ├── SyslogUdpHandler.php        # UDP syslog handler (RFC 5424)
│   ├── ErrorLogHandler.php         # PHP error_log() handler
│   ├── NullHandler.php             # Discards records (testing/disabling)
│   ├── BufferHandler.php           # Buffers records, flushes on close/threshold
│   ├── DeduplicationHandler.php    # Deduplicates repeated messages
│   ├── GroupHandler.php            # Dispatches to multiple handlers
│   ├── FilterHandler.php           # Filters by log level range
│   ├── FallbackGroupHandler.php    # Tries handlers until one succeeds
│   ├── FingersCrossedHandler.php   # Activates on threshold, flushes buffer
│   ├── WhatFailureGroupHandler.php # GroupHandler that swallows exceptions
│   ├── SocketHandler.php           # TCP/UDP socket writing
│   ├── RedisHandler.php            # Redis list/pubsub handler
│   ├── MongoDBHandler.php          # MongoDB collection handler
│   ├── ElasticsearchHandler.php    # Elasticsearch bulk indexing
│   ├── SlackHandler.php            # Slack legacy webhook handler
│   ├── SlackWebhookHandler.php     # Slack incoming webhook handler
│   ├── SendGridHandler.php         # SendGrid email handler
│   ├── NativeMailerHandler.php     # PHP mail() handler
│   ├── SwiftMailerHandler.php      # SwiftMailer email handler
│   ├── SymfonyMailerHandler.php    # Symfony Mailer email handler
│   ├── TelegramBotHandler.php      # Telegram Bot API handler
│   ├── PushoverHandler.php         # Pushover notification handler
│   ├── SamplingHandler.php         # Probabilistic sampling handler
│   ├── OverflowHandler.php         # Rate-limiting overflow handler
│   ├── TestHandler.php             # Assertion helper for testing
│   └── HandlerInterface.php        # Handler contract
├── Formatter/
│   ├── FormatterInterface.php      # Formatter contract
│   ├── NormalizerFormatter.php     # Base — normalizes record data to scalars
│   ├── LineFormatter.php           # Single-line text format
│   ├── JsonFormatter.php           # JSON-encoded log output
│   ├── HtmlFormatter.php           # HTML table format for email handlers
│   ├── WildfireFormatter.php       # Wildfire/FirePHP protocol format
│   ├── ChromePHPFormatter.php      # Chrome Logger protocol format
│   ├── GelfMessageFormatter.php    # GELF (Graylog) message format
│   ├── LogstashFormatter.php       # Logstash JSON format
│   ├── ElasticsearchFormatter.php  # Elasticsearch bulk format
│   ├── FlowdockFormatter.php       # Flowdock message format
│   ├── MongoDBFormatter.php        # BSON-compatible format
│   ├── LogmaticFormatter.php       # Logmatic.io format
│   ├── FluentdFormatter.php        # Fluentd forward format
│   └── ScalarFormatter.php         # Scalars-only format
├── Processor/
│   ├── ProcessorInterface.php      # Processor contract
│   ├── IntrospectionProcessor.php  # Adds file, line, class, function
│   ├── WebProcessor.php            # Adds URL, IP, method, referrer
│   ├── MemoryUsageProcessor.php    # Adds current memory usage
│   ├── MemoryPeakUsageProcessor.php # Adds peak memory usage
│   ├── ProcessIdProcessor.php      # Adds PID
│   ├── UidProcessor.php            # Adds unique request ID
│   ├── GitProcessor.php            # Adds git branch and commit hash
│   ├── MercurialProcessor.php      # Adds Mercurial branch and revision
│   ├── TagProcessor.php            # Adds static tags
│   ├── HostnameProcessor.php       # Adds server hostname
│   └── PsrLogMessageProcessor.php  # Interpolates PSR-3 {placeholder} tokens
└── Test/
    └── TestCase.php                # Base test case helper
```

## Scale indicators

- ~80 PHP source files
- ~12K lines of code
- Deep handler hierarchy with abstract base classes
- Formatter and processor plugin architecture

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix StreamHandler not releasing file lock on write failure

When `StreamHandler` acquires a lock via `flock()` before writing and
the `fwrite()` call fails (e.g., disk full), the lock is not released
because the exception bypasses the unlock path. Fix `StreamHandler.php`
to ensure `flock()` is released in a `finally` block regardless of
write outcome.

### N2: Fix RotatingFileHandler not pruning old files when date format changes

`RotatingFileHandler` computes the rotated filename using a date format
string, but the glob pattern used for pruning assumes the default
`Y-m-d` format. When users configure a custom date format (e.g.,
`Y-m-d-H`), old files are never cleaned up. Fix the glob construction
in `RotatingFileHandler.php` to derive the pattern from the configured
date format.

### N3: Fix JsonFormatter encoding errors silently producing empty output

When `JsonFormatter::format()` encounters a record containing data that
fails `json_encode()` (e.g., invalid UTF-8 sequences), it returns an
empty string or `null` instead of a fallback representation. Fix
`JsonFormatter.php` to detect encoding failures via `json_last_error()`
and produce a safe fallback JSON string containing the error details
and the sanitized message.

### N4: Fix LineFormatter not escaping newlines in context data

`LineFormatter` replaces newlines in the main message via
`allowInlineLineBreaks`, but context and extra arrays rendered by
`stringify()` can still contain raw newlines, breaking single-line log
parsing. Fix `LineFormatter.php` to apply newline escaping consistently
to all serialized parts of the output.

### N5: Fix BufferHandler exceeding memory limit when bufferLimit is zero

`BufferHandler` treats `bufferLimit = 0` as "unlimited" but never checks
available memory, allowing unbounded growth that can exhaust PHP's
memory limit. Fix `BufferHandler.php` to interpret `bufferLimit = 0`
as truly unlimited but add a safety flush when the buffer count exceeds
a configurable high-water mark, preventing out-of-memory crashes.

### N6: Fix NormalizerFormatter::setMaxNormalizeDepth accepting zero and negative values

`NormalizerFormatter` in `Formatter/NormalizerFormatter.php` allows
setting `$maxNormalizeDepth` to any integer via `setMaxNormalizeDepth()`.
Setting it to `0` causes `normalize()`'s bail-out check
(`$depth > $this->maxNormalizeDepth`) to trigger immediately at
`$depth = 0`, replacing all data — including simple strings and
integers — with `"Over 0 levels deep, aborting normalization"`.
Negative values bypass depth protection entirely, allowing unbounded
recursion. Fix `setMaxNormalizeDepth()` to reject values less than 1
with an `\InvalidArgumentException`.

### N7: Fix GitProcessor caching stale branch and commit data in long-running processes

`GitProcessor` in `Processor/GitProcessor.php` caches the git branch
and commit hash in a static class property (`$cache`) that is
populated once via `git log` and `git branch` shell commands and
never refreshed. In long-running processes (queue workers, daemons,
Swoole servers), the cached data becomes stale when new commits are
pushed to the repository, causing all log records to report an
outdated commit hash. Fix `GitProcessor.php` to accept an optional
TTL parameter that expires the cache after a configurable interval,
triggering a re-read of git data.

### N8: Fix SyslogUdpHandler not splitting messages exceeding UDP max size

`SyslogUdpHandler` sends the entire formatted message in a single UDP
datagram. Messages exceeding the 65507-byte UDP limit are silently
discarded by the OS. Fix `SyslogUdpHandler.php` to chunk oversized
messages into multiple datagrams with continuation markers per RFC 5424.

### N9: Fix FingersCrossedHandler not resetting buffer after activation

After `FingersCrossedHandler` activates (threshold reached) and flushes
its buffer, subsequent records below the threshold continue to be
buffered but are never flushed because the handler remains in the
activated state. Fix the handler to transition back to buffering mode
after a configurable reset interval or when `reset()` is called.

### N10: Fix PsrLogMessageProcessor removing context entries for lossy object replacements

`PsrLogMessageProcessor` in `Processor/PsrLogMessageProcessor.php`
replaces `{key}` placeholders with context values and, when
`$removeUsedContextFields` is enabled, removes the context entry
after substitution. For non-stringable objects, the replacement is
the lossy string `[object ClassName]`, discarding all object data.
Despite the replacement losing information, the original context
entry is still removed, preventing downstream handlers and formatters
from accessing the full object. Fix the processor to preserve context
entries when the replacement is a lossy representation (plain objects,
arrays, resource types) so the original data remains available to
other handlers.

## Medium

### M1: Add structured context redaction support to NormalizerFormatter

Implement a `RedactingNormalizerFormatter` or extend
`NormalizerFormatter` with a configurable list of context keys (e.g.,
`password`, `token`, `secret`) whose values are replaced with
`[REDACTED]` during normalization. Support nested key paths using
dot notation (e.g., `user.credentials.password`). Changes span
`NormalizerFormatter.php` for the redaction logic and
`LineFormatter.php` / `JsonFormatter.php` which inherit from it.

### M2: Implement handler circuit breaker for unreachable services

Add a `CircuitBreakerHandler` decorator that wraps any handler and
tracks consecutive failures. After a configurable failure threshold,
the circuit opens and bypasses the wrapped handler for a cooldown
period before attempting a probe write. Requires a new
`CircuitBreakerHandler.php` in `Handler/`, integration with
`AbstractProcessingHandler.php` for failure counting, and support
for the `ResettableInterface` to reset circuit state.

### M3: Add log record enrichment via middleware pipeline

Implement a middleware-style pipeline where each middleware receives
a `LogRecord`, can modify it, and passes it to the next middleware
before handler dispatch. This replaces the simple processor array
with a composable chain. Changes span `Logger.php` for the pipeline
execution, a new `Middleware/MiddlewareInterface.php`, and adapter
logic so existing `ProcessorInterface` implementations continue to
work as middleware.

### M4: Implement async log writing for SocketHandler and RedisHandler

Add non-blocking write support to `SocketHandler` and `RedisHandler`
using `stream_set_blocking(false)` and a write buffer that flushes
asynchronously. Support configurable buffer sizes and a synchronous
fallback on shutdown. Changes span `SocketHandler.php` for the async
stream logic, `RedisHandler.php` for pipelined writes, and a new
`AsyncWriteTrait.php` shared between both handlers.

### M5: Add correlation ID propagation across Logger instances

Implement automatic correlation ID injection so that all `Logger`
instances in a request share the same trace ID. Support reading the
ID from HTTP headers (`X-Request-ID`, `X-Correlation-ID`) or
generating one. Changes span a new `CorrelationIdProcessor.php` in
`Processor/`, `WebProcessor.php` for header extraction, `Logger.php`
for default processor registration, and `Registry.php` for
propagating the ID to all registered loggers.

### M6: Implement conditional handler activation based on log record content

Add a `ConditionalHandler` that evaluates a user-defined predicate
against each `LogRecord` and dispatches to one of two wrapped handlers
based on the result. Support predicates on level, channel, message
pattern, and context keys. Requires a new `ConditionalHandler.php`,
a `RecordMatcher.php` utility for predicate evaluation, and
integration with `HandlerInterface` for proper bubbling behavior.

### M7: Add batched Elasticsearch bulk indexing with retry logic

Extend `ElasticsearchHandler` to batch records and flush them using
the Elasticsearch `_bulk` API at configurable intervals or buffer
thresholds. Add exponential backoff retry on `429` and `503` responses.
Changes span `ElasticsearchHandler.php` for the batching and retry
logic, `ElasticsearchFormatter.php` for proper bulk-format serialization,
and `BufferHandler.php` patterns as reference for flush-on-close
behavior.

### M8: Implement log sampling with per-channel and per-level rates

Extend `SamplingHandler` to support different sampling rates per
channel name and per log level (e.g., 10% of DEBUG, 100% of ERROR).
Add a rate configuration DSL and deterministic sampling based on
message hash for reproducibility. Changes span `SamplingHandler.php`
for the multi-rate logic, a new `SamplingConfig.php` for rate
definitions, and `Logger.php` to pass channel context to the handler.

### M9: Add log record validation and schema enforcement

Implement a `ValidatingProcessor` that checks each `LogRecord` against
a configurable schema: required context keys, type constraints on
values, maximum message length, and allowed extra keys. Invalid records
are either rejected, sanitized, or annotated with validation warnings.
Requires a new `ValidatingProcessor.php` in `Processor/`, a
`RecordSchema.php` definition class, and integration with
`NormalizerFormatter.php` for type coercion.

### M10: Implement handler-level log level override with runtime adjustment

Add `AbstractHandler::setLevel(Level)` that allows changing the minimum
log level of a handler at runtime without reconstructing the handler
stack. When combined with `GroupHandler` or `BufferHandler`, the level
change must propagate to wrapped handlers via a `LevelAware` interface.
Changes span `AbstractHandler.php` for the setter and thread-safe
level storage, `AbstractProcessingHandler.php` for level-check
updating, `GroupHandler.php` for propagation to child handlers,
`BufferHandler.php` for re-filtering buffered records on level change,
and `Logger.php` for a `setHandlerLevel(string $handlerClass, Level)`
convenience method.

## Wide

### W1: Implement structured logging with first-class typed log events

Replace free-form string messages with typed log event classes that
carry structured data. Add a `LogEvent` base class with static factory
methods, automatic context extraction from event properties, and
formatter support for rendering events as structured JSON or human-
readable text. Changes span `Logger.php` for event dispatch,
`LogRecord.php` for event embedding, all formatters for event-aware
rendering, `PsrLogMessageProcessor.php` for event interpolation,
and backward-compatible PSR-3 message fallback.

### W2: Add multi-transport handler with failover and load balancing

Implement a `MultiTransportHandler` that sends log records to multiple
backends (file, syslog, Elasticsearch, Redis) with configurable
strategies: round-robin, failover, fanout, and weighted distribution.
Add health checking per transport and automatic failover with circuit
breaker integration. Changes span a new `MultiTransportHandler.php`,
a `TransportStrategy/` directory with strategy classes,
`AbstractProcessingHandler.php` for health-check hooks, `Logger.php`
for strategy-aware handler management, and `Registry.php` for
cross-logger transport sharing.

### W3: Implement log pipeline with filtering, routing, and transformation

Build a declarative log pipeline DSL where records flow through
filter → transform → route → sink stages. Filters match on level,
channel, message pattern, and context. Transforms modify records
(redact, enrich, rename keys). Routers dispatch to different handlers
by rules. Changes span a new `Pipeline/` directory with stage classes,
`Logger.php` for pipeline execution replacing the handler stack,
`Handler/FilterHandler.php` for reuse as a pipeline filter, all
processors adapted as pipeline transforms, and a YAML/array
configuration loader.

### W4: Add comprehensive metrics and observability for the logging system

Instrument Monolog itself with operational metrics: records per
second per handler, handler write latency histograms, buffer
utilization, formatter serialization time, dropped record counts,
and circuit breaker state transitions. Expose metrics via a
`MetricsCollector` that supports Prometheus exposition format and
StatsD. Changes span every handler for timing instrumentation,
`Logger.php` for aggregate counters, all formatters for serialization
timing, `BufferHandler.php` for utilization tracking, `Registry.php`
for global metrics aggregation, and a new `Metrics/` directory.

### W5: Implement log record persistence and replay system

Add a `PersistentBufferHandler` that writes log records to a local
SQLite or file-based journal before dispatching to the wrapped handler.
On handler failure, records are retained and replayed when the handler
recovers. Support journal compaction, TTL-based expiry, and concurrent
access from multiple PHP processes. Changes span a new
`PersistentBufferHandler.php`, a `Journal/` directory for storage
backends, `Logger.php` for replay triggering on startup,
`ResettableInterface.php` for journal reset, and integration with
`FingersCrossedHandler.php` for persistent buffering.

### W6: Add tenant-aware multi-channel logging with isolation

Implement tenant-scoped logging where each tenant gets isolated
handler stacks, formatters, and processors configured via a tenant
context. Add `TenantLogger` that delegates to per-tenant `Logger`
instances with lazy initialization and resource cleanup. Changes span
a new `TenantLogger.php`, `Registry.php` for tenant-aware logger
lookup, `Logger.php` for scoped handler resolution, all handlers
for tenant-prefixed output (file paths, Redis keys, Elasticsearch
indices), and `Processor/TenantProcessor.php` for automatic tenant
context injection.

### W7: Implement log aggregation with deferred batch processing

Build a centralized `LogAggregator` that collects records from
multiple `Logger` instances, deduplicates repeated messages within a
time window, aggregates counts, and flushes summary records to
handlers at configurable intervals. Support aggregation by message
template, level, and channel. Changes span a new `Aggregator/`
directory, `Logger.php` for aggregator integration,
`DeduplicationHandler.php` for shared dedup logic, all formatters
for rendering aggregated counts, `Registry.php` for connecting
loggers to the aggregator, and `ResettableInterface.php` for
flush-on-reset.

### W8: Add dynamic handler configuration with hot-reload support

Implement a configuration system that loads handler, formatter, and
processor definitions from YAML/JSON files and supports hot-reloading
without restarting the PHP process. Add a `ConfigurableLogger` that
watches the config file via `filemtime()` and reconfigures its handler
stack on change. Changes span a new `Config/` directory with loaders
and schema validation, `Logger.php` for dynamic reconfiguration,
all handler constructors for factory-based instantiation,
`Registry.php` for global config propagation, and
`ResettableInterface.php` for clean handler teardown during reload.

### W9: Implement cross-process distributed logging with gRPC transport

Add a `GrpcHandler` that sends log records to a remote log collector
via gRPC, with protobuf serialization, connection pooling, and
client-side buffering. Implement a companion `GrpcLogServer` that
receives records and dispatches to local handlers. Changes span a
new `Grpc/` directory with protobuf definitions and client/server
classes, `Logger.php` for gRPC handler registration,
`NormalizerFormatter.php` for protobuf-compatible normalization,
`LogRecord.php` for serialization/deserialization support,
`BufferHandler.php` patterns for client-side buffering, and
`ResettableInterface.php` for connection lifecycle management.

### W10: Add end-to-end log tracing with distributed context propagation

Implement OpenTelemetry-compatible trace context propagation through
the logging pipeline. Inject `trace_id`, `span_id`, and `baggage`
from incoming W3C `traceparent` headers into every log record. Support
context propagation across `Logger` instances, async handlers, and
buffered handlers. Changes span a new `Tracing/` directory with
context extractors and propagators, `WebProcessor.php` for header
parsing, `Logger.php` for trace context attachment, all handlers
for context forwarding through async/buffered paths,
`JsonFormatter.php` and `LogstashFormatter.php` for trace field
rendering, and `Registry.php` for trace context sharing across
loggers.
