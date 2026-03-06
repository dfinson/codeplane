# jbogard/MediatR

| Field | Value |
|-------|-------|
| **URL** | https://github.com/jbogard/MediatR |
| **License** | Apache-2.0 |
| **Language** | C# |
| **Scale** | Small |
| **Category** | Mediator pattern for .NET |
| **Set** | Cutoff |
| **Commit** | `6a1bf54413124866b5c8647ce42eb5901c93b7b9` |

## Why this repo

- **Well-structured**: Clean separation — `MediatR.Contracts` (core
  abstractions: `IRequest<T>`, `IRequest`, `INotification`,
  `IStreamRequest<T>`, `Unit`), and `MediatR` (implementation:
  `Mediator`, `ISender`, `IPublisher`, pipeline behaviors, notification
  publishers, request/stream handler wrappers, DI registration, and
  licensing). Pipeline behavior architecture with clear pre/post
  processor and exception handler stages.
- **Rich history**: 11K+ stars. The standard mediator/CQRS library for
  .NET, used extensively in enterprise applications. Issues span
  pipeline ordering, DI registration edge cases, stream request
  handling, notification publishing strategies, and generic type
  resolution.
- **Permissive**: Apache-2.0 licensed.

## Structure overview

```
MediatR/
├── MediatR.slnx                                       # Solution file
├── Directory.Build.props                              # Shared MSBuild properties
├── src/
│   ├── MediatR.Contracts/
│   │   ├── IRequest.cs                                # IRequest<TResponse> and IRequest (void) marker interfaces
│   │   ├── INotification.cs                           # INotification marker interface for pub/sub
│   │   ├── IStreamRequest.cs                          # IStreamRequest<TResponse> for IAsyncEnumerable streaming
│   │   └── Unit.cs                                    # Unit type for void-returning requests
│   └── MediatR/
│       ├── Mediator.cs                                # Core Mediator — Send, Publish, CreateStream via reflection + caching
│       ├── IMediator.cs                               # IMediator = ISender + IPublisher combined interface
│       ├── ISender.cs                                 # ISender — Send<TResponse>, Send(object)
│       ├── IPublisher.cs                              # IPublisher — Publish<TNotification>, Publish(object)
│       ├── IRequestHandler.cs                         # IRequestHandler<TRequest, TResponse> handler interface
│       ├── INotificationHandler.cs                    # INotificationHandler<TNotification> handler interface
│       ├── IStreamRequestHandler.cs                   # IStreamRequestHandler<TRequest, TResponse> for streaming
│       ├── IPipelineBehavior.cs                       # IPipelineBehavior<TRequest, TResponse> — middleware pipeline
│       ├── IStreamPipelineBehavior.cs                 # IStreamPipelineBehavior for stream request pipelines
│       ├── INotificationPublisher.cs                  # Strategy interface for publishing notifications
│       ├── NotificationHandlerExecutor.cs             # Wraps handler + callback for notification dispatch
│       ├── TypeForwardings.cs                         # Assembly-level type forwarding declarations
│       ├── Wrappers/
│       │   ├── RequestHandlerWrapper.cs               # Wraps IRequestHandler resolution + pipeline aggregation
│       │   ├── NotificationHandlerWrapper.cs          # Wraps INotificationHandler resolution + dedup by type
│       │   └── StreamRequestHandlerWrapper.cs         # Wraps IStreamRequestHandler + stream pipeline aggregation
│       ├── Pipeline/
│       │   ├── IRequestPreProcessor.cs                # Pre-processing interface before handler execution
│       │   ├── IRequestPostProcessor.cs               # Post-processing interface after handler execution
│       │   ├── RequestPreProcessorBehavior.cs         # Pipeline behavior that invokes all pre-processors
│       │   ├── RequestPostProcessorBehavior.cs        # Pipeline behavior that invokes all post-processors
│       │   ├── IRequestExceptionHandler.cs            # Exception handler interface for request failures
│       │   ├── IRequestExceptionAction.cs             # Exception action interface (fire-and-forget on error)
│       │   ├── RequestExceptionProcessorBehavior.cs   # Pipeline behavior for exception handlers
│       │   ├── RequestExceptionActionProcessorBehavior.cs # Pipeline behavior for exception actions
│       │   └── RequestExceptionHandlerState.cs        # Mutable state for exception handler flow control
│       ├── NotificationPublishers/
│       │   ├── ForeachAwaitPublisher.cs               # Sequential notification publishing (foreach + await)
│       │   └── TaskWhenAllPublisher.cs                # Parallel notification publishing (Task.WhenAll)
│       ├── MicrosoftExtensionsDI/
│       │   ├── MediatRServiceCollectionExtensions.cs  # AddMediatR IServiceCollection extension methods
│       │   ├── MediatrServiceConfiguration.cs         # Configuration — assembly scanning, lifetime, behavior registration
│       │   └── RequestExceptionActionProcessorStrategy.cs # Strategy enum for exception action processing
│       ├── Registration/
│       │   └── ServiceRegistrar.cs                    # Assembly scanning and handler registration logic
│       ├── Internal/
│       │   ├── HandlersOrderer.cs                     # Orders handlers by registration priority
│       │   └── ObjectDetails.cs                       # Internal helper for object type metadata
│       ├── Entities/
│       │   └── OpenBehavior.cs                        # Represents an open generic behavior registration
│       └── Licensing/
│           ├── License.cs                             # License model
│           ├── LicenseAccessor.cs                     # License key accessor
│           ├── LicenseValidator.cs                    # License validation logic
│           ├── Edition.cs                             # License edition enum
│           ├── ProductType.cs                         # Product type enum
│           └── BuildInfo.cs                           # Build metadata
├── test/
│   ├── MediatR.Tests/                                 # Unit tests — handler resolution, pipeline, exceptions
│   ├── MediatR.Benchmarks/                            # BenchmarkDotNet performance benchmarks
│   └── MediatR.DependencyInjectionTests/              # DI container integration tests (Autofac, DryIoc, etc.)
└── samples/                                           # Sample applications
```

