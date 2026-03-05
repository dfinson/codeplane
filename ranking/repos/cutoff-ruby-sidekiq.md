| Field    | Value                                              |
|----------|----------------------------------------------------|
| URL      | https://github.com/sidekiq/sidekiq                  |
| License  | LGPL-3.0                                            |
| Language | Ruby                                                |
| Scale    | Medium                                              |
| Category | Background Jobs / Concurrency                       |
| Set      | cutoff                                              |

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

### N1 – Fix retry delay calculation overflow for large retry counts

When a job exceeds 20 retries, the exponential backoff formula in job_retry.rb produces a delay value that overflows Ruby's integer-to-time conversion, causing a NoMethodError. The calculation should cap the delay at a configurable maximum rather than growing unboundedly.

### N2 – Scheduled poller skips jobs when system clock drifts backward

The scheduled set poller in scheduled.rb compares job scores against Time.now.to_f. When the system clock drifts backward (e.g., NTP correction), jobs whose scores fall in the gap between the old and new time are never dequeued until the clock catches up. The comparison should account for backward drift.

### N3 – CSRF token validation fails on dashboard form resubmission

The web dashboard's CSRF protection rejects valid form submissions when a user has two dashboard tabs open. The token rotation logic in csrf_protection.rb invalidates the first tab's token when the second tab loads, causing a 403 on any subsequent form submission from the first tab.

### N4 – Fetch strategy does not respect queue weights under low load

The BasicFetch strategy is documented to perform weighted random queue selection, but under low job volumes the implementation in fetch.rb degenerates to round-robin. The queue ordering array is shuffled without accounting for weight distribution, so queues with higher weights are not polled proportionally more often.

### N5 – Logger context lost after middleware chain exception

When a server middleware raises and the exception propagates through the chain, the structured logging context (job ID, queue name) set by the processor is cleared before the error handler runs. The error log entry lacks the job context needed for debugging.

### N6 – Client middleware not invoked for ActiveJob-enqueued jobs

Jobs enqueued through ActiveJob's Sidekiq adapter bypass the client middleware chain because the adapter calls raw Redis LPUSH instead of going through Sidekiq::Client#push. Client middleware registered for logging or enrichment never fires for these jobs.

### N7 – Dead job display truncates large payloads in web UI

The morgue view in the web dashboard truncates job argument display at 100 characters without providing an expand option. For jobs with large serialized payloads, this makes it impossible to inspect the full arguments from the dashboard.

### N8 – Redis connection pool exhaustion not reported in health check

When the Redis connection pool is fully checked out and threads are waiting, the health endpoint in the web application still returns 200 OK. It should report a degraded status when pool contention exceeds a configurable threshold so load balancers can react.

### N9 – Signal handler raises ThreadError on JRuby

The TSTP signal handler installed by cli.rb calls Thread.new inside a signal trap, which raises ThreadError on JRuby. The handler should defer thread creation by writing to a self-pipe that the main loop reads, matching the approach used for other signals.

### N10 – i18n fallback missing for relative time helpers on dashboard

The web helpers that display relative time strings (e.g., "3 hours ago") fall back to English when the selected locale is missing the relative_time key, but the fallback bypasses Rails I18n.fallbacks and instead hard-codes "en", ignoring any configured fallback chain.

## Medium

### M1 – Add batch job support with completion callbacks

Implement a batching mechanism that groups multiple jobs into a logical batch. The batch should track the completion status of all constituent jobs in Redis, invoke a configurable callback job when all jobs in the batch succeed, and invoke a separate callback on any failure. Integrate with the client push path, the processor completion path, and the web dashboard to display batch progress.

### M2 – Implement queue pausing with dashboard controls

Add the ability to pause and resume individual queues. The fetch strategy should skip paused queues, the web dashboard should display pause/resume buttons on the queues page, and pause state should be stored in Redis so it survives process restarts. The API module should expose pause and resume methods programmatically.

### M3 – Add job uniqueness enforcement with configurable strategies

Implement a uniqueness layer that prevents duplicate jobs from being enqueued. Support multiple strategies (until_executing, until_completed, while_executing) configured per worker class. The check should happen in client middleware using Redis locks, and lock cleanup should happen in server middleware at the appropriate lifecycle point for each strategy.

### M4 – Extend retry subsystem with per-job retry timing overrides

The retry system uses a global exponential backoff formula. Allow individual worker classes to specify a custom retry timing function via a sidekiq_retry_in method. The job_retry module should check for this method on the worker class before falling back to the default formula, and the web retry view should display the custom next-retry time.

### M5 – Build a middleware profiling system

Create a profiling middleware that measures and records the wall-clock and CPU time spent in each middleware in both the client and server chains. Store per-middleware timing histograms in Redis, expose them through a new web dashboard page, and provide an API endpoint that returns the profiling data as JSON.

### M6 – Implement graceful deploy with job draining

Extend the launcher shutdown sequence to support a two-phase drain: first stop fetching new jobs, then wait for in-flight jobs to complete up to a configurable deadline before sending hard kill signals. Add a new CLI flag to trigger drain mode, surface drain status in the busy page of the web dashboard, and ensure the manager correctly coordinates thread completion.

### M7 – Add cron-like recurring job scheduler

Implement a recurring job system that reads a YAML schedule definition at startup and enqueues jobs at cron-like intervals. The scheduler should use Redis sorted sets to track next-run times, handle leader election among multiple Sidekiq processes to avoid duplicate scheduling, integrate with the existing scheduled poller infrastructure, and display upcoming and past runs in the web dashboard.

### M8 – Support encrypted job payloads

Add an encryption layer for job arguments that encrypts payloads on the client side before they enter Redis and decrypts them on the server side before the processor invokes the worker. Implement this as a matched pair of client and server middleware, support configurable encryption backends, handle key rotation with versioned keys, and ensure the web dashboard can display redacted argument previews.

### M9 – Implement job priority within queues

Currently jobs within a queue are FIFO. Add an optional priority field to jobs that causes higher-priority jobs to be dequeued first. This requires changing the Redis data structure from a list to a sorted set for priority-enabled queues, updating the fetch strategy to use ZPOPMIN, adapting the API module for sorted-set operations, and updating the queue page in the web dashboard to sort by priority.

### M10 – Add structured event hooks for observability

Create an event notification system that fires callbacks at key lifecycle points: job_enqueued, job_start, job_success, job_failure, job_retry, job_dead, process_start, process_quiet, process_stop. Each event should carry structured metadata. Allow user code to subscribe to events, implement a built-in subscriber that emits StatsD metrics, and wire the events into the processor, launcher, and client code paths.

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
