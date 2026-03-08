# ReactiveX/RxSwift

| Field | Value |
|-------|-------|
| **URL** | https://github.com/ReactiveX/RxSwift |
| **License** | MIT |
| **Language** | Swift |
| **Scale** | Large |
| **Category** | Reactive extensions for Swift |
| **Set** | eval |
| **Commit** | `c5a74e0378ab8fe8a8f16844fd438347d87e5641` |

## Why this repo

- **Multi-subsystem**: Core observables, operators, subjects, schedulers, disposables, traits (Single/Maybe/Completable/Infallible), RxCocoa UI bindings, RxBlocking, RxRelay, RxTest
- **Well-structured**: Clear separation between RxSwift/, RxCocoa/, RxBlocking/, RxRelay/, RxTest/, Platform/
- **Rich history**: 24K+ stars, mature reactive programming library with 70+ operators

## Structure overview

```
RxSwift/
├── Observables/         # 70+ operators: Map, Filter, Merge, CombineLatest, Throttle, etc.
├── Subjects/            # PublishSubject, BehaviorSubject, ReplaySubject, AsyncSubject
├── Schedulers/          # MainScheduler, SerialDispatchQueue, ConcurrentDispatchQueue, etc.
├── Disposables/         # DisposeBag, CompositeDisposable, SerialDisposable, etc.
├── Concurrency/         # AsyncLock, Lock, SynchronizedOnType
├── Traits/              # PrimitiveSequence (Single, Maybe, Completable), Infallible
├── Extensions/          # Bag+Rx
RxCocoa/
├── Common/              # DelegateProxy, Binder, Observable+Bind
├── Foundation/          # URLSession+Rx, NotificationCenter+Rx, KVO
├── iOS/                 # UIKit reactive extensions
├── Traits/              # ControlEvent, ControlProperty, Driver, Signal
RxBlocking/              # Synchronous blocking operators
RxRelay/                 # PublishRelay, BehaviorRelay, ReplayRelay
RxTest/                  # TestScheduler, HotObservable, ColdObservable, TestableObserver
Platform/                # AtomicInt, RecursiveLock, data structures (Bag, Queue, PriorityQueue)
```

## Scale indicators

- ~1,000 Swift source files
- ~170K lines of code
- 70+ observable operators with scheduler-aware implementations
- Multiple subsystems: core, UI bindings, testing, blocking, relay

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `throttle` operator dropping the last element when using `.latest` mode

The `ThrottleSink` in `Observables/Throttle.swift` schedules a timer on `next` events but when the source completes while a throttle window is active, the pending latest value is never forwarded. The `on(.completed)` path disposes the scheduled item without flushing it. Document the corrected throttle behavior in `Documentation/Tips.md`.

### N2: Fix `ReplaySubject` not trimming buffer after `bufferSize` exceeded under concurrent access

In `Subjects/ReplaySubject.swift`, the replay buffer (`ReplayBufferBase`) trims elements in `_synchronized_on`, but concurrent subscriptions can read the un-trimmed buffer between the append and trim, causing subscribers to receive more elements than `bufferSize` specifies.

### N3: Fix `DisposeBag` not thread-safe when calling `insert` during `deinit`

`Disposables/DisposeBag.swift` uses a lock in `insert(_:)` but the `deinit` path calls `dispose()` without holding the lock, creating a race if another thread is inserting a disposable concurrently with bag deallocation.

### N4: Add `distinctUntilChanged` overload accepting `KeyPath` for `Infallible`

The `Infallible` trait in `Traits/Infallible/Infallible+Operators.swift` forwards most operators from `Observable` but lacks a `distinctUntilChanged` variant that accepts a `KeyPath<Element, V>` for property-based comparison, unlike the base `ObservableType` extension.

### N5: Fix `HistoricalScheduler` not advancing clock on `sleep` call

In `Schedulers/HistoricalScheduler.swift`, the `sleep(_:)` method should advance the virtual clock by the specified duration, but it currently calls the superclass `VirtualTimeScheduler.sleep` which is a no-op, preventing time-based operators from progressing in test scenarios.

