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

30 tasks (10 narrow, 10 medium, 10 wide).

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

### N4: Fix broadcast channel `recv` missing messages under high contention

When many receivers call `recv()` on a `broadcast` channel simultaneously
under heavy sender throughput, some receivers occasionally skip a message
and receive the next one instead. The internal tail pointer update races
with the slot overwrite. Fix the broadcast channel to guarantee that every
active receiver sees every message sent after its subscription, even under
maximum contention.

### N5: Fix `tokio::time::sleep` drift with nested runtimes

When `tokio::time::sleep` is called inside a `block_on` nested within an
outer runtime, the sleep sometimes completes significantly later than
expected. The inner runtime's timer wheel is not being advanced because the
I/O driver tick in the inner runtime doesn't process timer events when the
outer driver owns the thread's timer state. Fix the inner runtime's timer
processing so it correctly advances its own timer wheel independently.

### N6: Fix `mpsc::Sender::send` hanging on bounded channel after receiver drop

When all `mpsc::Receiver` handles for a bounded channel are dropped while
a `Sender::send` is awaiting capacity, the send future never resolves
instead of returning a `SendError`. The closed flag is being checked before
the waiter is registered, creating a window where the close notification is
missed. Reorder the closed-flag check to occur after waiter registration.

### N7: Fix `Semaphore::acquire_many` starvation under mixed acquire sizes

When a `tokio::sync::Semaphore` has callers using both `acquire` (1 permit)
and `acquire_many` (N permits), the single-permit acquires can starve the
multi-permit acquires indefinitely. The current implementation drains the
waitlist greedily and skips any waiter whose requested count exceeds the
available permits. Change the waitlist to a strict FIFO so that a large
acquire blocks subsequent small acquires until it can be fulfilled.

### N8: Fix `AsyncRead` adapter dropping bytes on short reads

The `tokio::io::BufReader` wrapper sometimes loses data when the underlying
reader returns a short read that exactly fills the internal buffer, and the
next read call sees an empty buffer and issues a new underlying read instead
of returning the already-buffered bytes. Fix the buffer tracking so that
a full-buffer short read is correctly reported to the caller on the
subsequent poll.

### N9: Fix `signal::ctrl_c` not firing on secondary runtime

Registering `tokio::signal::ctrl_c()` on a runtime other than the first
one created in the process silently never delivers the signal. The global
signal driver registration is bound to the first runtime's I/O driver
handle and never re-registered when that runtime is dropped. Fix signal
driver initialization to support re-registration when the previous
owning runtime has been shut down.

### N10: Fix `UdpSocket::recv_buf` returning wrong source address for first datagram

After calling `UdpSocket::bind` and immediately calling `recv_buf_from`,
the returned `SocketAddr` for the very first datagram is zeroed out
instead of reflecting the actual source. The readiness state transitions
before the OS-level `recvfrom` populates the address buffer. Fix the
readiness flow to ensure the address is fully populated before returning.

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

### M5: Add cooperative budget tracking for user-spawned blocking tasks

The blocking thread pool spawns tasks via `spawn_blocking` but does not
enforce any cooperative scheduling budget, so a single blocking closure
that internally polls async code can monopolize the thread and starve peer
blocking tasks. Implement a per-task budget mechanism in the blocking pool
analogous to the async scheduler's coop budget. Track the number of
poll iterations and yield control back to the pool when the budget is
exhausted. Expose a `max_blocking_budget` configuration on the runtime
builder.

### M6: Implement graceful `broadcast` channel resize

Currently a `broadcast` channel's buffer capacity is fixed at creation and
cannot be changed. Implement a `Sender::resize(new_capacity)` method that
dynamically grows or shrinks the ring buffer while the channel is live.
Active receivers must not lose any unseen messages during the resize. Handle
the case where shrinking the buffer would discard messages that some slow
receivers haven't consumed yet by returning an error instead of dropping
data. Add backpressure signaling so senders are aware of resize-in-progress.

### M7: Add async-aware `RwLock` upgrade and downgrade

The `tokio::sync::RwLock` supports separate read and write guards but does
not allow upgrading a read guard to a write guard or downgrading in the
other direction. Implement `RwLockReadGuard::upgrade()` that atomically
transitions from a shared lock to an exclusive lock without releasing and
re-acquiring, and `RwLockWriteGuard::downgrade()` for the reverse. Handle
the case where multiple readers attempt to upgrade simultaneously by
returning an error to all but one.

### M8: Implement per-task resource usage tracking

Add the ability to track resource consumption on a per-task basis within
the runtime. Each spawned task should accumulate metrics for total poll
time, number of polls, I/O bytes read and written, and timer registrations.
Store these lightweight counters in the task header to avoid allocation.
Expose the data through a `tokio::task::LocalMetrics` handle that the
task can query, and aggregate stats available via the runtime metrics API.

