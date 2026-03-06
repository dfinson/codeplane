# abseil/abseil-cpp

| Field | Value |
|-------|-------|
| **URL** | https://github.com/abseil/abseil-cpp |
| **License** | Apache-2.0 |
| **Language** | C++ |
| **Scale** | Very Large |
| **Category** | C++ standard library extensions |
| **Set** | Cutoff |
| **Commit** | `60152322663f4e5a16cb71ca8c5f18c38a081265` |

## Why this repo

- **Well-structured**: Modular library organised into independent
  components under `absl/`: strings (Cord, StrCat, StrFormat),
  containers (flat_hash_map, btree, inlined_vector), synchronisation
  (Mutex, Notification, CondVar), time (Duration, Time, TimeZone),
  status (Status, StatusOr), hash, logging, flags, random, CRC, and
  debugging. Each module has public headers, internal implementation,
  and separated tests.
- **Rich history**: 6K+ commits, 15K+ stars. Google's foundational
  C++ library, widely adopted. Issues span performance, portability,
  correctness, and standards-track evolution.
- **Permissive**: Apache-2.0 license.

## Structure overview

```
absl/
├── strings/
│   ├── str_cat.h / str_cat.cc         # StrCat(), StrAppend() — efficient concatenation
│   ├── str_format.h                   # StrFormat() — type-safe printf
│   ├── str_split.h                    # StrSplit() — string splitting
│   ├── str_join.h                     # StrJoin() — string joining
│   ├── substitute.h                   # Substitute() — positional formatting
│   ├── ascii.h                        # ASCII character classification
│   ├── escaping.h                     # CEscape, CUnescape, Base64
│   ├── match.h                        # EqualsIgnoreCase, StartsWith, EndsWith
│   ├── numbers.h                      # SimpleAtoi, SimpleAtof — parsing
│   ├── charconv.h                     # from_chars — float/double parsing
│   ├── cord.h                         # Cord — heavy-duty immutable string
│   ├── cord_buffer.h                  # CordBuffer — mutable cord building
│   ├── string_view.h                  # string_view implementation
│   └── internal/                      # Cord B-tree, char conversion, etc.
├── container/
│   ├── flat_hash_map.h                # Swiss Table hash map
│   ├── flat_hash_set.h                # Swiss Table hash set
│   ├── node_hash_map.h                # Node-based hash map (pointer stability)
│   ├── node_hash_set.h                # Node-based hash set
│   ├── btree_map.h                    # B-tree ordered map
│   ├── btree_set.h                    # B-tree ordered set
│   ├── inlined_vector.h               # Small-buffer-optimised vector
│   ├── fixed_array.h                  # Stack-allocated array
│   └── internal/raw_hash_set.h        # Core Swiss Table implementation
├── synchronization/
│   ├── mutex.h / mutex.cc             # Mutex with conditional critical sections
│   ├── notification.h                 # One-shot notification
│   ├── blocking_counter.h             # Countdown barrier
│   └── internal/                      # Waiter, GraphCycles (deadlock detection)
├── time/
│   ├── time.h / time.cc               # Time, Duration — arithmetic, formatting
│   ├── civil_time.h                   # CivilDay, CivilMonth — calendaric time
│   ├── clock.h                        # Now(), SleepFor() — system clock
│   └── internal/cctz/                 # CCTZ timezone library
├── status/
│   ├── status.h / status.cc           # Status — error code + message + payload
│   ├── statusor.h                     # StatusOr<T> — value-or-error
│   └── status_matchers.h              # GoogleTest matchers
├── hash/
│   ├── hash.h                         # absl::Hash — extensible hashing framework
│   └── internal/                      # City hash, low-level hash combine
├── log/
│   ├── log.h                          # LOG() macro — structured logging
│   ├── check.h                        # CHECK(), DCHECK() macros
│   ├── die_if_null.h                  # Pointer null-check with fatal
│   ├── log_sink.h                     # LogSink interface for custom sinks
│   └── internal/                      # Log message formatting, sink registry
├── flags/
│   ├── flag.h                         # ABSL_FLAG() — command-line flags
│   ├── parse.h                        # absl::ParseCommandLine()
│   ├── usage.h                        # Usage/help generation
│   └── internal/                      # Flag registry, marshalling
├── random/
│   ├── random.h                       # absl::BitGen — random bit generator
│   ├── distributions.h                # Uniform, Gaussian, Bernoulli
│   └── internal/                      # PCG, randen engine
├── debugging/
│   ├── stacktrace.h                   # GetStackTrace()
│   ├── symbolize.h                    # Symbolize() — address-to-symbol
│   ├── failure_signal_handler.h       # SIGSEGV/SIGABRT handler
│   └── internal/                      # Unwinder, ELF reader
├── crc/
│   ├── crc32c.h                       # CRC32C computation
│   └── internal/                      # Hardware-accelerated CRC
├── base/
│   ├── config.h                       # Platform/compiler detection macros
│   ├── optimization.h                 # ABSL_PREDICT_TRUE/FALSE
│   ├── thread_annotations.h           # Thread-safety annotations
│   ├── log_severity.h                 # LogSeverity enum
│   └── internal/                      # Spinlock, sysinfo, malloc hooks
├── numeric/
│   ├── int128.h                       # int128, uint128 types
│   └── bits.h                         # Bit manipulation utilities
├── functional/
│   ├── any_invocable.h                # Move-only std::function replacement
│   ├── function_ref.h                 # Non-owning function reference
│   └── overload.h                     # Overload set for visitors
├── types/
│   ├── optional.h                     # absl::optional (pre-C++17 backport)
│   ├── variant.h                      # absl::variant (pre-C++17 backport)
│   ├── any.h                          # absl::any (pre-C++17 backport)
│   └── span.h                         # absl::Span — non-owning array view
├── cleanup/
│   └── cleanup.h                      # absl::Cleanup — scope guard
├── algorithm/
│   └── container.h                    # Container algorithm wrappers
├── memory/
│   └── memory.h                       # WrapUnique, make_unique (backport)
├── meta/
│   └── type_traits.h                  # Type traits extensions
├── utility/
│   └── utility.h                      # absl::exchange, in_place_t
└── profiling/
    └── internal/                      # PeriodicSampler, ExponentialBiased
```