### N6: Fix `window` operator not completing inner observable on source error

In `Observables/Window.swift`, when the source observable errors, the `WindowTimeCountSink` disposes the timer and forwards the error to the outer observable, but the current inner `Subject` never receives a `.completed` or `.error` event, leaving subscribers hanging.

### N7: Add `timeout` operator variant for `Maybe` trait

The `PrimitiveSequence` extensions in `Traits/PrimitiveSequence/PrimitiveSequence.swift` expose `timeout` for `Single` and `Completable` but the `Maybe` type is missing a `timeout` overload, requiring users to convert to `Observable` and back.

### N8: Fix `sample` operator emitting duplicate values when trigger fires rapidly

In `Observables/Sample.swift`, the `SampleSequenceSink` stores the latest value and a `hasValue` flag, but when the sampler fires multiple times before a new source value arrives, the same value is emitted repeatedly because `hasValue` is not cleared after emission when `onlyNew` is not available.

### N9: Fix `CurrentThreadScheduler` recursive scheduling causing stack overflow

In `Schedulers/CurrentThreadScheduler.swift`, recursive `schedule` calls within a scheduled action queue work items into the current thread's queue, but deeply nested recursive scheduling causes unbounded stack growth because the trampoline re-enters before the queue drains.

### N10: Add `materialize` operator for `Completable` trait

The `Completable` extensions in `Traits/PrimitiveSequence/Completable.swift` lack a `materialize()` operator that would convert `.completed` and `.error` events into `Observable<CompletableEvent>`, unlike `Single` and `Maybe` which have their own materialized event types.

### N11: Fix `Documentation/GettingStarted.md` code samples using deprecated `bind(to:)` syntax

The `Documentation/GettingStarted.md` file contains code examples that use the deprecated `bind(to:)` API patterns and reference outdated import statements. Update all code samples in `Documentation/GettingStarted.md` to use current syntax, fix broken markdown links in `Documentation/Examples.md` and `Documentation/Traits.md`, and add version-specific callouts for API differences between RxSwift 5 and 6 in `Documentation/SwiftConcurrency.md`.

## Medium

### M1: Implement `share(replay:scope:)` for `Infallible`

The `Infallible` trait needs a `share(replay:scope:)` operator equivalent to `Observable.share(replay:scope:)` in `Observables/ShareReplayScope.swift`. This requires adapting `ShareReplay1WhileConnected` and `ShareReplayLifetimeScope` to work with the `InfallibleType` protocol while preserving the no-error guarantee.

### M2: Add retry with exponential backoff operator

Implement a `retry(maxAttempts:delay:multiplier:scheduler:)` operator that retries a failed observable sequence with configurable exponential backoff. The operator should integrate with `RetryWhen` logic in `Observables/RetryWhen.swift` and schedulers for delay timing without blocking threads. Update `README.md` with retry operator documentation and add error handling guidance.

### M3: Implement `TestScheduler` cold observable auto-disposal tracking

Extend `RxTest/Schedulers/TestScheduler.swift` to track all subscriptions created by `createColdObservable` and provide an assertion method `assertAllDisposed(by:)` that verifies all subscriptions are disposed by a specific virtual time. This requires extending `TestableObservable` and `Subscription` tracking.

### M4: Add `combineLatest` collection operator for `Infallible`

The `Infallible` trait lacks the collection-based `combineLatest` available in `Observables/CombineLatest+Collection.swift`. Implement `Infallible.combineLatest(_ collection:resultSelector:)` that combines an arbitrary collection of `Infallible` sequences while maintaining the infallible guarantee.

### M5: Implement `prefetch` operator for observable sequences

Add a `prefetch(count:)` operator that eagerly subscribes and buffers up to `count` elements before the downstream subscribes. This requires a new file in `Observables/` with a custom `Producer` subclass, buffer management with proper back-pressure, and scheduler-aware element delivery.