## Scale indicators

- ~2 projects, ~42 C# source files in src/
- ~11,200 lines total (including tests)
- .NET solution with NuGet package structure
- Dependencies: `Microsoft.Extensions.DependencyInjection.Abstractions`

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix Mediator.Send(object) not propagating CancellationToken to the pipeline

In `src/MediatR/Mediator.cs`, the `Send(object request, CancellationToken)`
method resolves the wrapper via `_requestHandlers.GetOrAdd` and calls
`handler.Handle(request, _serviceProvider, cancellationToken)`. However,
in `Wrappers/RequestHandlerWrapper.cs`, the `RequestHandlerWrapperImpl<TRequest, TResponse>.Handle(object, ...)`
overload calls `Handle((IRequest<TResponse>)request, serviceProvider, cancellationToken)`
which constructs a `Handler` delegate. The inner `Handler` delegate uses
`t == default ? cancellationToken : t` comparison, but when `t` is
`CancellationToken.None` (the default parameter value from the delegate
signature), it incorrectly falls back to the outer token even when the
caller explicitly passed `CancellationToken.None`. Fix the delegate
parameter handling to distinguish between "not provided" and "explicitly
None".

### N2: Fix NotificationHandlerWrapperImpl dedup logic removing legitimate multiple registrations

In `src/MediatR/Wrappers/NotificationHandlerWrapper.cs`,
`NotificationHandlerWrapperImpl<TNotification>.Handle` groups handlers
by `GetType()` and takes `First()` from each group. This deduplication
removes intentional multiple registrations of the same handler type
with different constructor parameters (e.g., via factory registrations
with different configuration). Fix the dedup to only remove exact
reference duplicates using `Distinct()` with `ReferenceEqualityComparer`
instead of grouping by type.

### N3: Fix RequestExceptionHandlerState not resetting when exception handler re-throws

In `src/MediatR/Pipeline/RequestExceptionHandlerState.cs`, the `Handled`
property is set to `true` when an exception handler calls `SetHandled(response)`.
In `RequestExceptionProcessorBehavior.cs`, if the handler sets `Handled`
but then the response processing throws a second exception, the state
remains `Handled = true` and the outer behavior does not re-process.
Fix `RequestExceptionProcessorBehavior` to reset `Handled` to `false`
when the handled response itself throws.

### N4: Fix static ConcurrentDictionary caches in Mediator causing memory leaks in test scenarios

