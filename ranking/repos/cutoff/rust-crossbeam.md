# crossbeam-rs/crossbeam

| Field | Value |
|-------|-------|
| **URL** | https://github.com/crossbeam-rs/crossbeam |
| **License** | MIT / Apache-2.0 |
| **Language** | Rust |
| **Scale** | Large |
| **Category** | Concurrent data structures |
| **Set** | Cutoff |
| **Commit** | `03919fedb43cdbd0866aee0c77e0d6df8976b12f` |

## Why this repo

- **Well-structured**: Workspace of six focused crates — `crossbeam-channel`
  (multi-producer multi-consumer channels with bounded, unbounded, zero-
  capacity, tick, and after flavors), `crossbeam-deque` (work-stealing
  deques), `crossbeam-epoch` (epoch-based lock-free memory reclamation),
  `crossbeam-queue` (lock-free concurrent queues), `crossbeam-skiplist`
  (lock-free concurrent skip list map and set), and `crossbeam-utils`
  (backoff, scoped threads, `AtomicCell`, `ShardedLock`, `WaitGroup`,
  `CachePadded`, `Parker`). Each crate is self-contained with clear
  internal module boundaries.
- **Rich history**: 7.5K+ stars, 1.5K+ commits. The foundational
  concurrency toolkit for the Rust ecosystem, depended on by tokio,
  rayon, and thousands of crates. Issues span memory ordering edge
  cases, epoch reclamation latency, channel fairness, and platform-
  specific atomics.
- **Permissive**: Dual-licensed MIT / Apache-2.0.

## Structure overview