## Scale indicators

- ~522 non-test source files (.h + .cc)
- ~143K lines of code
- Deep structure (3–4 levels: `absl/strings/internal/`)
- Zero external dependencies (self-contained)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add StrContainsIgnoreCase to strings/match.h

The `absl/strings/match.h` header provides `StrContains()`,
`StartsWith()`, `EndsWithIgnoreCase()`, and other matchers, but there
is no `StrContainsIgnoreCase()`. Add a case-insensitive substring search
function that converts both strings to lowercase using `absl::ascii_tolower`
incrementally (not allocating a copy) and performs the substring search.
Add the declaration to `match.h` and implementation to `match.cc`.

### N2: Fix Cord::Compare not short-circuiting on length mismatch

The `Cord::Compare()` method in `strings/cord.cc` performs a
byte-by-byte comparison of two Cords. When two Cords differ in length
and the shorter one is a prefix of the longer, the traversal continues
past the shorter cord's end rather than immediately returning based on
length. Add an early length-based short-circuit for the prefix case.

### N3: Add Duration::ToTimeval and Duration::ToTimespec symmetry checks

The `absl/time/time.h` header provides `ToTimeval()` and `ToTimespec()`
for converting `Duration` to POSIX time structures, but there are no
`constexpr` compile-time assertions verifying the round-trip correctness
of `FromTimeval(ToTimeval(d)) == d` for representable durations. Add
static assertions in `time.cc` that validate round-trip behaviour for
edge-case durations (zero, max, negative).

### N4: Fix flat_hash_map::insert_or_assign not returning correct bool on existing key