### M6: Add debug name propagation through operator chains

Extend the `debug()` operator in `Observables/Debug.swift` to support propagating debug identifiers through chained operators. Each operator in the chain should inherit and append its operator name to a debug path (e.g., `"source > map > filter > throttle"`), with the path accessible in `Rx.resources` for leak debugging.

### M7: Implement `RxBlocking` timeout with partial results

Extend `RxBlocking/BlockingObservable+Operators.swift` to add `toArray(timeout:partial:)` that returns elements collected before the timeout instead of throwing. This requires modifying `BlockingObservable`'s `RunLoopLock` integration to support partial result collection and adding new error types for timeout-with-partial-data.

### M8: Add `BehaviorRelay` snapshot and diff support

Extend `RxRelay/BehaviorRelay.swift` with `snapshot()` returning the current value and subscription count, and `changes()` returning an `Observable` that emits `(oldValue, newValue)` tuples. Requires adding value comparison infrastructure and thread-safe old value tracking within the relay.

### M9: Implement scheduler-aware `delay` for `Completable` trait

The `Completable` trait lacks a `delay` operator. Implement `delay(_:scheduler:)` in `Traits/PrimitiveSequence/Completable.swift` that delays the `.completed` event by the specified duration using the provided scheduler, correctly handling disposal during the delay window and error passthrough.

### M10: Add `groupBy` operator support for `Driver` trait

The `Driver` trait in `RxCocoa/Traits/` supports most operators but lacks `groupBy`. Implement `Driver.groupBy(keySelector:)` that returns `Driver<GroupedObservable<Key, Element>>`, ensuring all emissions happen on the main scheduler and errors are replaced with the `onErrorJustReturn` recovery mechanism.

### M11: Improve SwiftLint and formatting configuration

Update `.swiftlint.yml` with module-specific rules for RxSwift/, RxCocoa/, RxBlocking/, RxRelay/, and RxTest/ directories. Configure `.swiftformat` with per-directory formatting rules matching the existing code style. Update `.jazzy.yml` with complete module documentation generation settings and custom theme. Add a `Makefile` target for documentation generation and linting. Configure `mise.toml` with development tool versions. Create a `CONTRIBUTING.md` with code style guidelines referencing the linting configuration. Changes span `.swiftlint.yml`, `.swiftformat`, `.jazzy.yml`, `Makefile`, `mise.toml`, `CONTRIBUTING.md`, and `README.md`.

## Wide

### W1: Implement structured concurrency bridge for async/await

Add comprehensive `async`/`await` bridging beyond the existing `Observable+Concurrency.swift`. Implement `AsyncObservableSequence` conforming to `AsyncSequence`, `Observable.init(asyncSequence:)` for the reverse direction, `Subject.send(from:)` for async stream feeding, and `Task`-aware disposal that cancels the RxSwift subscription when the Task is cancelled. Changes span RxSwift/Concurrency/, Observable, Subjects, and DisposeBag.

### W2: Implement reactive caching layer with expiration

Add a `CachedObservable` type that wraps a source observable with configurable TTL, max-size LRU caching, and cache invalidation. Support cache key extraction via `KeyPath`, shared cache across multiple subscribers, cache prewarming, and disk persistence. Changes span new files in RxSwift/Observables/, extensions to `ObservableType`, new data structures in Platform/, and integration with schedulers for TTL expiration.

### W3: Add comprehensive operator fusion optimization

Implement operator fusion that detects and optimizes common operator chain patterns: `map.map` → single `map`, `filter.filter` → single `filter`, `observeOn.observeOn` → last `observeOn`, and `share().share()` → single `share()`. Requires modifying `Producer` in `Observables/Producer.swift`, adding a fusion protocol to `Sink`, and updating `Map`, `Filter`, `ObserveOn`, and `ShareReplayScope` to participate in fusion.

### W4: Implement distributed tracing for operator chains