```
crossbeam/
├── src/lib.rs                               # Umbrella re-exports from all sub-crates
├── Cargo.toml                               # Workspace manifest with feature flags
├── crossbeam-channel/
│   └── src/
│       ├── lib.rs                           # Public API — bounded, unbounded, select!, Sender, Receiver
│       ├── channel.rs                       # Channel constructors, Sender/Receiver structs, SenderFlavor/ReceiverFlavor enums
│       ├── context.rs                       # Thread context for blocking select operations
│       ├── counter.rs                       # Reference-counted channel handle management
│       ├── err.rs                           # Error types — SendError, RecvError, TrySendError, TryRecvError, SelectTimeoutError
│       ├── select.rs                        # Select interface — Token, Operation, Selected, SelectHandle trait
│       ├── select_macro.rs                  # select! macro internal implementation
│       ├── utils.rs                         # Internal utilities — Spinlock, CachePadded usage
│       ├── waker.rs                         # Waker registry for parking/unparking blocked threads
│       └── flavors/
│           ├── mod.rs                       # Flavor module declarations
│           ├── array.rs                     # Bounded channel (pre-allocated ring buffer)
│           ├── list.rs                      # Unbounded channel (linked list of blocks)
│           ├── zero.rs                      # Zero-capacity rendezvous channel
│           ├── at.rs                        # after() — single-shot timer channel
│           ├── tick.rs                      # tick() — periodic timer channel
│           └── never.rs                     # never() — channel that never delivers
├── crossbeam-deque/
│   └── src/
│       ├── lib.rs                           # Public API — Worker, Stealer, Injector
│       ├── deque.rs                         # Work-stealing deque (Chase-Lev) — Worker (LIFO/FIFO), Stealer, Injector
│       └── alloc_helper.rs                  # Allocation helpers for no_std
├── crossbeam-epoch/
│   └── src/
│       ├── lib.rs                           # Public API — pin, Atomic, Owned, Shared, Guard, Collector
│       ├── atomic.rs                        # Atomic<T> — tagged atomic pointer with compare_exchange, load, store
│       ├── collector.rs                     # Collector — thread registration, epoch advancement
│       ├── default.rs                       # Global default collector singleton
│       ├── deferred.rs                      # Deferred — trait-object wrapper for deferred destruction
│       ├── epoch.rs                         # Epoch counter — AtomicEpoch with Relaxed/Pinned/Locked states
│       ├── guard.rs                         # Guard — epoch pin, defer_destroy, flush, repin
│       ├── internal.rs                      # Internal — thread-local epoch state, Global bag list, garbage collection
│       └── sync/
│           ├── mod.rs                       # Sync module declarations
│           ├── list.rs                      # Lock-free linked list (IterList) for internal use
│           └── queue.rs                     # Lock-free queue for garbage bags
├── crossbeam-queue/
│   └── src/
│       ├── lib.rs                           # Public API — ArrayQueue, SegQueue
│       ├── array_queue.rs                   # ArrayQueue — bounded lock-free MPMC queue (ring buffer)
│       └── seg_queue.rs                     # SegQueue — unbounded lock-free MPMC queue (segmented linked list)
├── crossbeam-skiplist/
│   └── src/
│       ├── lib.rs                           # Public API — SkipMap, SkipSet
│       ├── base.rs                          # SkipList base — Node, Tower, iterator, insert, remove, epoch-guarded
│       ├── map.rs                           # SkipMap — concurrent ordered map (Entry, get, insert, remove, range)
│       ├── set.rs                           # SkipSet — concurrent ordered set backed by SkipMap
│       ├── comparator.rs                    # Comparator trait for custom key ordering
│       └── equivalent.rs                    # Equivalent trait for key lookup by reference
├── crossbeam-utils/
│   └── src/
│       ├── lib.rs                           # Public API — thread::scope, Backoff, CachePadded, etc.
│       ├── backoff.rs                       # Backoff — adaptive spinning strategy (spin → yield → park)
│       ├── cache_padded.rs                  # CachePadded<T> — prevent false sharing with cache-line alignment
│       ├── thread.rs                        # Scoped threads — scope(), ScopedJoinHandle, ScopedThreadBuilder
│       ├── atomic/
│       │   ├── mod.rs                       # Atomic module re-exports
│       │   ├── atomic_cell.rs              # AtomicCell<T> — lock-free cell for arbitrary T
│       │   ├── consume.rs                   # AtomicConsume trait — consume memory ordering
│       │   ├── seq_lock.rs                  # SeqLock — sequence-number based lock for AtomicCell fallback
│       │   └── seq_lock_wide.rs             # SeqLock variant for types wider than pointer size
│       └── sync/
│           ├── mod.rs                       # Sync module re-exports
│           ├── once_lock.rs                 # OnceLock — one-time initialization primitive
│           ├── parker.rs                    # Parker / Unparker — thread parking primitive
│           ├── sharded_lock.rs              # ShardedLock<T> — reader-scalable RwLock with per-CPU shards
│           └── wait_group.rs                # WaitGroup — synchronization barrier for thread groups
└── tests/
    └── subcrates.rs                         # Integration tests verifying sub-crate re-exports
```

## Scale indicators

- ~6 crates, ~50 Rust source files
- ~25,500 lines of library code (channel ~8K, epoch ~5K, utils ~4.2K,
  skiplist ~4.4K, deque ~2.4K, queue ~1.5K)
- Workspace structure with per-crate Cargo.toml and feature flags
- Dependencies: minimal (only `cfg-if` and intra-workspace crates)

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix ArrayQueue::push not waking parked consumers after a successful push

In `crossbeam-queue/src/array_queue.rs`, after a successful `push`
the implementation does not signal any mechanism to wake consumers
that may be spin-waiting via `Backoff`. When the queue transitions
from empty to non-empty, consumers using `Backoff::snooze()` in a
polling loop can experience unnecessary latency. Fix `push` to
include a fence or wake mechanism after successfully enqueuing to
the previously-empty queue.

### N2: Add missing capacity() method to SegQueue

`crossbeam-queue/src/array_queue.rs` exposes `pub fn capacity(&self) -> usize`
for the fixed-capacity `ArrayQueue`, but `crossbeam-queue/src/seg_queue.rs`
has no `capacity()` method at all. Users who write generic code over
both queue types cannot uniformly query capacity, and the public API
is asymmetric. Add `pub fn capacity(&self) -> Option<usize>` to
`SegQueue` that always returns `None` to indicate it is unbounded.
Update the module-level documentation for `SegQueue` in
`crossbeam-queue/src/seg_queue.rs` to explicitly state it is
unbounded. Also update `crossbeam-queue/src/lib.rs` to ensure the
new method is visible in the public API docs.

