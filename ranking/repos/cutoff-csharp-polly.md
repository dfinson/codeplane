# App-vNext/Polly

| Field | Value |
|-------|-------|
| **URL** | https://github.com/App-vNext/Polly |
| **License** | BSD-3-Clause |
| **Language** | C# |
| **Scale** | Medium (multi-module project) |
| **Category** | Resilience framework |
| **Set** | Cutoff |

## Why this repo

- **Composable strategy architecture**: V8+ redesigned around
  `ResiliencePipeline` and composable strategies — each strategy
  (Retry, CircuitBreaker, Timeout, Hedging, RateLimiter, Fallback)
  is a self-contained module with options, internal state, and
  telemetry integration. Tasks require navigating across strategy
  boundaries, the pipeline builder, and the telemetry/registry layers.
- **Well-structured**: Source under `src/Polly.Core/` with clear
  per-strategy directories (`Retry/`, `CircuitBreaker/`, `Timeout/`,
  `RateLimiter/`, `Hedging/`, `Fallback/`), shared infrastructure
  in `Telemetry/` and `Registry/`, and the pipeline builder as the
  composition root.
- **Rich history**: 13K+ stars, widely adopted in .NET microservice
  architectures. Issues span correctness under concurrency, strategy
  composition edge cases, and telemetry fidelity.
- **Permissive**: BSD-3-Clause license.

## Structure overview

```
src/Polly.Core/
├── ResiliencePipeline.cs              # Main entry — Execute, ExecuteAsync
├── ResiliencePipeline.TResult.cs      # Generic result pipeline
├── ResiliencePipelineBuilder.cs       # Composition builder
├── ResiliencePipelineBuilder.TResult.cs
├── ResilienceContext.cs               # Per-execution context (state, cancellation)
├── ResilienceStrategyOptions.cs       # Base options for all strategies
├── Outcome.cs                        # Result-or-exception wrapper
├── Retry/
│   ├── RetryStrategyOptions.cs        # MaxRetryAttempts, Delay, BackoffType
│   ├── RetryResilienceStrategy.cs     # Core retry logic
│   ├── RetryConstants.cs              # Default values
│   ├── RetryHelper.cs                 # Delay calculation (exponential, jitter)
│   └── OnRetryArguments.cs            # Event arguments
├── CircuitBreaker/
│   ├── CircuitBreakerStrategyOptions.cs
│   ├── CircuitBreakerResilienceStrategy.cs
│   ├── CircuitStateController.cs      # State machine (Closed→Open→HalfOpen)
│   ├── AdvancedCircuitBreakerOptions.cs
│   ├── BrokenCircuitException.cs
│   └── CircuitBreakerManualControl.cs
├── Timeout/
│   ├── TimeoutStrategyOptions.cs
│   ├── TimeoutResilienceStrategy.cs
│   └── TimeoutRejectedException.cs
├── RateLimiter/
│   ├── RateLimiterStrategyOptions.cs
│   ├── RateLimiterResilienceStrategy.cs
│   └── RateLimiterRejectedException.cs
├── Hedging/
│   ├── HedgingStrategyOptions.cs
│   ├── HedgingResilienceStrategy.cs
│   ├── HedgingHandler.cs
│   └── HedgingActionGeneratorArguments.cs
├── Fallback/
│   ├── FallbackStrategyOptions.cs
│   ├── FallbackResilienceStrategy.cs
│   └── FallbackHandler.cs
├── Telemetry/
│   ├── TelemetryUtil.cs
│   ├── ResilienceTelemetryDiagnosticSource.cs
│   ├── TelemetryOptions.cs
│   └── ExecutionAttemptArguments.cs
├── Registry/
│   ├── ResiliencePipelineRegistry.cs
│   ├── ResiliencePipelineProvider.cs
│   └── RegistryOptions.cs
└── Utils/
    ├── TimeProvider.cs
    ├── CancellationTokenSourcePool.cs
    └── SystemClock.cs
```

## Scale indicators

- ~80–100 C# source files under src/Polly.Core/
- ~15K lines of code
- 2–3 levels of namespace nesting
- Clear per-strategy module boundaries

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix RetryHelper jitter calculation producing negative delays

`RetryHelper.GetRetryDelay()` applies jitter by subtracting a random
value from the base delay, but for small base delays with high jitter
factors, the result can go negative, producing an `ArgumentOutOfRange
Exception` in `Task.Delay()`. Fix the jitter calculation in
`RetryHelper.cs` to clamp the result to `TimeSpan.Zero` minimum.

### N2: Fix CircuitBreakerManualControl.IsolateAsync not respecting cancellation

