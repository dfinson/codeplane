# celery/celery

| Field | Value |
|-------|-------|
| **URL** | https://github.com/celery/celery |
| **License** | BSD-3-Clause |
| **Language** | Python |
| **Scale** | Medium-large |
| **Category** | Distributed task queue |
| **Set** | eval |
| **Commit** | `92c2606aab31a521b3e006e53ca729f2e586d1b6` |

## Why this repo

- **Multi-subsystem**: Worker processes, result backends, concurrency models, periodic scheduling, workflow primitives (canvas), event system
- **Well-structured**: Clear separation between app/, worker/, backends/, concurrency/, beat/, canvas, events/
- **Rich history**: 20K+ commits, widely deployed production system

## Structure overview

```
celery/
├── app/                 # Celery application, configuration
├── worker/              # Consumer, autoscaler, heartbeat, state
├── backends/            # Redis, RPC, database, Elasticsearch
├── concurrency/         # prefork, eventlet, gevent, thread
├── beat/                # Periodic task scheduler
├── canvas.py            # Workflow: chain, group, chord, chunks
├── result.py            # Async result handling
├── events/              # Real-time event system
├── security/            # Serializer signing
├── bin/                 # CLI commands
└── utils/               # Utilities
```

## Scale indicators

- ~300 Python source files
- ~60K lines of code
- Multiple concurrency backends and result backends
- Cross-cutting event system and configuration

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix task retry not preserving countdown when using `autoretry_for`

When a task configured with `autoretry_for` retries due to an expected exception, the `retry_backoff` countdown is not applied. The retry fires immediately instead of using exponential backoff.

### N2: Fix `chord` callback not called when group has zero tasks

When a `chord` is created with an empty group, the callback is never invoked. The chord logic waits for group completion signals that never arrive.

### N3: Add `task_received_timeout` configuration option

When a worker receives a task but doesn't start executing it within a reasonable time due to prefetch, the task publisher has no visibility. Add a configurable timeout that triggers a warning event.

### N4: Fix `AsyncResult.get()` hanging when result backend is unreachable

When the result backend goes down, `AsyncResult.get(timeout=10)` hangs indefinitely instead of raising after the timeout. The backend connection retry loop ignores the caller's timeout.

### N5: Fix `task_acks_late` not working with `worker_prefetch_multiplier=1`

When both settings are enabled, task acknowledgment happens before the task completes, defeating the purpose of late acks. The prefetch logic sends the ack early to fetch the next task.

### N6: Add `on_retry` signal for task retry events

Tasks emit `task_prerun` and `task_postrun` signals but there's no signal for retry events. Add an `on_retry` signal that fires when a task is about to be retried, providing the exception, retry count, and countdown.

### N7: Fix `group.apply_async()` not propagating `queue` argument to all tasks

When calling `group(tasks).apply_async(queue='high')`, only the first task in the group is routed to the specified queue. Subsequent tasks use the default queue.

### N8: Fix `beat` scheduler drift accumulating over long run times

The periodic beat scheduler accumulates timing drift when the system clock is adjusted or when task scheduling takes longer than the interval. After running for days, schedules drift by minutes.

### N9: Add `task_id` to structured log records automatically

Task log messages from `self.logger` don't include the task ID in structured log output. Add automatic task_id injection into log records during task execution.

### N10: Fix `revoke` not working for tasks in ETA queue

When revoking a task submitted with an `eta`, the revocation is ignored because the task hasn't been delivered to a worker yet. The revocation signal only reaches active workers.

### N11: Update configuration documentation for the `task_received_timeout` setting

The `docs/userguide/configuration.rst` reference table is missing the `task_received_timeout` option. Add the setting with type, default value, and description to the configuration reference. Update `docs/userguide/tasks.rst` to cross-reference the timeout behavior and add a usage example in the task execution section.

## Medium

### M1: Implement dead letter queue for permanently failed tasks

When a task exhausts all retries and fails permanently, it's silently dropped. Add a dead letter queue that captures permanently failed tasks with their exception details, arguments, and retry history. Update `docs/userguide/tasks.rst` with dead letter queue usage documentation and add the new configuration options to `docs/userguide/configuration.rst`.

### M2: Add task priority support across all broker backends

Task priority is supported for RabbitMQ but not for Redis or SQS brokers. Implement priority queue support for Redis using sorted sets and SQS using message group priorities.

### M3: Implement task execution timeline visualization

Add an event-based timeline that shows task execution across workers: which tasks ran on which worker, execution duration, queue time, and retry patterns. Expose via the events API.

### M4: Add batch task processing support

Implement a `@app.batch_task` decorator that groups multiple invocations of the same task into a single execution. The worker collects batch_size invocations or waits batch_timeout then calls the task with a list of argument sets.

### M5: Implement result backend connection pooling