### N3: Implement Clone for Backoff starting from step 0

In `crossbeam-utils/src/backoff.rs`, the `Backoff` struct does not
implement `Clone`. Structs that contain a `Backoff` field cannot
derive or implement `Clone` without a manual workaround, forcing
callers to store a `Cell<u32>` step counter separately instead of
using `Backoff` directly. Implement `Clone` for `Backoff` so that
the clone always creates a fresh `Backoff` at step 0 (equivalent to
`Backoff::new()`), rather than copying the current step value.
Copying an exhausted backoff's step count would cause the clone to
immediately report `is_completed() == true`, which is surprising.
Add a doc-comment on the `Clone` impl explaining this semantics, and
add a doc-test demonstrating that a cloned `Backoff` starts from
step 0 regardless of the original's state.

### N4: Fix ShardedLock::is_poisoned checking only the first shard

In `crossbeam-utils/src/sync/sharded_lock.rs`,
`ShardedLock::is_poisoned()` only inspects
`self.shards[0].lock.is_poisoned()`. Because a write guard acquires
all shards simultaneously, a panic while holding the write guard
poisons every shard. If shard 0 was not poisoned (e.g., the panic
occurred after locking later shards), `is_poisoned()` returns
`false` even though one or more other shards are poisoned. This
silently hides poisoning from callers checking lock integrity. Fix
`is_poisoned()` to iterate all shards with `.any()` and return
`true` if any shard reports `is_poisoned()`.

### N5: Document Guard::flush epoch-advancement limitation and add flush_and_repin

In `crossbeam-epoch/src/guard.rs`, `Guard::flush()` internally calls
`Global::collect()` which calls `try_advance()`, but the public
documentation does not explain when deferred destructors are actually
executed. Critically, if a single thread repeatedly calls `flush()`
while holding the same `Guard`, the global epoch can only advance by
at most one step relative to the guard's pinned epoch — subsequent
`try_advance()` calls fail because the live guard is still pinned at
its original epoch, blocking further advancement. Deferred work
accumulates without being reclaimed. Fix this by: (1) updating the
`Guard::flush()` documentation in `guard.rs` to explain this
limitation and direct users to drop and re-pin or call
`guard.repin()` between flushes; (2) adding a
`Guard::flush_and_repin(&mut self)` convenience method that calls
`flush()` then `repin()` in sequence, allowing the epoch to advance
multiple steps when called repeatedly.

### N6: Fix bounded channel try_send returning Full when the receiver has disconnected

In `crossbeam-channel/src/flavors/array.rs`, `start_send` loops to
find a send slot and checks for disconnect at the top of each
iteration via `tail & self.mark_bit != 0`. In the "channel is full"
code path, the function issues a `SeqCst` fence (which makes any
concurrent `disconnect()` store visible) and then checks whether the
head has advanced enough to confirm fullness — but does not re-read
`tail` after the fence to check the disconnect mark bit. If
`disconnect()` was called just before the fence, its effect is now
visible but not observed, and `start_send` returns `false` (signaling
Full), causing `try_send` to return `TrySendError::Full` instead of
`TrySendError::Disconnected`. Fix the full-check path in `start_send`
to reload `tail` after the `SeqCst` fence and re-check
`tail & self.mark_bit` before returning `false`.

### N7: Fix SkipMap::get_or_insert not linearizable when called concurrently with remove

In `crossbeam-skiplist/src/base.rs`, `get_or_insert` first searches
for the key and then inserts if not found. Between the search and
insert, a concurrent `remove` can delete the found entry, causing
`get_or_insert` to return a reference to a logically-removed node
that will be reclaimed. Fix the insert path to re-validate the node's
removal status under the epoch guard before returning.

### N8: Refactor WaitGroup::wait to eliminate unsafe block