The `flat_hash_map::insert_or_assign()` in `container/flat_hash_map.h`
(via `raw_hash_set`) should return `{iterator, false}` when the key
already exists and the value is assigned. Verify and fix the return
value semantics in the internal `raw_hash_set::insert_or_assign`
implementation to match the C++17 standard requirements for
`std::unordered_map::insert_or_assign`.

### N5: Add absl::AsciiStrToLower and AsciiStrToUpper in-place variants

The `absl/strings/ascii.h` header provides `absl::AsciiStrToLower()`
that returns a new string. Add an in-place variant `AsciiStrToLower(
std::string* s)` that modifies the string in-place without allocating
a new one, for use in performance-sensitive code paths. Similarly add
`AsciiStrToUpper(std::string* s)`.

### N6: Fix StatusOr move-assignment not clearing the error on value assignment

When a `StatusOr<T>` that holds an error status is move-assigned from a
`StatusOr<T>` that holds a value, the error payload memory from the
original status should be released. Verify and fix the move-assignment
operator in `status/statusor.h` to ensure the error payload destructor
runs before the value is constructed in-place.

### N7: Add Notification::WaitForNotificationWithDeadline

The `Notification` class in `synchronization/notification.h` provides
`WaitForNotificationWithTimeout(Duration)` but not a deadline-based
variant that accepts `absl::Time`. Add
`WaitForNotificationWithDeadline(absl::Time deadline)` that computes
the remaining duration and delegates to the timeout variant, handling
the case where the deadline has already passed.

### N8: Add StrSplit support for limiting the number of splits

The `StrSplit()` function in `strings/str_split.h` splits a string on
a delimiter without a limit on the number of resulting pieces. Add a
`MaxSplits(int n)` wrapper that can be composed with any delimiter
to limit splitting: `StrSplit(s, MaxSplits(ByChar(','), 3))`, producing
at most `n+1` pieces with the remainder in the last piece.

### N9: Fix CRC32C::Extend not handling zero-length input without branch

The `crc32c::ExtendCrc32c()` in `crc/crc32c.h` handles zero-length
input correctly but incurs an unnecessary branch in the hot path. When
hardware CRC is available, the empty-input branch is never taken by the
CPU branch predictor but still impacts code layout. Move the
zero-length check to a cold path with `ABSL_PREDICT_FALSE`.

### N10: Add uint128 literal operator for compile-time construction

The `absl::uint128` type in `numeric/int128.h` requires runtime
construction via `absl::MakeUint128(high, low)`. Add a user-defined
literal operator `_u128` that enables compile-time construction:
`auto x = 123456789012345678901234567890_u128`. Implement the literal
parser as a `constexpr` function template that processes the digit
string at compile time.

## Medium

### M1: Implement StrFormat compile-time format string validation

The `StrFormat()` function in `strings/str_format.h` validates format
strings at runtime. Add `consteval` format string parsing (C++20) that
catches mismatched format specifiers and argument types at compile time.
Requires a `consteval` format parser, integration with the existing
`FormatSpec` machinery, compile-time type checking against the format
argument pack, and backward-compatible runtime fallback for C++17.

### M2: Add flat_hash_map::extract and merge operations

The `flat_hash_map` in `container/flat_hash_map.h` does not support
node extraction and merge operations (`extract(key)`, `merge(other_map)`)
that `std::unordered_map` provides. Implement `extract()` returning a
node handle, `insert(node_handle)`, and `merge()` in the `raw_hash_set`
layer. Requires a `node_handle` type, rehashing integration, and proper
allocator handling across merged maps.

### M3: Implement Cord::FindAll for multi-match substring search

The `Cord` type in `strings/cord.h` provides `Cord::Find()` for
single-occurrence substring search. Add `Cord::FindAll(string_view
pattern)` returning a vector of offsets, using an Aho-Corasick or
Boyer-Moore-Horspool algorithm adapted for the Cord's chunked
representation. Requires traversal via `CordRepBtreeNavigator`,
cross-chunk boundary matching, and efficient result accumulation.