Each `AsyncResult.get()` call creates a new connection to the result backend. Implement connection pooling with configurable pool size, connection recycling, and health checking for Redis, database, and Elasticsearch backends.

### M6: Add structured task dependency tracking

Implement explicit task dependency declarations beyond canvas primitives. `@app.task(depends_on=['task_a', 'task_b'])` ensures the task only executes after its dependencies complete. Detect and reject circular dependencies.

### M7: Implement gradual worker shutdown with task draining

When a worker receives SIGTERM, it should stop accepting new tasks, wait for currently executing tasks to complete with configurable timeout, and gracefully disconnect from the broker.

### M8: Add per-task resource limits

Implement configurable resource limits per task type: memory limit, CPU time limit, and disk I/O rate limiting. Track resource usage per task execution and expose via events.

### M9: Implement task versioning for safe deployment

Add task version tracking so when a new version of a task handler is deployed, tasks serialized with the old version's arguments are still processed correctly. Add argument migration functions between versions.

### M10: Add distributed rate limiting for tasks

Implement rate limiting coordinated across all workers using Redis. Support per-task-type rate limits, sliding window algorithm, and configurable burst allowance. Return tasks to the queue when rate limit is exceeded.

### M11: Add Helm chart support for Celery flower monitoring deployment

Add a `flower` deployment template in `helm-chart/templates/` for the Celery monitoring dashboard. Update `helm-chart/values.yaml` with flower configuration options (replica count, resource limits, broker URL). Bump the chart version in `helm-chart/Chart.yaml` and update `helm-chart/README.rst` with deployment instructions. Update `docker/docker-compose.yml` to include a flower service for local development.

## Wide

### W1: Implement multi-broker support

Add the ability to consume from and publish to multiple message brokers simultaneously. Route tasks to specific brokers based on queue declarations. Changes span the transport layer, worker consumer, task publishing, and configuration.

### W2: Add end-to-end task tracing with OpenTelemetry

Implement distributed tracing across the task lifecycle: publish → broker transit → worker execution → result storage. Propagate trace context through task headers. Support nested task calls. Changes span app publishing, worker execution, canvas primitives, and result backends.

### W3: Implement workflow engine with conditional branching

Extend the canvas system with conditional workflows: if_task that branches based on a previous task's result, while_task for loops, and try_task for error handling within workflows. Add workflow state persistence and resumption after worker restart.

### W4: Add multi-tenant task isolation

Implement tenant-aware task processing: each tenant's tasks run in isolated worker pools with independent concurrency limits, rate limits, and backend connections. Changes span the worker, task routing, backends, events, and configuration.

### W5: Implement task profiling and performance analytics

Add automatic profiling for task execution: CPU time, wall time, memory allocation, I/O wait, database query count, and external HTTP call duration. Store profiling data and add an analytics API for querying aggregated performance.

### W6: Add comprehensive testing framework for Celery tasks

Implement `celery.testing` with synchronous task execution mode, in-memory broker and result backend, task call assertion helpers, workflow testing, and time travel for testing scheduled tasks. Changes span app, worker, backends, beat, and add a testing module. Add testing framework usage guide to `CONTRIBUTING.rst`, update `requirements/test.txt` with new test dependencies, and add a CI job to `.github/workflows/python-package.yml` for framework validation.

### W7: Implement automatic task retry with circuit breaker

Add a circuit breaker pattern that stops retrying tasks when a downstream service is consistently failing. Track failure rates per task type. When open, route tasks to a fallback handler or dead letter queue. Changes span task execution, retry logic, events, and monitoring.

### W8: Add real-time worker fleet management API

Implement an API for managing the worker fleet: scale workers up/down programmatically, reassign queues at runtime, drain specific workers, and redistribute tasks when workers go offline. Changes span worker remote control, events, monitoring, and add a fleet management module.

### W9: Implement persistent task scheduling with database backend

Replace the file-based beat schedule store with a database-backed scheduler supporting dynamic schedule creation via API, schedule history, timezone-aware scheduling, and HA with leader election. Changes span beat, configuration, and add a database schedule store.

### W10: Add migration system for broker queue management

Implement a queue migration framework for moving tasks between brokers without message loss. Support draining, replaying, format transformation, and verification. Changes span transport layer, CLI, worker consumer, and add a migration module.

### W11: Overhaul CI pipeline and contributor docs for multi-broker testing

Update `.github/workflows/integration-tests.yml` to add a broker matrix testing Redis, RabbitMQ, and SQS backends. Update `CONTRIBUTING.rst` with local multi-broker setup instructions using Docker. Add `requirements/test-multi-broker.txt` with broker client dependencies. Update `docker/docker-compose.yml` with additional broker services. Add feature entries to `Changelog.rst` and update `docs/userguide/` guides for multi-broker configuration, routing, and troubleshooting.