`CircuitBreakerManualControl.IsolateAsync()` transitions the circuit
to `Isolated` state but does not check the `CancellationToken` from
the `ResilienceContext`. If the token is already cancelled, the
isolation proceeds silently instead of throwing
`OperationCanceledException`. Fix to check cancellation before state
transition.

### N3: Fix TimeoutResilienceStrategy not disposing linked CancellationTokenSource

When a timeout fires, `TimeoutResilienceStrategy` creates a linked
`CancellationTokenSource` but does not dispose it in the non-timeout
path. Under high throughput, this causes handle exhaustion. Fix the
strategy to always dispose the linked token source via `finally` or
the `CancellationTokenSourcePool`.

### N4: Fix ResilienceContext.ContinueOnCapturedContext not propagated to hedging actions

When `ResilienceContext.ContinueOnCapturedContext` is set to `false`,
the hedging strategy ignores it for spawned hedging actions, causing
them to resume on the captured `SynchronizationContext`. Fix
`HedgingResilienceStrategy.cs` to propagate the context setting to
each hedging action's inner context.

### N5: Fix RateLimiterRejectedException missing retry-after metadata

`RateLimiterRejectedException` is thrown when the rate limit is
exceeded but does not include the `RetryAfter` value from the
underlying `RateLimitLease`. Fix `RateLimiterResilienceStrategy.cs`
to read `RetryAfter` from the lease metadata and set it on the
exception.

### N6: Fix FallbackResilienceStrategy swallowing original exception stack trace

When `FallbackResilienceStrategy` catches an exception and invokes the
fallback handler, the original exception's stack trace is lost because
it is stored only as `Outcome.Exception` without preservation. Fix the
fallback to use `ExceptionDispatchInfo` to preserve the original stack
trace when re-throwing after a failed fallback.

### N7: Fix RetryStrategyOptions.OnRetry delegate receiving wrong attempt number

The `OnRetryArguments.AttemptNumber` passed to the `OnRetry` delegate
starts at 1 instead of 0 for the first retry, inconsistent with the
documentation and `MaxRetryAttempts` counting. Fix
`RetryResilienceStrategy.cs` to use zero-based attempt numbering.

### N8: Fix ResiliencePipelineBuilder.AddStrategy not validating null options

`ResiliencePipelineBuilder.AddStrategy()` accepts a `null`
`ResilienceStrategyOptions` without throwing, leading to a
`NullReferenceException` deep in the pipeline construction. Fix the
builder to validate options with `ArgumentNullException` at the
`AddStrategy` call site.

### N9: Fix CircuitStateController half-open state allowing unlimited concurrent probes

When the circuit transitions to `HalfOpen`, multiple concurrent
requests can all pass through as probe requests because the state
check is not atomic. Fix `CircuitStateController.cs` to use
`Interlocked.CompareExchange` to allow only a single probe request
in the `HalfOpen` state.

### N10: Fix TelemetryUtil not including pipeline name in enriched tags

`TelemetryUtil` emits telemetry events with strategy name and type
but omits the `ResiliencePipeline` name from the tags. When multiple
pipelines use the same strategy type, events are indistinguishable.
Fix `TelemetryUtil.cs` to include the pipeline instance name from
`ResilienceContext.Properties`.

## Medium

### M1: Add exponential backoff with decorrelated jitter to RetryHelper

Implement the "decorrelated jitter" backoff algorithm (based on the
AWS architecture blog) as a new `BackoffType.DecorrelatedJitter` option.
The algorithm tracks previous delay to compute the next, requiring
state across retries. Changes span `RetryHelper.cs` for the algorithm,
`RetryStrategyOptions.cs` for the new `BackoffType` enum value,
`RetryResilienceStrategy.cs` for state threading, and
`RetryConstants.cs` for defaults.

### M2: Implement CircuitBreaker health metrics with sliding window

Replace the simple consecutive-failure counter with a time-based
sliding window that tracks success/failure rates. Add
`AdvancedCircuitBreakerOptions.SamplingDuration` and
`MinimumThroughput`. Changes span `CircuitBreakerStrategyOptions.cs`,
`CircuitStateController.cs` for the sliding window data structure,
`CircuitBreakerResilienceStrategy.cs` for metric collection, and
`AdvancedCircuitBreakerOptions.cs` for validation.

### M3: Add ResiliencePipeline execution result metadata

Implement `ResilienceExecutionResult<T>` that wraps the outcome with
execution metadata: total duration, attempt count, strategies invoked,
circuit state consulted, and whether a fallback was used. Add
`ResiliencePipeline.ExecuteWithResult<T>()`. Changes span
`ResiliencePipeline.cs`, `ResiliencePipeline.TResult.cs`, new result
metadata classes, and each strategy for metadata Population.