In `crossbeam-utils/src/sync/wait_group.rs`, `WaitGroup::wait()`
takes `self` by value and uses an `unsafe` block with
`ManuallyDrop` and `core::ptr::read` to extract `inner` while
preventing `Drop` from running a second reference-count decrement.
This unsafe code can be replaced with a safe idiom: clone the
`Arc<Inner>` before consuming `self`, then call `mem::forget(self)`
to prevent the `Drop` impl from running, then proceed with the
decremented count using the cloned `Arc`. Refactor
`WaitGroup::wait()` to eliminate the `unsafe` block using
`Arc::clone` and `core::mem::forget`, preserving identical
observable semantics while removing the manual-drop bypass.
Add a comment explaining why `mem::forget` is used instead of
calling `drop`.

### N9: Fix AtomicCell<f64>::compare_exchange using bitwise comparison for NaN

In `crossbeam-utils/src/atomic/atomic_cell.rs`, `compare_exchange`
for floating-point types uses bitwise comparison via `transmute` to
integer types. This means `NaN != NaN` bitwise comparison succeeds
(two NaN values with different bit patterns fail to match), which
is inconsistent with the documented behavior. Fix the comparison to
use canonical NaN representation or document the bitwise semantics
explicitly and add a `compare_exchange_bitwise` variant.

### N10: Fix tick channel not accounting for system clock adjustments

The `tick` channel flavor in `crossbeam-channel/src/flavors/tick.rs`
uses `Instant::now()` for computing the next delivery time. If the
system monotonic clock is adjusted (e.g., NTP step on some platforms),
the tick interval can drift or deliver a burst of messages. Fix the
tick delivery logic to use duration-based arithmetic (elapsed since
creation) rather than absolute `Instant` comparison, preventing
burst delivery after clock adjustments.

## Medium

### M1: Implement a priority channel with ordered message delivery

Add a `priority()` constructor to `crossbeam-channel` that creates a
channel delivering messages in priority order (highest first). Requires
a new `priority.rs` flavor in `crossbeam-channel/src/flavors/` with
a concurrent binary heap, integration with `channel.rs` for the
`SenderFlavor`/`ReceiverFlavor` enums, `select.rs` for select support,
`waker.rs` for parking/unparking on the priority queue, and `lib.rs`
for the public API.

### M2: Add try_recv_timeout to bounded and unbounded channels

Implement `Receiver::recv_timeout_try(Duration)` that attempts to
receive without parking (spinning only) for up to the given duration.
Requires changes to `crossbeam-channel/src/channel.rs` for the new
method signature, per-flavor implementations in `array.rs` and
`list.rs` using `Backoff` with a deadline, `select.rs` integration
for timed try operations, error type additions in `err.rs`, and
public API in `lib.rs`.

### M3: Implement Inspector API for queue and channel diagnostics

Add `.inspector()` to `ArrayQueue`, `SegQueue`, `Sender`, and
`Receiver` that returns a snapshot of internal state: length, capacity,
sender/receiver count, and approximate memory usage. Requires
diagnostic methods on each data structure, a shared `Inspector` trait
in `crossbeam-queue/src/lib.rs`, per-flavor inspector implementations
in `crossbeam-channel/src/flavors/`, memory estimation logic, and
public API surface changes.

### M4: Implement Stealer::steal_batch_into_injector for global task redistribution

In `crossbeam-deque/src/deque.rs`, `Stealer` can steal tasks into
another `Worker` via `steal_batch` and `steal_batch_with_limit`, but
there is no method to steal tasks directly into an `Injector` (the
global work queue). When a worker thread is shutting down, it must
redistribute its unprocessed tasks to the global queue; currently
this requires a manual loop calling `Injector::push` per item.
Implement `Stealer::steal_batch_into_injector(dest: &Injector<T>, limit: usize) -> Steal<()>`
that steals up to `limit` tasks from the victim worker and pushes
them into `dest` in FIFO order. Requires changes to `deque.rs` for
the new method using the same buffer pointer arithmetic and epoch-
pinned traversal as the existing `steal_batch_with_limit`, with
`Injector::push` calls in the transfer loop instead of worker buffer
writes. Update `crossbeam-deque/src/lib.rs` for the public API and
add doc-tests showing graceful worker shutdown.