Add tracing infrastructure that tracks element flow through operator chains: timestamp at each operator, processing duration, back-pressure metrics, and subscription lifecycle events. Requires adding a `Tracer` protocol, tracing hooks in `Producer.subscribe`, `Sink.forwardOn`, and `Disposable.dispose`, a `TracingScheduler` wrapper, and a reporting API. Changes span Observables/, Schedulers/, Disposables/, and new tracing module.

### W5: Add reactive state management framework

Implement `RxStore<State, Action>` as a Redux-like state container built on RxSwift: a `store.dispatch(action:)` that feeds through a `reduce(state:action:)` function, middleware support via observable transforms, `select(keyPath:)` for derived state streams with `distinctUntilChanged`, and time-travel debugging via `ReplaySubject`. Changes span new module files, integration with Subjects, operators, and RxCocoa for UI binding.

### W6: Implement comprehensive memory leak detection

Add a `LeakDetector` that tracks all RxSwift resource allocations and detects common leak patterns: retained `DisposeBag` in closures, missing disposal of subscriptions, circular references through `withLatestFrom`, and `share()` keeping upstream alive after all subscribers disconnect. Requires instrumenting `DisposeBag`, `Producer.subscribe`, `Sink`, `SubjectType`, and adding leak reporting to the `Resources` tracking system.

### W7: Add RxTest assertion DSL for complex sequence verification

Implement a fluent assertion API for `TestScheduler`: `expect(observer).toEmit([.next(200, "a"), .completed(300)])`, `expect(observable).toSubscribe(at: 200).andDispose(at: 500)`, `expect(hotObservable).toHaveSubscribers(count: 2, at: 300)`. Requires extending `TestableObserver`, `TestableObservable`, `Subscription`, adding custom assertion failure reporting, and integration with XCTest.

### W8: Implement cross-platform scheduler abstraction

Replace platform-specific scheduler implementations with a unified `SchedulerProvider` protocol supporting custom event loops. Implement providers for GCD (`DispatchQueue`), `OperationQueue`, Linux `epoll`, and a configurable thread pool. Requires refactoring `SerialDispatchQueueScheduler`, `ConcurrentDispatchQueueScheduler`, `MainScheduler`, Platform/ abstractions, and adding a provider registry.

### W9: Add backpressure support to observable sequences

Implement a backpressure mechanism for `Observable` sequences: `Flowable<Element>` type with `request(count:)` demand signaling, backpressure strategies (buffer, drop, latest, error), and integration with existing operators. Requires new protocol `FlowableType`, modifications to `Producer`/`Sink` for demand tracking, backpressure-aware versions of `Merge`, `FlatMap`, `Zip`, `CombineLatest`, and bridging operators between `Observable` and `Flowable`.

### W10: Implement reactive networking layer in RxCocoa

Extend `RxCocoa/Foundation/URLSession+Rx.swift` into a full reactive networking module: request retry with backoff, response caching with observable invalidation, request deduplication for identical in-flight requests, progress tracking as `Observable<Progress>`, multipart upload support, and automatic JSON decoding with `Codable`. Changes span RxCocoa/Foundation/, new networking files, integration with schedulers for retry timing, and RxSwift operators for deduplication logic.

### W11: Overhaul documentation site and playground examples

Comprehensively restructure the project's documentation: update all files in `Documentation/` (GettingStarted.md, Traits.md, Schedulers.md, Subjects.md, UnitTests.md, SwiftConcurrency.md, etc.) with current API examples and Swift 5.9+ syntax; refresh `Rx.playground/` examples to work with the latest Xcode; rebuild `docs/` Jazzy-generated API documentation from `.jazzy.yml`; update `README.md` with a feature comparison table, architecture overview, and migration guide from Combine; add structured changelog to `CONTRIBUTING.md`; update `CODE_OF_CONDUCT.md` to latest Contributor Covenant; and configure `Version.xcconfig` with documentation version tracking. Changes span `Documentation/`, `Rx.playground/`, `docs/`, `.jazzy.yml`, `README.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `LICENSE.md`, and `Version.xcconfig`.