In `src/MediatR/Mediator.cs`, `_requestHandlers`, `_notificationHandlers`,
and `_streamRequestHandlers` are `static readonly ConcurrentDictionary`
fields. In test scenarios where `IServiceProvider` is rebuilt per test,
the cached wrapper instances hold references to stale type metadata.
Fix by making the dictionaries instance-level or adding a `ClearCache()`
static method, and update `MediatRServiceCollectionExtensions` to call
it during registration.

### N5: Fix TaskWhenAllPublisher swallowing individual handler exceptions

In `src/MediatR/NotificationPublishers/TaskWhenAllPublisher.cs`, the
`Publish` method calls `Task.WhenAll(tasks)` which throws an
`AggregateException` containing only the first exception when multiple
handlers fail. The other exceptions are lost. Fix `Publish` to catch
the `AggregateException`, unwrap all inner exceptions via `Flatten()`,
and throw an `AggregateException` with all original exceptions preserved.

### N6: Fix HandlersOrderer not preserving registration order for handlers with equal priority

In `src/MediatR/Internal/HandlersOrderer.cs`, the ordering logic does
not use a stable sort, so handlers with the same priority may execute
in non-deterministic order. Fix `HandlersOrderer` to use a stable
sorting algorithm (e.g., `OrderBy` with index as tiebreaker) to
preserve the DI registration order for same-priority handlers.

### N7: Fix ServiceRegistrar not scanning handlers from referenced assemblies transitively

In `src/MediatR/Registration/ServiceRegistrar.cs`, the assembly scanning
only processes explicitly provided assemblies. Handlers defined in
assemblies referenced by the provided assemblies but not listed
themselves are silently missed. Fix `ServiceRegistrar` to optionally
scan referenced assemblies when configured via
`MediatrServiceConfiguration.ScanReferencedAssemblies`.

### N8: Fix IStreamPipelineBehavior not receiving CancellationToken propagated from CreateStream

In `src/MediatR/Wrappers/StreamRequestHandlerWrapper.cs`, the
`StreamRequestHandlerWrapperImpl` aggregates stream pipeline behaviors
using `Aggregate`. The `NextWrapper` method accepts
`CancellationToken` via `[EnumeratorCancellation]`, but the token
from `CreateStream` is only passed to the outermost iteration
`items.WithCancellation(cancellationToken)`. Inner pipeline behaviors
receive the token from their own `Handle` parameter which may differ
from the enumeration token. Fix the aggregate chain to consistently
pass the same cancellation token through all pipeline layers.

### N9: Fix Mediator.Publish not awaiting handlers when the notification type is an interface

In `src/MediatR/Mediator.cs`, `Publish(object notification, ...)` casts
to `INotification` and calls `PublishNotification`. The wrapper resolves
handlers for the concrete notification type via
`GetServices<INotificationHandler<TNotification>>()`. Handlers registered
against a base interface (e.g., `INotificationHandler<IMyNotification>`)
are not resolved when the concrete type is dispatched. Fix the handler
resolution to also resolve handlers registered for interface types
implemented by the notification.

### N10: Fix OpenBehavior registration allowing non-generic types to be registered as open behaviors

In `src/MediatR/Entities/OpenBehavior.cs` and
`MicrosoftExtensionsDI/MediatrServiceConfiguration.cs`, `AddOpenBehavior`
accepts any `Type` without validating it is an open generic type
implementing `IPipelineBehavior<,>`. Non-generic types silently fail at
runtime during resolution. Fix `AddOpenBehavior` to validate that the
type has open generic parameters and implements the expected behavior
interface, throwing `ArgumentException` with a descriptive message.

## Medium

### M1: Implement timeout pipeline behavior with configurable per-request timeouts

Add a `TimeoutBehavior<TRequest, TResponse>` that wraps handler
execution with a configurable timeout. Requires a new `ITimeoutRequest`
marker interface in `MediatR.Contracts`, `TimeoutBehavior.cs` in
`Pipeline/`, registration support in `MediatrServiceConfiguration.cs`,
`CancellationTokenSource` integration with the handler delegate,
timeout exception type, and configuration via attribute or interface.

### M2: Add request validation pipeline with FluentValidation integration

Implement `ValidationBehavior<TRequest, TResponse>` that resolves
`IValidator<TRequest>` from DI and validates before handler execution.
Requires `ValidationBehavior.cs` in `Pipeline/`, `ValidationException.cs`
with structured error details, `IValidator<T>` abstraction interface,
registration in `MediatrServiceConfiguration` via `AddValidationBehavior`,
configuration for fail-fast vs. collect-all-errors mode, and
`ServiceRegistrar` updates for validator scanning.