### M4: Implement dynamic strategy reconfiguration at runtime

Add `ResiliencePipelineRegistry.Reconfigure<T>()` that updates a
named pipeline's strategy options without rebuilding the pipeline.
Strategy instances must support hot-swapping their options via an
`IReconfigurable<TOptions>` interface. Changes span `Registry/` for
the reconfiguration API, `ResilienceStrategyOptions.cs` for the
interface, and each strategy class (`Retry`, `CircuitBreaker`,
`Timeout`) for reconfiguration support.

### M5: Add hedging action result selection strategy

Extend `HedgingStrategyOptions` with a `ResultSelector` delegate that
chooses the best result from completed hedging actions instead of
always taking the first successful one. Support scoring functions and
quorum-based selection. Changes span `HedgingStrategyOptions.cs`,
`HedgingResilienceStrategy.cs` for parallel result collection,
`HedgingHandler.cs` for selector invocation, and
`HedgingActionGeneratorArguments.cs` for context.

### M6: Implement chaos engineering strategies (fault injection)

Add `ChaosFaultStrategy` and `ChaosLatencyStrategy` that inject
faults and delays with configurable probability and fault factories.
Support enabling/disabling via a `ChaosManager` toggle. Changes span
new `Chaos/` directory with strategy, options, and constants classes,
`ResiliencePipelineBuilder.cs` for `AddChaosFault()` and
`AddChaosLatency()` extension methods, and telemetry integration.

### M7: Add per-strategy timeout support to ResiliencePipeline

Implement a pipeline-level overall timeout that wraps all strategies,
independent of the per-operation `TimeoutResilienceStrategy`. Add
`ResiliencePipelineBuilder.WithPipelineTimeout(TimeSpan)`. Changes span
`ResiliencePipelineBuilder.cs` for the configuration,
`ResiliencePipeline.cs` for the wrapping logic,
`ResilienceContext.cs` for remaining-time tracking, and
`CancellationTokenSourcePool.cs` for linked-source management.

### M8: Implement outcome predicate composition for strategies

Add fluent predicate builders:
`RetryStrategyOptions.HandleResult<T>(Func<T, bool>)`,
`.Handle<TException>()`, `.OrResult()`, `.OrHandle()` that compose
into a single `ShouldHandle` predicate. Replace the current raw
delegate assignment. Changes span `ResilienceStrategyOptions.cs` for
the builder base, each strategy's options class for the typed
overloads, and each strategy's core class for predicate evaluation.

### M9: Add circuit breaker state change notifications via IObservable

Implement `CircuitBreakerManualControl.StateChanges` as an
`IObservable<CircuitState>` that emits on every state transition.
Support `IAsyncObserver` for async subscribers. Changes span
`CircuitBreakerManualControl.cs`, `CircuitStateController.cs` for
event firing, `CircuitBreakerResilienceStrategy.cs` for wiring, and
new observable infrastructure in `Utils/`.

### M10: Implement strategy execution weight for load balancing

Add `ResilienceStrategyOptions.Weight` property used by the pipeline
to distribute load across multiple instances of the same strategy
type. Support weighted round-robin and least-connections strategies.
Changes span `ResilienceStrategyOptions.cs` for the weight property,
`ResiliencePipelineBuilder.cs` for multi-instance registration,
`ResiliencePipeline.cs` for the selection algorithm, and telemetry
for weight-based metrics.

## Wide

### W1: Implement distributed circuit breaker with shared state

Extend `CircuitBreakerResilienceStrategy` to support distributed state
via a pluggable `ICircuitBreakerStateStore` interface with
implementations for Redis and SQL Server. Shared state must handle
concurrent transitions across processes, lease-based half-open probes,
and eventual consistency. Changes span `CircuitBreaker/` for the state
store abstraction, `CircuitStateController.cs` for distributed locking,
`Registry/` for distributed pipeline registration, `Telemetry/` for
cross-process event correlation, and new persistence infrastructure.

### W2: Add OpenTelemetry-native instrumentation throughout the pipeline

Replace `DiagnosticSource`-based telemetry with native OpenTelemetry
`Activity` and `Meter` instrumentation. Emit spans for each strategy
execution with attributes (attempt number, circuit state, delay,
outcome), counters for retries/circuit-breaks/timeouts/rate-limits,
and histograms for execution duration. Changes span every strategy
class, `Telemetry/` for the OTel integration, `ResiliencePipeline.cs`
for span lifecycle, `ResilienceContext.cs` for baggage propagation,
and `Registry/` for meter management.

### W3: Implement adaptive concurrency limiter strategy