### M4: Add structured data payloads to absl::Status

The `Status` class in `status/status.h` supports string payloads but
not typed structured data. Add `Status::SetPayload<T>(key, value)` and
`Status::GetPayload<T>(key)` using type-erased storage with
`absl::any`. Requires serialisation hooks for cross-process transport,
payload forwarding in `StatusOr`, and integration with the existing
`Cord`-based payload mechanism.

### M5: Implement a thread-safe bounded MPMC queue in synchronization/

Add `absl::BoundedQueue<T>` — a fixed-capacity multi-producer
multi-consumer queue using the Abseil `Mutex` and `CondVar`. Support
blocking `Push`/`Pop`, try variants with timeout, `Close()` for
graceful shutdown, and `Drain()`. Requires a ring buffer implementation,
`Mutex` condition-based waiting, proper move semantics, and integration
with Abseil's thread-safety annotations.

### M6: Add log structured fields for machine-parseable log output

The `LOG()` macro in `log/log.h` emits unstructured text. Add
`LOG(INFO).Field("user_id", 42).Field("action", "login")` for
structured key-value logging. Requires a `StructuredLogMessage` builder,
integration with `LogSink` for sinks that understand structured data
(JSON output), backward-compatible text rendering for traditional sinks,
and `log_entry.h` updates for structure field access.

### M7: Implement flag validation with custom validators

The `ABSL_FLAG()` system in `flags/flag.h` supports type-based parsing
but not custom value validation (e.g., `--port` must be 1–65535). Add
`absl::RegisterFlagValidator<T>(flag, validator)` that runs after
parsing and `absl::ParseFlagValidator` for compile-time registration.
Requires validator storage in the flag registry, validation callback
invocation in `ParseCommandLine()`, and error reporting integration.

### M8: Add absl::Hash support for heterogeneous lookup keys

The `absl::Hash` framework in `hash/hash.h` requires hashing the exact
key type. Add transparent hashing support for heterogeneous lookup:
`flat_hash_map<std::string, V, absl::Hash<void>>` should support
`find(string_view)` without constructing a `std::string`. Requires
`AbslHashValue` specialisation for transparent comparison, `is_transparent`
trait integration in `raw_hash_set`, and compatibility with existing
hash customisation points.

### M9: Implement Duration formatting with custom patterns

The `absl::FormatDuration()` in `time/time.h` outputs a fixed format.
Add `absl::FormatDuration(Duration d, string_view pattern)` supporting
patterns like `"%H:%M:%S.%f"` (hours, minutes, seconds, fractional
seconds), `"%dd %Hh"` (days and hours), and locale-aware formatting.
Requires a duration format parser, component extraction from the
`Duration` representation, and output string building.

### M10: Add btree_map range-delete optimisation

The `btree_map` in `container/btree_map.h` supports `erase(first, last)`
for iterator-range deletion, but it erases elements one by one. Add an
optimised range-delete path in the btree internal implementation
(`container/internal/btree.h`) that removes entire nodes when the range
spans complete node boundaries, improving performance from O(k log n)
to O(log n + k/B) where B is the node fanout.

## Wide

### W1: Implement a concurrent hash map for high-contention scenarios

Add `absl::ConcurrentHashMap<K, V>` — a lock-striped concurrent hash
map with fine-grained locking, lock-free reads for immutable entries,
configurable stripe count, and `ForEach` iteration under snapshot
semantics. Requires a new module under `container/`, integration with
`absl::Mutex` for per-stripe locks, `absl::Hash` for key hashing,
`raw_hash_set` design influence for probing, and comprehensive
thread-safety annotations.

### W2: Add a coroutine-aware synchronization library

Implement `absl::CoMutex`, `absl::CoCondVar`, and `absl::CoNotification`
that integrate with C++20 coroutines. When a coroutine awaits a mutex,
it suspends without blocking the thread. Requires coroutine handle
management in a new `synchronization/co_` module, `Awaitable` concepts,
integration with the existing `Mutex` waiter infrastructure for hybrid
thread/coroutine scheduling, and a coroutine-friendly `BoundedQueue`.

