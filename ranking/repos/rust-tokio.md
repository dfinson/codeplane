# tokio-rs/tokio

| Field | Value |
|-------|-------|
| **URL** | https://github.com/tokio-rs/tokio |
| **License** | MIT |
| **Language** | Rust |
| **Scale** | Large (multi-team project) |
| **Category** | Async runtime |

## Why this repo

- **No single developer knows it all**: Async runtime (task scheduler, thread
  pool, work stealing), I/O driver (epoll/kqueue/IOCP), networking (TCP, UDP,
  Unix sockets), synchronization primitives (Mutex, RwLock, Semaphore, channels),
  timers, signal handling, process spawning, filesystem operations — each a
  deep subsystem with platform-specific complexity.
- **Well-structured**: Main `tokio/` crate with clear module split. Runtime
  internals (`runtime/`), I/O (`io/`), networking (`net/`), sync (`sync/`),
  time (`time/`), signal (`signal/`), process (`process/`) each have their
  own sub-module hierarchies.
- **Rich history**: 7K+ commits, active multi-contributor development. PRs
  cover performance optimization, platform support, and API design. Strong
  review culture.
- **Permissive**: MIT license.

## Structure overview

```
tokio/src/
├── runtime/             # Async runtime engine
│   ├── scheduler/       # Work-stealing task scheduler
│   │   ├── multi_thread/ # Multi-threaded scheduler
│   │   └── current_thread/ # Single-threaded scheduler
│   ├── task/            # Task representation and waking
│   ├── io/              # I/O driver (epoll/kqueue/IOCP)
│   └── blocking/        # Blocking thread pool
├── net/                 # Networking
│   ├── tcp/             # TcpListener, TcpStream
│   ├── udp.rs           # UDP socket
│   └── unix/            # Unix domain sockets
├── io/                  # Async I/O traits and utilities
│   ├── read.rs          # AsyncRead
│   ├── write.rs         # AsyncWrite
│   └── util/            # BufReader, BufWriter, copy, etc.
├── sync/                # Synchronization primitives
│   ├── mutex.rs         # Async Mutex
│   ├── rwlock/          # Async RwLock
│   ├── semaphore.rs     # Semaphore
│   ├── mpsc/            # Multi-producer channel
│   ├── oneshot.rs       # One-shot channel
│   ├── broadcast.rs     # Broadcast channel
│   └── watch.rs         # Watch channel
├── time/                # Timers and delays
├── signal/              # OS signal handling
├── process/             # Child process spawning
└── fs/                  # Async filesystem operations
```

## Scale indicators

- ~400 Rust source files
- ~100K+ lines of code
- Deep module hierarchies (4-5 levels in runtime)
- Platform-specific code paths (#[cfg] blocks)

---

## Tasks

10 tasks (3 narrow, 4 medium, 3 wide) for the Rust async runtime.

## Narrow

### N1: Fix `tokio::sync::watch` spurious wake-up after sender drop

When the `watch::Sender` is dropped, all `watch::Receiver`s are woken
up even if the value hasn't changed since their last read. This causes
unnecessary churn in consumers that use `changed().await` in a loop.
Fix the watch channel to only wake receivers on actual value change,
and provide a separate `closed()` future for detecting sender drop.

### N2: Add `timeout_at` for `Instant`-based deadlines

`tokio::time::timeout` accepts a `Duration`, but there's no ergonomic
way to set a deadline at a specific `Instant`. Add `tokio::time::timeout_at`
that combines an `Instant` deadline with a future. This is a thin wrapper
but important for composing timeouts in protocol implementations where
the deadline is computed once and shared across multiple operations.

### N3: Fix `TcpListener::accept` not respecting runtime shutdown

When `tokio::runtime::Runtime::shutdown_timeout` is called, pending
`TcpListener::accept` calls block the shutdown until the timeout
expires instead of returning immediately with an error. Fix the accept
implementation to poll the runtime shutdown signal and return an error
when the runtime is shutting down.

## Medium

### M1: Implement priority-based task scheduling

Add task priority support to the multi-threaded scheduler. Tasks spawned
with `tokio::spawn_with_priority(priority, future)` should be scheduled
according to their priority level (High, Normal, Low). High-priority
tasks should be dequeued before normal and low. Implement this without
degrading the performance of the normal-priority fast path. Add
metrics for per-priority queue depths.

### M2: Add structured concurrency with `TaskGroup`

Implement a `tokio::task::TaskGroup` that manages a set of spawned
tasks as a unit. When the group is dropped or cancelled, all tasks in
the group are cancelled. The group should propagate panics from child
tasks to the parent. Provide `group.spawn()` and `group.join_all()`
methods. Support nested groups.

### M3: Implement async `Read`/`Write` for Unix domain sockets with fd passing

The current `UnixStream` supports async read/write but not file
descriptor passing (`sendmsg`/`recvmsg` with `SCM_RIGHTS`). Implement
`send_fd()` and `recv_fd()` methods on `UnixStream` that allow passing
file descriptors between processes. This requires interfacing with
the I/O driver for readiness notification on ancillary data.

### M4: Add runtime metrics export via `tracing`

Implement automatic export of runtime metrics (task count, task poll
duration histogram, I/O driver event count, timer wheel size, thread
pool utilization) through the `tracing` subscriber system. Emit
metrics as `tracing` events with structured fields so any tracing
subscriber (e.g., tracing-opentelemetry) can collect them. Add
configurable emission intervals.

## Wide

### W1: Implement io_uring backend for Linux

Add an io_uring-based I/O driver as an alternative to the epoll driver
on Linux. This affects the I/O driver, file operations, networking
(TCP accept, read, write), and timer implementation. The io_uring
backend should be selectable at runtime via a builder option. Implement
buffer ring support for zero-copy reads. Maintain the existing epoll
backend as the default.

### W2: Add WASM/WASI runtime support

Port the Tokio runtime to work in WebAssembly/WASI environments.
Replace the multi-threaded scheduler with a single-threaded variant
(no threads in WASM), adapt the I/O driver to use WASI's async I/O
primitives, remove signal handling, and use WASI clocks for timers.
Provide a feature flag (`wasm`) that configures the appropriate
backend. Keep the API surface identical where possible.

### W3: Implement cross-runtime task migration

Add support for migrating a running task from one Tokio runtime to
another. This enables load balancing across multiple runtime instances
in a process. Implement a migration protocol that checkpoints the
task's state (waker, resources), transfers it to the target runtime,
and resumes execution. Handle I/O resource ownership transfer and
timer re-registration. Add a `Runtime::migrate_task()` API.