Add `AdaptiveConcurrencyStrategy` that dynamically adjusts concurrency
limits based on latency percentiles (gradient algorithm inspired by
Netflix's adaptive concurrency library). Track in-flight requests,
p99 latency, and adjust limits using AIMD. Changes span a new
`AdaptiveConcurrency/` directory, integration with
`ResiliencePipelineBuilder.cs`, `ResilienceContext.cs` for timing,
`Telemetry/` for limit-change events, `RateLimiter/` for shared
rate-limiting infrastructure, and `Utils/` for percentile tracking.

### W4: Add resilience pipeline composition with dependency graph

Implement `ResiliencePipelineBuilder.AddDependency(pipeline)` that
models inter-pipeline dependencies (e.g., service A depends on service
B). Propagate circuit-breaker state across dependent pipelines — if
a downstream circuit opens, upstream pipelines preemptively shed load.
Changes span `ResiliencePipelineBuilder.cs`, `ResiliencePipeline.cs`
for dependency tracking, `CircuitBreaker/` for state propagation,
`Registry/` for dependency graph management, `Telemetry/` for
cascade visualization, and `Hedging/` for dependency-aware action
generation.

### W5: Implement policy-as-code with JSON/YAML configuration

Add `ResiliencePipelineBuilder.FromConfiguration(IConfiguration)` that
builds pipelines from structured configuration (JSON, YAML, or
`IConfiguration`). Support all strategy types, custom predicate
expressions, and hot-reload via `IOptionsMonitor`. Changes span a new
`Configuration/` directory, `ResiliencePipelineBuilder.cs` for the
configuration binding, each strategy's options class for serialization
attributes, `Registry/` for configuration-based pipeline registration,
and validation logic for configuration schema.

### W6: Add request hedging with circuit-breaker-aware endpoint selection

Extend the hedging strategy to receive a list of endpoints and select
which ones to hedge against based on per-endpoint circuit breaker
state. Endpoints with open circuits are deprioritized. Add
`HedgingStrategyOptions.EndpointSelector` and per-endpoint health
tracking. Changes span `Hedging/` for the endpoint model and selector,
`CircuitBreaker/` for per-endpoint state, `Registry/` for endpoint
registration, `Telemetry/` for per-endpoint metrics, and
`ResiliencePipeline.cs` for endpoint-aware execution.

### W7: Implement resilience testing framework

Add `Polly.Testing` utilities: `ResiliencePipelineAssert` for verifying
pipeline composition (assert retry count, circuit threshold, timeout
value), `SimulatedClock` for deterministic time control,
`FaultInjectionContext` for scripted failure sequences, and
`TelemetryCollector` for asserting on emitted events. Changes span a
new `Testing/` directory, integration with `Telemetry/` for event
capture, `Utils/TimeProvider.cs` for clock injection, each strategy
for inspectable state, and `ResiliencePipeline.cs` for composition
introspection.

### W8: Add service mesh integration layer

Implement `Polly.Mesh` that bridges Polly resilience pipelines with
service mesh policies (Istio, Linkerd): sync circuit breaker state
with mesh-level outlier detection, respect mesh-injected retry budgets,
and propagate resilience headers (`x-retry-attempt`, `x-circuit-state`).
Changes span a new `Mesh/` integration layer, `ResilienceContext.cs`
for header propagation, `CircuitBreaker/` for external state sync,
`Retry/` for budget awareness, `Hedging/` for mesh endpoint discovery,
and `Telemetry/` for mesh-correlated spans.

### W9: Implement multi-tier resilience pipeline (inner/outer/cross-cutting)

Add `ResiliencePipelineBuilder.WrapWith()` composition that explicitly
models inner (per-call), outer (per-service), and cross-cutting
(global) resilience tiers. Each tier has its own telemetry scope and
can override or augment strategies from other tiers. Support
tier-aware circuit breaker aggregation. Changes span
`ResiliencePipelineBuilder.cs` for tier modeling,
`ResiliencePipeline.cs` for nested execution, every strategy for
tier-aware context, `Telemetry/` for per-tier metrics, and
`Registry/` for tiered pipeline management.

### W10: Add persistent execution log with replay capability

Implement `ResiliencePipeline.ExecuteWithLog<T>()` that persists every
execution attempt, outcome, delay, and strategy decision to a durable
log. Add `ExecutionLog.Replay()` that re-executes a logged sequence
for debugging. Support pluggable storage (file, database, event hub).
Changes span new `ExecutionLog/` infrastructure, integration with
`ResiliencePipeline.cs` and each strategy for event recording,
`ResilienceContext.cs` for log correlation, `Telemetry/` for log-based
metrics reconstruction, and `Registry/` for log configuration.