### M3: Implement notification filtering with predicate-based handler selection

Add `INotificationFilter<TNotification>` that allows handlers to
declare predicates for whether they should receive a notification.
Requires a new filter interface, filter resolution in
`NotificationHandlerWrapperImpl`, `MediatrServiceConfiguration`
options for filter registration, a `FilteredNotificationPublisher`
decorator, integration with both `ForeachAwaitPublisher` and
`TaskWhenAllPublisher`, and service registration updates.

### M4: Add request/response logging pipeline behavior with structured output

Implement `LoggingBehavior<TRequest, TResponse>` that logs request
type, handler duration, response type, and exceptions using
`ILogger<T>`. Requires `LoggingBehavior.cs` in `Pipeline/`,
`IRequestLogger<TRequest>` for custom log formatting,
`MediatrServiceConfiguration` options for log level and serialization,
sensitive data masking via `[Sensitive]` attribute, and registration
in `ServiceRegistrar`.

### M5: Implement retry pipeline behavior with configurable retry policies

Add `RetryBehavior<TRequest, TResponse>` that retries failed handler
execution with configurable retry count, delay, and exception filter.
Requires `RetryBehavior.cs` in `Pipeline/`, `IRetryableRequest`
interface with `RetryCount` and `RetryDelay` properties,
`RetryPolicy.cs` for policy configuration, exponential backoff
support, circuit-breaker integration point, and registration via
`MediatrServiceConfiguration.AddRetryBehavior`.

### M6: Add mediator-scoped request context for cross-cutting data

Implement `IMediatorContext` that provides a dictionary-like context
flowing through the entire pipeline for a single `Send`/`Publish`
call. Requires `IMediatorContext.cs` interface, `MediatorContext.cs`
implementation with `AsyncLocal<T>` storage, injection into pipeline
behaviors via DI, context population in `Mediator.Send/Publish`,
cleanup after completion, and access from handlers and processors.

### M7: Implement notification channel partitioning for selective publishing

Add `INotificationChannel` that allows notifications to be published
to named channels, with handlers subscribing to specific channels.
Requires `INotificationChannel.cs`, a `[Channel("name")]` attribute
for handlers, channel-aware resolution in
`NotificationHandlerWrapperImpl`, `Publish(notification, channel)`
overload on `IPublisher`, and `MediatrServiceConfiguration` for
channel registration.

### M8: Add compile-time source generator for handler registration

Implement a Roslyn source generator that generates `AddMediatR`
registration code at compile time instead of runtime assembly
scanning. Requires a new `MediatR.Generators` project, a
`[GenerateRegistration]` attribute, source generator for scanning
`IRequestHandler<,>` and `INotificationHandler<>` implementations,
generated `AddMediatRGenerated()` extension method, and integration
tests comparing generated output with runtime scanning.

### M9: Implement request deduplication pipeline behavior

Add `DeduplicationBehavior<TRequest, TResponse>` that prevents
duplicate concurrent requests from being processed using a
`ConcurrentDictionary` keyed by request hash. Requires
`DeduplicationBehavior.cs` in `Pipeline/`, `IDeduplicatable`
interface with `DeduplicationKey` property, response caching for
duplicate requests, TTL-based cache expiry, and configuration via
`MediatrServiceConfiguration`.

### M10: Add distributed tracing integration with Activity/OpenTelemetry

Implement `TracingBehavior<TRequest, TResponse>` that creates
`System.Diagnostics.Activity` spans for each request/notification.
Requires `TracingBehavior.cs` in `Pipeline/`, `ActivitySource`
setup in `MediatRServiceCollectionExtensions`, span attributes for
request type, handler type, and duration, exception recording,
notification publish spans, and configuration via
`MediatrServiceConfiguration.EnableTracing`.

## Wide

### W1: Implement saga/process manager pattern on top of MediatR

Build a `MediatR.Sagas` package that orchestrates multi-step
workflows: define sagas as state machines with `ISaga<TState>`,
step handlers, compensation logic, and timeout-based deadlines.
Changes span a new project with `ISaga<T>`, `SagaContext`,
`SagaOrchestrator`, state persistence interface, step execution
via `ISender`, compensation pipeline, DI registration, saga
discovery via assembly scanning, and integration tests with
multi-step scenarios.

