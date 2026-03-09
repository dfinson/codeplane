| Field | Value |
|-------|-------|
| **URL** | https://github.com/sidekiq/sidekiq |
| **License** | LGPL-3.0 |
| **Language** | Ruby |
| **Scale** | Medium |
| **Category** | Background Jobs / Concurrency |
| **Set** | cutoff |
| **Commit** | `978e2bdf59ea09f796beb8c1f4763e985117e286` |

## Why this repo

Sidekiq is the dominant background job processing framework for Ruby. It uses Redis as a job queue and processes jobs with a multi-threaded architecture, making it an excellent test subject for code navigation tasks that span concurrency, middleware chains, Redis protocols, and web UI concerns. The codebase exercises Ruby threading primitives, Rack middleware, Sinatra-based web applications, retry logic with exponential backoff, connection pooling, and plugin architectures. Its clear separation between client-side job enqueuing and server-side job execution creates well-defined boundaries that test an agent's ability to trace data flow across subsystems.

## Structure overview

```
sidekiq/
├── bin/
│   └── sidekiq
├── lib/
│   ├── sidekiq.rb
│   └── sidekiq/
│       ├── cli.rb
│       ├── launcher.rb
│       ├── manager.rb
│       ├── processor.rb
│       ├── fetch.rb
│       ├── job_retry.rb
│       ├── scheduled.rb
│       ├── redis_connection.rb
│       ├── api.rb
│       ├── client.rb
│       ├── worker.rb
│       ├── job.rb
│       ├── config.rb
│       ├── logger.rb
│       ├── paginator.rb
│       ├── middleware/
│       │   ├── chain.rb
│       │   ├── current_attributes.rb
│       │   └── i18n.rb
│       ├── web/
│       │   ├── application.rb
│       │   ├── action.rb
│       │   ├── csrf_protection.rb
│       │   ├── helpers.rb
│       │   └── router.rb
│       └── testing/
│           ├── inline.rb
│           └── fake.rb
├── web/
│   ├── assets/
│   │   ├── javascripts/
│   │   └── stylesheets/
│   ├── locales/
│   └── views/
│       ├── busy.erb
│       ├── dashboard.erb
│       ├── dead.erb
│       ├── morgue.erb
│       ├── queue.erb
│       ├── queues.erb
│       ├── retries.erb
│       └── retry.erb
└── test/
    ├── test_api.rb
    ├── test_cli.rb
    ├── test_client.rb
    ├── test_fetch.rb
    ├── test_launcher.rb
    ├── test_manager.rb
    ├── test_middleware.rb
    ├── test_processor.rb
    ├── test_retry.rb
    ├── test_scheduled.rb
    ├── test_web.rb
    └── helper.rb
```

## Scale indicators

- ~12k lines of Ruby source
- Multi-threaded server architecture (Launcher → Manager → Processor)
- Client and server middleware chains with composable ordering
- Redis-backed queue with sorted-set scheduling
- Sinatra-based web dashboard with CSRF protection
- Retry subsystem with exponential backoff and dead-letter queue
- Connection pooling for Redis
- Inline and fake testing modes
- i18n support across web views
- Signal handling for graceful shutdown

## Tasks

## Narrow

### N1 – Fix retry delay calculation lacks an upper bound for large retry counts

The exponential backoff formula in `job_retry.rb` computes the delay as `count**4 + 15` seconds. Ruby uses arbitrary-precision integers so there is no arithmetic overflow, but there is no upper cap on the result. When operators configure a non-default `max_retries` value much larger than the default 25, the delay grows to astronomical values (e.g., count=100 yields ~1.16 million seconds, over 13 days), making the job effectively unreachable. Fix `job_retry.rb` to cap the computed delay at a configurable maximum (e.g., defaulting to `MAX_RETRY_DELAY = 86_400` seconds) before adding jitter.

### N2 – Scheduled poller skips jobs when system clock drifts backward

The scheduled set poller in scheduled.rb compares job scores against Time.now.to_f. When the system clock drifts backward (e.g., NTP correction), jobs whose scores fall in the gap between the old and new time are never dequeued until the clock catches up. The comparison should account for backward drift.