### M5: Implement epoch-guarded concurrent hash map

Add a `HashMap` to `crossbeam-skiplist` (or a new `crossbeam-hashmap`
crate) that uses epoch-based reclamation for lock-free operations.
Requires a hash table with bucket arrays in a new source file,
epoch-guarded insert/remove/get operations using `crossbeam-epoch`,
resizing logic with atomic bucket migration, iterator support with
guard pinning, and `Cargo.toml` manifest for the new crate or module.
Also update the workspace `Cargo.toml` to add the new crate to the
`[workspace]` members list, add a re-export in the umbrella crate's
`src/lib.rs`, and update `README.md` to document the new data
structure in the feature overview table.

### M6: Add Spurious variant to UnparkReason for distinguishing spurious wakeups

In `crossbeam-utils/src/sync/parker.rs`, `Parker::park_timeout` and
`Parker::park_deadline` return `UnparkReason` with two variants:
`Unparked` (woken by an `unpark` call) and `Timeout` (timed out).
However, the underlying `condvar.wait_timeout` can return spuriously
without an `unpark` call or actual timeout expiry. The current code
maps all non-notification wakeups to `UnparkReason::Timeout`,
indistinguishably conflating true timeouts with spurious wakeups.
Callers that act on `Timeout` results cannot distinguish a real
timeout from a spurious wakeup. Add a `Spurious` variant to
`UnparkReason`, update the `Inner::park()` state machine in `parker.rs`
to detect and return `UnparkReason::Spurious` when the condvar
returns without notification and before the deadline has passed,
update all `match` sites on `UnparkReason` within `parker.rs` and
the surrounding `sync/` module, update `crossbeam-utils/src/sync/mod.rs`
and `crossbeam-utils/src/lib.rs` to re-export the updated enum, and
add tests and documentation for the new variant.

### M7: Implement bounded SkipMap with eviction policy

Add `SkipMap::with_capacity(cap)` that limits the map to `cap` entries
and evicts the lowest-priority (smallest key) entry on overflow.
Requires capacity tracking in `crossbeam-skiplist/src/base.rs`,
atomic length maintenance, eviction logic in the insert path with
epoch-safe removal, an `EvictionPolicy` trait for custom eviction
(smallest key, LRU), and integration with `map.rs` and `set.rs`.

### M8: Add select! macro support for SkipMap operations

Extend the `select!` macro in `crossbeam-channel` to also wait on
`SkipMap` insertion/removal events via a notification channel. Requires
a `Notifier` adapter in `crossbeam-skiplist/src/map.rs` that wraps
a crossbeam channel, integration with `select_macro.rs` for custom
operation types, `SelectHandle` implementation for map events, and
documentation with examples.

### M9: Implement thread-local caching for SegQueue to reduce contention

Add a per-thread batch buffer to `SegQueue` that accumulates pushes
and flushes to the shared queue in batches, reducing CAS contention.
Requires a thread-local buffer in `crossbeam-queue/src/seg_queue.rs`,
flush-on-full and flush-on-pop logic, epoch-guarded buffer reclamation,
configurable batch size, and benchmark comparisons.

### M10: Add Collector statistics for epoch-based reclamation monitoring

Implement `Collector::stats() -> EpochStats` that returns current
epoch, number of pinned threads, pending garbage bags, and total
reclaimed objects. Requires atomic counters in
`crossbeam-epoch/src/internal.rs`, counter updates in the pin/unpin
and reclamation paths, a `EpochStats` struct in
`crossbeam-epoch/src/collector.rs`, and public API exposure.

## Wide

### W1: Implement a lock-free concurrent B-tree map

Add a `crossbeam-btree` crate with a lock-free concurrent B-tree
supporting `insert`, `remove`, `get`, `range`, and `iter` — all using
epoch-based memory reclamation from `crossbeam-epoch`. Implement
node splitting and merging with atomic pointer swaps. Changes span
a new crate with `Cargo.toml`, node and tree modules, epoch-guarded
traversal, iterator with guard pinning, range query support,
workspace manifest updates, re-exports from the umbrella `crossbeam`
crate, and comprehensive tests.