### W2: Implement event sourcing integration with MediatR notifications

Build `MediatR.EventSourcing` that captures domain events as
notifications, stores them in an event store, and supports replay.
Changes span a new project with `IEventStore`, `EventStream`,
`AggregateRoot<T>` base class publishing events via `IPublisher`,
projection rebuilding via `CreateStream`, snapshot support,
optimistic concurrency, DI registration, and integration tests
with in-memory event store.

### W3: Implement multi-tenant mediator with tenant-scoped handler resolution

Build `MediatR.MultiTenancy` that resolves handlers per tenant:
tenant identification, tenant-scoped DI containers, per-tenant
pipeline configuration, and tenant context propagation. Changes
span a new project with `ITenantAccessor`, `TenantMediator`,
tenant-scoped `IServiceProvider` factory, `TenantPipelineBehavior`,
per-tenant handler registration, `MediatrServiceConfiguration`
extensions, and integration tests with multiple tenants.

### W4: Implement request batching and bulk processing pipeline

Build a batching system where multiple `IRequest<T>` of the same type
are collected and dispatched to a bulk handler. Changes span
`IBatchHandler<TRequest, TResponse>`, `BatchingMediator` decorator,
configurable batch window (count + time), batch pipeline behaviors,
result mapping back to individual callers, cancellation support,
DI registration, and benchmarks comparing batched vs. individual
processing.

### W5: Implement MediatR-based job queue with background processing

Build `MediatR.BackgroundJobs` that enqueues requests for
background processing: `ISender.Enqueue(request)` serializes the
request, stores in a durable queue, and a hosted service dequeues
and dispatches via `Mediator.Send`. Changes span a new project with
`IJobQueue`, `JobQueueHostedService`, request serialization,
retry with dead-letter queue, `IJobScheduler` for delayed/recurring
jobs, DI registration, and integration tests with in-memory queue.

### W6: Implement cross-process mediator communication via message bus

Build `MediatR.MessageBus` that routes requests/notifications across
process boundaries via a message bus abstraction. Changes span a new
project with `IMessageBus`, `RemoteMediatorProxy`, serialization
layer, request-response correlation, notification fan-out, transport
abstractions (in-memory, RabbitMQ), error propagation across
processes, DI registration, and integration tests with two
service instances.

### W7: Implement mediator-integrated caching with cache invalidation pipeline

Build `MediatR.Caching` with `ICacheableRequest<T>` for automatic
response caching and `ICacheInvalidation` for invalidation triggers.
Changes span a new project with `CacheBehavior<TRequest, TResponse>`,
`IDistributedCache` integration, `ICacheableRequest` interface with
cache key and TTL, `CacheInvalidationBehavior` triggered by specific
requests, cache stampede prevention with `SemaphoreSlim`, DI
registration, and integration tests.

### W8: Implement authorization pipeline with policy-based access control

Build `MediatR.Authorization` that enforces authorization policies
before handler execution. Changes span a new project with
`IAuthorizationRequirement`, `AuthorizationBehavior<TRequest, TResponse>`,
`[Authorize(Policy)]` attribute, policy resolver from DI,
`ICurrentUser` abstraction, claim-based and role-based checks,
`UnauthorizedException` with policy details, DI registration,
and integration tests with mock user contexts.

### W9: Implement mediator testing framework with handler mocking and pipeline verification

Build `MediatR.Testing` that provides test utilities: `MockMediator`
for capturing sent requests, `TestPipeline<T>` for verifying
behavior execution order, `HandlerAssertions` for asserting handler
invocations, fake notification publisher for capturing published
events. Changes span a new project with `MockMediator`, `HandlerSpy`,
`PipelineRecorder`, assertion helpers, `TestServiceProvider`,
Moq/NSubstitute integration, and documentation.

### W10: Implement GraphQL integration with MediatR handlers as resolvers

Build `MediatR.GraphQL` that maps MediatR request/response types to
GraphQL queries/mutations: `[GraphQLQuery]` and `[GraphQLMutation]`
attributes on request types, automatic schema generation from handler
registrations, DataLoader integration via batching, subscription
support via `INotification`. Changes span a new project with
schema builder, attribute processing, resolver adapter, subscription
mapper, DI registration in GraphQL server, and integration tests
with schema introspection.