### N3 – Web dashboard to_display helper does not cap exception message length

The `to_display` method in `web/helpers.rb` calls `arg.inspect` with
a rescue fallback to `arg.to_s`. If both raise, the final rescue
interpolates `ex.message` into the fallback string without length
limiting. A broken object whose `inspect` and `to_s` raise exceptions
with very large messages (e.g., containing a full serialized payload)
can cause the dashboard page to balloon in size and slow rendering.
Fix `web/helpers.rb` to truncate `ex.message` in the fallback display
string to a reasonable maximum (e.g., 200 characters). Also update
`web/views/morgue.html.erb` to add a CSS `max-height` with an
overflow toggle on the job arguments cell, so that even non-exception
large payloads render without blowing up the page layout.

### N4 – BasicFetch brpop timeout is hardcoded and not configurable

The `BasicFetch` class in `fetch.rb` declares `TIMEOUT = 2` as a module constant and uses it as both the `brpop` blocking timeout and the sleep duration when the queue list is empty. This value is not present in `Sidekiq::Config::DEFAULTS` in `config.rb` and cannot be tuned by operators. Under graceful shutdown, each worker thread must wait up to `TIMEOUT` seconds before detecting the `@done` flag; with 25 concurrent threads all blocked simultaneously, shutdown responsiveness scales with this constant. Fix `fetch.rb` to read the timeout from `config[:fetch_timeout]` with a fallback of 2, and add `fetch_timeout: 2` to `Sidekiq::Config::DEFAULTS` in `config.rb`.

### N5 – Logger context lost after middleware chain exception

When a server middleware raises and the exception propagates through the chain, the structured logging context (job ID, queue name) set by the processor is cleared before the error handler runs. The error log entry lacks the job context needed for debugging.

### N6 – Scheduled poller zpopbyscore retries NOSCRIPT without a depth limit

The `zpopbyscore` method in `scheduled.rb` rescues `NOSCRIPT` errors,
sets `@lua_zpopbyscore_sha = nil`, and calls `retry` with no guard on
the number of attempts. If the Redis server repeatedly returns
`NOSCRIPT` after a fresh `SCRIPT LOAD` — for example when
`maxmemory-policy` is configured to evict Lua scripts immediately, or
after a failover where the new primary rejects the freshly loaded
script before the connection is fully established — the method loops
infinitely rather than surfacing the error to the caller. Fix
`scheduled.rb` to track the number of NOSCRIPT retries and raise the
original `RedisClient::CommandError` after more than one attempt, so
persistent script-loading failures propagate to the `Poller#enqueue`
error handler instead of hanging the scheduler thread.

### N7 – Dead job display truncates large payloads in web UI

The `display_args` helper in `web/helpers.rb` truncates each argument string to 2000 characters with no expand option. For jobs with large serialized payloads, this makes it impossible to inspect the full arguments from the morgue view in the dashboard.

### N8 – Web application lacks a lightweight health endpoint for readiness probes

The Sidekiq web application exposes a `/stats` JSON endpoint that returns full queue and Redis statistics, but there is no dedicated lightweight health endpoint suitable for Kubernetes readiness probes or load-balancer health checks. Add a `/health` route to the web application that performs a minimal Redis ping and returns HTTP 200 with a JSON body when the Redis connection is available, or HTTP 503 when it is not. The endpoint should also report a degraded status when the Redis connection pool is fully checked out beyond a configurable threshold.

### N9 – INFO signal silently fails on Linux, leaving no signal-triggered backtrace mechanism

The `INFO` signal is a BSD/macOS-specific signal that is not available on Linux. In `cli.rb`, `Signal.trap("INFO")` raises `ArgumentError` on Linux; this is caught and printed to stdout with `puts "Signal INFO not supported"` rather than through the logger, so the failure is invisible in structured log streams. The TTIN deprecation message in the same file tells users to "use the INFO signal for backtraces", but on Linux this is silently a no-op. Fix `cli.rb` to detect when `INFO` registration fails (rescue `ArgumentError`), log the warning through the configured logger rather than `puts`, and register the same backtrace-dump handler on `USR1` as a Linux fallback. Update the TTIN deprecation message to mention the `USR1` fallback for non-BSD platforms.