### W2: Implement an async-compatible channel layer

Add async `send` and `recv` methods to `crossbeam-channel` channels
that integrate with `std::task::Waker` and are compatible with any
async runtime (tokio, async-std, smol). Support `select!` macro with
mixed sync/async operations. Changes span a new `async` module in
`crossbeam-channel/src/`, Waker integration in the `waker.rs` module,
per-flavor async implementations in `flavors/`, `Future`
implementations for send/recv operations, `select.rs` extension for
async selects, and feature-gated dependencies.

### W3: Implement a concurrent memory pool with epoch-safe allocation

Add a `crossbeam-pool` crate that provides a lock-free object pool
with type-erased slots, epoch-guarded deallocation, thread-local
free lists, and configurable pool sizing (fixed, grow-on-demand,
bounded). Use `crossbeam-epoch` for safe reclamation. Changes span
a new crate with pool allocator, slot management, thread-local
cache using `crossbeam-utils`, epoch integration, workspace manifest,
re-exports from `crossbeam`, and integration tests with stress
testing.

### W4: Implement NUMA-aware data structures

Add NUMA awareness to `ArrayQueue`, `SegQueue`, and `ShardedLock`:
per-NUMA-node memory allocation, NUMA-local fast paths for push/pop,
cross-node stealing for load balancing, and topology detection via
`libnuma` (Linux) or platform APIs. Changes span a new `numa` module
in `crossbeam-utils/src/`, NUMA-aware allocation wrappers, per-NUMA
partitioning in `crossbeam-queue/src/`, NUMA-aware shard assignment
in `ShardedLock`, deque topology in `crossbeam-deque/src/`, and
platform-specific build configuration.

### W5: Implement a hazard-pointer-based reclamation scheme as an alternative to epoch

Add a `crossbeam-hazard` crate implementing hazard-pointer-based
memory reclamation as a drop-in alternative to `crossbeam-epoch`.
Provide `HazardGuard` (analogous to `Guard`), `HazardAtomic`
(analogous to `Atomic<T>`), per-thread hazard pointer arrays, and
scan-based reclamation. Adapt `crossbeam-skiplist` and
`crossbeam-queue` to be generic over the reclamation scheme. Changes
span a new crate, trait abstraction for reclamation in
`crossbeam-epoch`, generics in `crossbeam-skiplist/src/base.rs` and
queue sources, workspace manifest, and comparative benchmarks.

### W6: Implement a concurrent graph data structure

Add a `crossbeam-graph` crate with a lock-free directed graph
supporting concurrent vertex/edge insertion and removal, adjacency
queries, BFS/DFS traversal under epoch protection, and topological
sort. Use `crossbeam-epoch` for reclamation and `crossbeam-skiplist`
for ordered adjacency lists. Changes span a new crate with vertex
and edge modules, epoch-guarded traversal iterators, per-vertex
adjacency using `SkipMap`, graph-level operations (connected
components, shortest path), workspace manifest, and stress tests.

### W7: Implement a distributed channel protocol for cross-process communication

Extend `crossbeam-channel` with a cross-process channel backed by
shared memory and futex-based waking. Support bounded and unbounded
variants, producer/consumer in separate processes, serialization
via `serde`, and graceful process-crash detection. Changes span a
new `crossbeam-ipc` crate, shared-memory ring buffer implementation,
futex-based waker in `waker.rs` adaptation, serialization layer,
crash detection via heartbeats, process-scoped epoch reclamation,
workspace manifest, and integration tests with `fork`.

### W8: Implement comprehensive Loom model-checking integration across all crates

Add `loom` model-checking support to all six crates: replace `std`
atomics with `loom::sync::atomic`, add `loom`-compatible thread
spawning, write model-checked tests for every lock-free algorithm,
and configure CI to run loom tests. Changes span build configuration
(`cfg(loom)`) in every crate, `loom` wrappers for `Atomic`,
`AtomicCell`, `CachePadded`, `Parker`, test modules in each crate
under `#[cfg(loom)]`, `epoch` collector tests with loom threads,
channel flavor tests, deque steal tests, and CI workflow updates.

