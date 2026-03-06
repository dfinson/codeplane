# crossbeam-rs/crossbeam

| Field | Value |
|-------|-------|
| **URL** | https://github.com/crossbeam-rs/crossbeam |
| **License** | MIT / Apache-2.0 |
| **Language** | Rust |
| **Scale** | Large |
| **Category** | Concurrent data structures |
| **Set** | Cutoff |
| **Commit** | `bc5f78cb544fa03a40474e878a84b3cdd640f2fa` |

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

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix ArrayQueue::push not waking parked consumers after a successful push

In `crossbeam-queue/src/array_queue.rs`, after a successful `push`
the implementation does not signal any mechanism to wake consumers
that may be spin-waiting via `Backoff`. When the queue transitions
from empty to non-empty, consumers using `Backoff::snooze()` in a
polling loop can experience unnecessary latency. Fix `push` to
include a fence or wake mechanism after successfully enqueuing to
the previously-empty queue.

### N2: Fix SegQueue::len returning an inconsistent count under concurrent access

The `len()` method in `crossbeam-queue/src/seg_queue.rs` computes
length as `tail.wrapping_sub(head)` using relaxed loads of `head`
and `tail`. Under concurrent push/pop, `head` can be read after
`tail`, producing a negative (wrapped) count. Fix `len` to use
acquire ordering and read `head` before `tail` to ensure a
consistent lower bound.

### N3: Fix Backoff::is_completed always returning false after reset

In `crossbeam-utils/src/backoff.rs`, calling `Backoff::reset()` sets
the internal step counter to 0 but does not clear the "completed"
state flag if the backoff was previously exhausted. Subsequent calls
to `is_completed()` return `false` even after re-exhausting all
steps. Fix `reset` to also clear the internal state used by
`is_completed`.

### N4: Fix ShardedLock::try_write not returning WouldBlock when readers are active

In `crossbeam-utils/src/sync/sharded_lock.rs`, `try_write()` is
documented to return `TryLockError::WouldBlock` when the lock cannot
be acquired without blocking. However, when reader shards are active,
the implementation spins briefly before returning the error, violating
the "try" semantics. Fix `try_write` to check all shards without any
spin loop and return immediately if any shard has active readers.

### N5: Fix epoch Guard::flush not advancing the global epoch when called repeatedly

In `crossbeam-epoch/src/guard.rs`, `Guard::flush()` processes the
thread-local garbage bag and moves it to the global queue, but does
not trigger a global epoch advancement. If only one thread is
pinned and calling `flush()` repeatedly, deferred destructors
accumulate without being reclaimed because the epoch never advances.
Fix `flush` to attempt a global epoch advancement after moving the
local bag.

### N6: Fix bounded channel try_send returning Full when the receiver has disconnected

In `crossbeam-channel/src/flavors/array.rs`, `try_send` on a bounded
channel where all receivers have been dropped returns
`TrySendError::Full` instead of `TrySendError::Disconnected` when the
ring buffer is also full. The disconnect check happens after the
capacity check, so a full+disconnected channel reports `Full`. Fix
the ordering to check for disconnection first and return the correct
error variant.

### N7: Fix SkipMap::get_or_insert not linearizable when called concurrently with remove

In `crossbeam-skiplist/src/base.rs`, `get_or_insert` first searches
for the key and then inserts if not found. Between the search and
insert, a concurrent `remove` can delete the found entry, causing
`get_or_insert` to return a reference to a logically-removed node
that will be reclaimed. Fix the insert path to re-validate the node's
removal status under the epoch guard before returning.

### N8: Fix WaitGroup::wait not returning promptly when the count reaches zero concurrently

In `crossbeam-utils/src/sync/wait_group.rs`, after the internal count
reaches zero, the last `drop` of a `WaitGroup` clone unparks the
waiting thread. However, if `wait()` is called concurrently with the
final `drop`, there is a race window where the count reaches zero
before `wait` parks, causing it to park indefinitely. Fix the
`wait` implementation to re-check the count after registering the
parker to avoid the missed-wakeup race.

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

### M4: Add Stealer::steal_batch_with_limit to work-stealing deque

Implement `Stealer::steal_batch_with_limit(dest, limit)` that steals
up to `limit` items from the victim into the destination worker.
Requires changes to `crossbeam-deque/src/deque.rs` for the new method
on `Stealer`, atomic index arithmetic for partial batch stealing,
buffer wrapping logic, steal result types, and integration with the
`Injector` for limit-aware steal from the global queue.

### M5: Implement epoch-guarded concurrent hash map

Add a `HashMap` to `crossbeam-skiplist` (or a new `crossbeam-hashmap`
crate) that uses epoch-based reclamation for lock-free operations.
Requires a hash table with bucket arrays in a new source file,
epoch-guarded insert/remove/get operations using `crossbeam-epoch`,
resizing logic with atomic bucket migration, iterator support with
guard pinning, and `Cargo.toml` manifest for the new crate or module.

### M6: Add Parker::park_timeout with spurious-wakeup-safe API

Implement `Parker::park_timeout(Duration)` that parks the thread
with a timeout and returns a `ParkResult` enum (`Notified`, `TimedOut`,
`Spurious`). Requires changes to `crossbeam-utils/src/sync/parker.rs`
for the timed variant, platform-specific condvar timeout handling,
the result enum definition, integration with `Backoff` for timeout-
aware spinning, and documentation.

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