### N10 – i18n fallback missing for relative time helpers on dashboard

The web helpers that display relative time strings (e.g., "3 hours ago") fall back to English when the selected locale is missing the relative_time key, but the fallback bypasses Rails I18n.fallbacks and instead hard-codes "en", ignoring any configured fallback chain.
### N11 – Non-English locale files missing translation keys added for metrics, filtering, and profiles pages

The `web/locales/en.yml` file contains all required translation keys including newer entries for the metrics page (`Metrics`, `TotalExecutionTime`, `AvgExecutionTime`, `NoJobMetricsFound`, `NoDataFound`), the filtering page (`Filter`, `AnyJobContent`), and the profiles page (`Profiles`, `Data`, `View`, `Token`, `ElapsedTime`, `Context`). However, over half of the 30 locale files under `web/locales/` are missing these newer keys, causing mixed-language rendering on the metrics, filtering, and profiles pages for non-English users. Additionally, the `.github/ISSUE_TEMPLATE/bug_report.md` template does not ask reporters to specify their locale setting, making it difficult to reproduce i18n-related dashboard bugs. Audit all 30 `web/locales/*.yml` files to add all keys present in `en.yml` that are absent in each locale (using the English string as a placeholder), and add a locale field to `.github/ISSUE_TEMPLATE/bug_report.md`.
## Medium

### M1 – Add batch job support with completion callbacks

Implement a batching mechanism that groups multiple jobs into a logical batch. The batch should track the completion status of all constituent jobs in Redis, invoke a configurable callback job when all jobs in the batch succeed, and invoke a separate callback on any failure. Integrate with the client push path, the processor completion path, and the web dashboard to display batch progress.

### M2 – Implement queue pausing with dashboard controls

Add the ability to pause and resume individual queues. The fetch strategy should skip paused queues, the web dashboard should display pause/resume buttons on the queues page, and pause state should be stored in Redis so it survives process restarts. The API module should expose pause and resume methods programmatically.

### M3 – Add job uniqueness enforcement with configurable strategies

Implement a uniqueness layer that prevents duplicate jobs from being enqueued. Support multiple strategies (until_executing, until_completed, while_executing) configured per worker class. The check should happen in client middleware using Redis locks, and lock cleanup should happen in server middleware at the appropriate lifecycle point for each strategy.

### M4 – Add per-attempt retry lifecycle hook to the retry subsystem

The retry subsystem in `job_retry.rb` provides `sidekiq_retries_exhausted` (called when all retries are exhausted) and `sidekiq_retry_in` (to customize delay), but has no hook that fires on each individual retry attempt. Add a `sidekiq_on_retry` class method to `Sidekiq::Job` in `lib/sidekiq/job.rb` that accepts a block receiving the retry count, exception, and job hash, storing it as `sidekiq_on_retry_block`. Invoke this block from `process_retry` in `job_retry.rb` before scheduling the retry delay, with the same error-handling guard used by `sidekiq_retry_in_block`. Support the wrapped-class pattern (ActiveJob) by also checking the `wrapped` class for the hook, matching the existing `sidekiq_retry_in_block` lookup. Expose the hook in the web retries view to indicate which job classes have custom retry handlers, and add a convenience accessor in `lib/sidekiq/api.rb` `Job` so callers can check `retry_handler_registered?`.

### M5 – Build a middleware profiling system

Create a profiling middleware that measures and records the wall-clock and CPU time spent in each middleware in both the client and server chains. Store per-middleware timing histograms in Redis, expose them through a new web dashboard page, and provide an API endpoint that returns the profiling data as JSON.

### M6 – Implement graceful deploy with job draining

Extend the launcher shutdown sequence to support a two-phase drain: first stop fetching new jobs, then wait for in-flight jobs to complete up to a configurable deadline before sending hard kill signals. Add a new CLI flag to trigger drain mode, surface drain status in the busy page of the web dashboard, and ensure the manager correctly coordinates thread completion.

### M7 – Add cron-like recurring job scheduler