### W3: Implement a type-erased serialisation framework

Add `absl::Serialize(const T&) -> Cord` and `absl::Deserialize<T>(Cord)
-> StatusOr<T>` using a reflection-like registration system. Support
primitive types, containers, `absl::optional`, `absl::variant`, nested
structs, and schema evolution (added/removed fields). Requires a type
registry, encoder/decoder for a compact binary format, `Cord` builder
integration, schema versioning metadata, and compile-time registration
via `ABSL_SERIALIZABLE()` macro.

### W4: Add a comprehensive benchmarking framework

Implement `absl::Benchmark` — a micro-benchmarking framework: `ABSL_BENCHMARK(BM_Sort)`,
fixture support, parameterised benchmarks, CPU/wall time measurement,
memory allocation tracking, statistical analysis (mean, stddev, CI),
and CSV/JSON output. Requires a benchmark registry, runner with
warm-up and iteration control, platform-specific timers (TSC, clock_gettime),
allocator interposition, and result comparison between runs.

### W5: Implement a command-line subcommand framework on top of absl::flags

Extend `absl/flags/` with subcommand support: `myapp build --jobs 4`,
`myapp test --filter "..."`. Add `ABSL_SUBCOMMAND()` macro, subcommand
routing in `ParseCommandLine()`, per-subcommand flag namespacing, help
text generation with subcommand listing, completion generation, and
nested subcommand hierarchies. Changes span `flags/flag.h`, `flags/parse.cc`,
`flags/usage.cc`, and a new `flags/subcommand.h`.

### W6: Add a safe subset of absl::strings for untrusted input processing

Implement `absl::SafeStrFormat()`, `absl::SafeStrCat()`, and
`absl::SafeSubstitute()` that enforce output length limits, reject
format strings with unbounded repetition, sanitise non-printable
characters, and support HTML/URL/SQL escaping modes. Requires input
validation in `str_format` internals, configurable escaping backends,
output truncation with indicators, and integration with `Status` for
error reporting on malformed input.

### W7: Implement an asynchronous logging pipeline

Add `absl::AsyncLogSink` — a `LogSink` that buffers log entries in a
lock-free queue and flushes them from a background thread. Support
backpressure when the queue is full, configurable flush interval and
batch size, graceful drain on process exit, and sink chaining (async →
file, async → network). Requires a lock-free SPSC queue, background
thread management with `Mutex`/`CondVar`, integration with `log_sink.h`,
and `LOG` macro performance optimisation.

### W8: Add a compile-time reflection system for absl types

Implement `absl::Reflect<T>()` that provides compile-time access to
struct field names, types, and offsets for types annotated with
`ABSL_REFLECT()`. Support field iteration, field access by name,
`StructuredLog` integration, `Serialize` integration, and `StrFormat`
integration for automatic struct formatting. Requires a macro-based
field registration system, `constexpr` field descriptor types, and
template metaprogramming for field pack traversal.

### W9: Implement a distributed-systems time library extension

Extend `absl/time/` with distributed-clock primitives: `HybridLogicalClock`
(HLC) for causally-ordered timestamps, `VectorClock` for partial
ordering, `TrueTime`-style clock with uncertainty bounds, and clock
synchronisation utilities. Requires new types in `time/`, serialisation
to `Cord`, comparison operators respecting uncertainty, integration with
`Status` for clock-error reporting, and compatibility with the existing
`absl::Time` type.

### W10: Add a safe memory management module

Implement `absl::Arena` — a region-based memory allocator for bulk
allocation and deallocation, `absl::ObservedPtr<T>` — a non-owning
pointer that detects use-after-free in debug mode, and `absl::Pool<T>` —
a thread-safe object pool with pre-allocation. Requires allocator
integration in `base/`, `flat_hash_map` allocator support tests,
`Cord` arena-allocated node support, debug-mode poisoning for freed
memory, and thread-safety annotations.