### M9: Add `tokio::net::TcpStream` socket warm-up with TCP Fast Open

Implement TCP Fast Open support for `TcpStream::connect` and
`TcpListener::accept`. On connect, allow the caller to supply initial
payload data that is sent in the SYN packet. On the listener side,
configure the socket to accept TFO connections. Abstract the
platform-specific socket options (Linux `TCP_FASTOPEN`, macOS
`CONNECT_DATA_IDEMPOTENT`) behind a unified builder API. Fall back
gracefully to a normal three-way handshake on platforms or kernels that
do not support TFO.

### M10: Implement channel back-pressure propagation for `mpsc` pipelines

When multiple bounded `mpsc` channels are chained in a pipeline (producer
→ stage1 → stage2 → consumer), back-pressure from a slow consumer only
propagates one hop at a time, causing intermediate stages to buffer up to
their full capacity before slowing the upstream. Implement a cooperative
protocol where an `mpsc::Sender` can optionally subscribe to downstream
pressure signals, so the entire pipeline stalls together. Add a
`PressureAware` wrapper type and builder method to opt in per-channel.

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

### W4: Implement distributed tracing context propagation across runtime boundaries

Add first-class support for propagating distributed tracing context
(trace ID, span ID, baggage) across task spawns, blocking pool
dispatches, channel sends, and oneshot completions. Intercept
`tokio::spawn`, `spawn_blocking`, and every sync primitive's send path
to capture and restore the ambient tracing span. Support both
the `tracing` crate's span system and OpenTelemetry context
propagation. Provide a runtime builder flag to enable or disable
the propagation layer.

### W5: Add per-worker-thread NUMA affinity and memory locality

Implement NUMA-aware scheduling for the multi-threaded runtime. Each
worker thread should be pinned to a specific NUMA node, and the
work-stealing scheduler should prefer stealing from same-node workers
before cross-node ones. The I/O driver's event buffers and the timer
wheel's storage should be allocated from the local node's memory.
Expose NUMA configuration through the runtime builder and degrade
gracefully to current behavior on single-node machines or unsupported
platforms.

### W6: Implement runtime-level memory pressure response

Add a memory pressure subsystem that monitors process RSS and responds
by shedding load across the runtime. When memory pressure is detected,
the scheduler should stop accepting new spawns and return an error,
the I/O driver should reduce its event buffer sizes, the timer wheel
should compact its storage, and channel buffers should begin dropping
oldest unread messages with a notification to receivers. Expose
pressure thresholds via the runtime builder and emit pressure-level
changes as tracing events.

### W7: Add comprehensive runtime snapshotting and restore

Implement the ability to snapshot the entire state of a Tokio runtime —
including all runnable and suspended tasks, timer registrations, I/O
interest sets, channel contents, and semaphore permits — and serialize
it to a portable format. Support restoring a runtime from a snapshot
on the same or a different host. Handle I/O resource re-binding by
re-registering file descriptors with the new I/O driver. Provide a
`Runtime::snapshot()` and `Runtime::restore(snapshot)` API.

### W8: Implement zero-copy forwarding between `TcpStream` pairs

Add a high-performance forwarding path that moves data between two
`TcpStream`s without copying bytes through user-space buffers. On Linux
use `splice(2)` via the I/O driver, on other platforms use `sendfile`
or vectored I/O as a fallback. Integrate with the async I/O traits so
the forwarding respects the cooperative scheduling budget. Support
bidirectional forwarding with configurable buffer pipe sizes. Expose a
`tokio::io::splice(reader, writer)` API and a higher-level
`tokio::net::forward(stream_a, stream_b)` utility.

### W9: Add hot-reload support for runtime configuration

Allow reconfiguring a live Tokio runtime without restarting it. Support
changing the number of worker threads (growing and shrinking the thread
pool), the blocking pool size limit, the timer tick resolution, and I/O
driver parameters. Coordinate with the scheduler to drain and re-balance
tasks when threads are added or removed. Handle timer wheel restructuring
on tick-resolution changes by re-inserting all pending timers. Provide a
`Runtime::reconfigure(new_config)` method and emit configuration-change
events through tracing.

### W10: Implement cross-platform async filesystem notifications

Add a `tokio::fs::watch` API that provides async file and directory
change notifications. On Linux use `inotify` via the I/O driver, on
macOS use `FSEvents`, and on Windows use `ReadDirectoryChangesW`. Each
platform backend must register with the runtime's I/O driver for
readiness-based notification instead of spawning a background thread.
Support recursive directory watching, event debouncing with configurable
delay using the timer subsystem, and filtering by event type. Deliver
events through a bounded channel returned from the watch call.