Implement a recurring job system that reads a YAML schedule definition at startup and enqueues jobs at cron-like intervals. The scheduler should use Redis sorted sets to track next-run times, handle leader election among multiple Sidekiq processes to avoid duplicate scheduling, integrate with the existing scheduled poller infrastructure, and display upcoming and past runs in the web dashboard. Also add a `docs/recurring.md` documentation page describing the YAML schedule format and leader election behavior, add a link to it from `README.md`, and add a `Changes.md` entry under a new "Features" heading.

### M8 – Support encrypted job payloads

Add an encryption layer for job arguments that encrypts payloads on the client side before they enter Redis and decrypts them on the server side before the processor invokes the worker. Implement this as a matched pair of client and server middleware, support configurable encryption backends, handle key rotation with versioned keys, and ensure the web dashboard can display redacted argument previews.

### M9 – Implement job priority within queues

Currently jobs within a queue are FIFO. Add an optional priority field to jobs that causes higher-priority jobs to be dequeued first. This requires changing the Redis data structure from a list to a sorted set for priority-enabled queues, updating the fetch strategy to use ZPOPMIN, adapting the API module for sorted-set operations, and updating the queue page in the web dashboard to sort by priority.

### M10 – Add structured event hooks for observability

Create an event notification system that fires callbacks at key lifecycle points: job_enqueued, job_start, job_success, job_failure, job_retry, job_dead, process_start, process_quiet, process_stop. Each event should carry structured metadata. Allow user code to subscribe to events, implement a built-in subscriber that emits StatsD metrics, and wire the events into the processor, launcher, and client code paths.

### M11 – Update docs/middleware.md and upgrade guides with middleware chain documentation

The `docs/middleware.md` file documents the middleware concept but
does not include a diagram of the default middleware chain ordering
or explain the difference between client and server middleware
execution sequence. The `docs/7.0-Upgrade.md` guide does not
mention the breaking change to middleware argument passing
introduced in version 7. The `docs/internals.md` file references
the fetch-process-retry cycle but does not link to the middleware
documentation. The `.github/contributing.md` Beginner's Guide
instructs contributors to run `bundle exec rake` but does not
explain that this single command runs three tasks in sequence:
`standard` (Ruby linting), `lint:herb` (ERB linting), and `test`
(the full test suite), which matters when contributors want to run
only one stage. Update `docs/middleware.md` with an execution-order
diagram and client vs. server middleware comparison,
update `docs/7.0-Upgrade.md` with the middleware argument change,
add cross-references from `docs/internals.md` to
`docs/middleware.md`, and expand the contributing.md testing section
to document the three individual Rake tasks.

## Wide

### W1 – Extract web dashboard into standalone mountable Rack engine

Separate the entire web dashboard from the core Sidekiq library into an independent gem. The extracted engine must carry its own routes, CSRF protection, assets, views, locales, helpers, and Rack middleware. The core library should define a stable internal API that the dashboard gem consumes for queue, retry, scheduled, and dead-set data. Both gems need independent test suites, and the extraction must preserve backward compatibility for users who mount the dashboard in Rails applications.

### W2 – Implement multi-region job routing with affinity

Add cross-region job routing so that jobs can be tagged with a target region and Sidekiq processes in that region preferentially dequeue them. This requires changes to the client push path (region tagging), the fetch strategy (region-aware queue selection), Redis connection management (multi-datacenter Redis topology), the job_retry module (region-aware retry routing), the scheduled poller (cross-region scheduled sets), the web dashboard (per-region views), and the API module (region-filtered queries).

### W3 – Build a full job dependency DAG execution engine

Implement a directed acyclic graph (DAG) scheduler where jobs can declare dependencies on other jobs and only execute when their dependencies have completed. This spans the client layer (DAG construction and storage in Redis), the server layer (dependency checking in the processor before execution), the retry subsystem (propagating failures to dependent jobs), the scheduled poller (deferred dependency resolution), the web dashboard (DAG visualization), and the API (DAG status queries and manual intervention).

### W4 – Add multi-tenant isolation across all subsystems

Implement tenant-scoped isolation so that queues, retry sets, scheduled sets, dead sets, and statistics are partitioned by tenant. This requires changes to Redis key namespacing, the client push path, the fetch strategy, the processor, job_retry, scheduled poller, the API module, the web dashboard (tenant picker and scoped views), connection pooling (per-tenant Redis targets), and the middleware chain (tenant context propagation).