### W9: Implement a benchmarking and profiling framework for concurrent data structures

Add a `crossbeam-bench` crate that provides: parameterized throughput
benchmarks (varying thread count, contention level, operation mix),
latency histograms, fairness metrics (per-thread operation counts),
cache-miss profiling integration (perf counters), and comparison
harness against `std::sync` equivalents. Generate HTML reports with
charts. Changes span a new crate, benchmark runners for each sub-
crate's data structures, metric collection and aggregation, report
generation, CI integration for regression detection, and workspace
manifest.

### W10: Implement a transactional memory abstraction over epoch-based structures

Add a software transactional memory (STM) layer that composes
operations on multiple `crossbeam` data structures atomically:
`transaction(|tx| { tx.insert(&map, k, v); tx.push(&queue, item); })`.
Support conflict detection, retry with backoff, read/write sets,
and composable nested transactions. Changes span a new
`crossbeam-stm` crate, per-structure transactional wrappers for
`SkipMap`, `ArrayQueue`, `SegQueue`, epoch-based conflict detection,
transaction log with rollback, workspace manifest, and stress tests
verifying serializability.

### N11: Fix CHANGELOG.md not documenting MSRV policy changes across sub-crate releases

The `CHANGELOG.md` documents feature changes but does not track
minimum supported Rust version (MSRV) bumps per sub-crate release.
When `crossbeam-epoch` or `crossbeam-channel` bump their `rust-version`
in their per-crate `Cargo.toml`, the root `CHANGELOG.md` does not
mention it, surprising downstream users who pin MSRV. Add an "MSRV
Changes" subsection to each release in `CHANGELOG.md` that lists
per-crate MSRV changes. Also update the root `README.md` to link to
the "Compatibility" section and add an MSRV badge that reflects the
workspace-level `rust-version` from `Cargo.toml`.

### M11: Add no_std CI testing matrix for crates that support it

The CI workflow in `.github/workflows/ci.yml` tests standard builds
but does not test `no_std` configurations, despite
`crossbeam-epoch`, `crossbeam-queue`, and `crossbeam-utils`
supporting `no_std` via `default-features = false`. Add a CI job
matrix entry that runs `cargo check --no-default-features` for each
of these three crates targeting `thumbv7m-none-eabi` (a no_std
embedded target). Update `.clippy.toml` to add lint rules for
`no_std` compatibility (e.g. `disallowed-methods` covering `std`
allocator paths that should not appear in `no_std` code). Add a
`no_std` section to the root `README.md` documenting which crates
and features are available without `std`, including a table of crate
features and their `std` requirements.

### W11: Overhaul workspace configuration and release infrastructure

Restructure the crossbeam workspace release process across all
non-code configuration files. The following items are missing or
incomplete and must be addressed: (1) Add a root `release.toml`
configuration file for `cargo-release` with per-crate version bump
rules and pre-release hooks that verify each sub-crate's
`CHANGELOG.md` has an "Unreleased" section before publishing;
(2) Update `.github/workflows/release.yml` to add a publish job that
runs `tools/publish.sh` in dependency order after creating GitHub
releases, replacing the current manual publish step; (3) Add
`SECURITY.md` at the repo root with vulnerability reporting
instructions specific to unsafe-code issues in the six sub-crates,
including how to report memory-safety bugs in `crossbeam-epoch`
and `crossbeam-deque`; (4) Standardize all per-crate `CHANGELOG.md`
files (`crossbeam-channel/CHANGELOG.md`,
`crossbeam-deque/CHANGELOG.md`, `crossbeam-epoch/CHANGELOG.md`,
`crossbeam-queue/CHANGELOG.md`, `crossbeam-skiplist/CHANGELOG.md`,
`crossbeam-utils/CHANGELOG.md`) to use the Keep a Changelog format
with a consistent "## Unreleased" header section; (5) Update the
root `README.md` to add a workspace crate dependency diagram showing
which crates depend on which, and a per-crate feature flag matrix
showing which features require `std` vs work in `no_std` environments.