### W5 – Replace Redis with pluggable backend abstraction

Abstract the Redis dependency behind a storage backend interface so that Sidekiq can support alternative backends (PostgreSQL, SQLite, in-memory). Define the backend contract covering queues, sorted sets, hashes, and pub/sub. Implement the Redis backend as the default and an in-memory backend for testing. Migrate every Redis call across fetch, scheduled, job_retry, api, client, web, and redis_connection to use the abstraction. Update all tests to run against both backends.

### W6 – Implement distributed tracing integration across all components

Add first-class distributed tracing support that propagates trace context through the entire job lifecycle. Instrument the client middleware (span for enqueue), server middleware (span for execution), the fetch strategy (span for dequeue), the retry subsystem (linking retry spans to the original trace), the scheduled poller (spans for scheduled job promotion), the web dashboard (trace ID display and linking), and the error handler. Support OpenTelemetry and Datadog trace formats with a pluggable exporter interface.

### W7 – Build a comprehensive rate-limiting and throttling framework

Implement a rate-limiting system that can throttle job execution by worker class, queue, tenant, or custom key. This requires a Redis-backed token bucket or sliding window implementation, server middleware to check and enforce limits, client middleware to tag rate-limit metadata, the processor to handle limit-exceeded backoff, the web dashboard to display throttle status and configuration, the API to expose rate-limit state, the CLI to accept throttle configuration, and the retry subsystem to distinguish rate-limited retries from error retries.

### W8 – Add job versioning and schema evolution

Implement a job versioning system that allows worker classes to evolve their argument schemas over time. This requires versioned serialization in the client push path, version-aware deserialization in the processor, a migration registry for transforming old payloads to new schemas, handling of in-flight jobs during deployment (mixed versions), the retry subsystem respecting version transforms, the web dashboard displaying version information, the API filtering by version, and the testing helpers supporting version-aware assertions.

### W9 – Implement a leader-elected maintenance subsystem

Create a leader election mechanism among Sidekiq processes and build a maintenance subsystem that only the leader runs. The subsystem should handle dead-job cleanup, statistics aggregation, queue rebalancing, stale-process detection, and scheduled-set compaction. This touches the launcher (leader election via Redis), the manager (maintenance thread lifecycle), the API (leader status), the web dashboard (leader identity and maintenance status), the CLI (maintenance configuration), and the health endpoint (leader health).

### W10 – End-to-end job workflow engine with saga pattern support

Implement a saga/workflow engine on top of Sidekiq that orchestrates multi-step business processes with compensating transactions. This requires a workflow definition DSL, a workflow state machine stored in Redis, client integration for workflow step enqueuing, server middleware for step completion tracking, the retry subsystem triggering compensation on failure, the scheduled poller handling step timeouts, the web dashboard visualizing workflow state and history, the API exposing workflow management operations, and the testing helpers supporting workflow assertions.

### W11 – Overhaul web/locales/ and docs/ for comprehensive i18n and documentation coverage

The `web/locales/` directory contains 30 locale files, many of which
are missing keys that exist in `web/locales/en.yml`, causing mixed-
language dashboard rendering. The `docs/` directory contains 24
markdown files with no table of contents or index page linking them
together. The `docs/webui.md` documentation does not describe the
metrics page, profiles page, or filtering feature. Several upgrade
guides (`docs/3.0-Upgrade.md` through `docs/8.0-Upgrade.md`) contain
broken relative links to other docs pages. The `Changes.md`,
`Pro-Changes.md`, and `Ent-Changes.md` changelogs follow different
formatting conventions. The `.github/workflows/ci.yml` CI workflow
does not run a linting step for the web locale YAML files. Audit all
30 `web/locales/*.yml` files to add missing keys from `en.yml`, add a
`docs/README.md` index page linking all documentation files, update
`docs/webui.md` with metrics and filtering documentation, fix broken
links across all upgrade guides, standardize changelog formatting
across all three changelogs, and add a YAML lint step to
`.github/workflows/ci.yml`.
