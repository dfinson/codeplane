# trpc/trpc

| Field | Value |
|-------|-------|
| **URL** | https://github.com/trpc/trpc |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Large |
| **Category** | End-to-end typesafe API framework |
| **Set** | Cutoff |

## Why this repo

- **Well-structured**: Monorepo under `packages/` with clear boundaries —
  `server/` (router, procedure, middleware, context), `client/` (client
  creation, links, request batching), `react-query/` (React hooks for
  queries/mutations/subscriptions), `next/` (Next.js adapter with SSR
  support), and shared type infrastructure. Each package has its own
  entry point and internal module hierarchy.
- **Rich history**: 4K+ commits, 34K+ stars. The dominant typesafe RPC
  framework for the TypeScript ecosystem. PRs cover link architecture,
  serialization improvements, framework adapter additions, and
  subscription transport layers.
- **Deep type-level programming**: Extensive use of conditional types,
  mapped types, and template literal types to infer procedure contracts
  at compile time — making it an excellent example of advanced
  TypeScript patterns.
- **Permissive**: MIT license.

## Structure overview

```
packages/
├── server/
│   ├── src/
│   │   ├── core/
│   │   │   ├── router.ts          # Router — merges procedures, namespacing
│   │   │   ├── procedure.ts       # Procedure builder — input/output/query/mutation
│   │   │   ├── middleware.ts      # Middleware — context extensions, authorization
│   │   │   ├── parser.ts         # Input/output schema validation (Zod, etc.)
│   │   │   └── types.ts          # Core type definitions (ProcedureRecord, etc.)
│   │   ├── adapters/
│   │   │   ├── standalone.ts     # Standalone HTTP server adapter
│   │   │   ├── fetch.ts          # Fetch API adapter (Cloudflare Workers, etc.)
│   │   │   ├── express.ts        # Express.js middleware adapter
│   │   │   ├── fastify.ts        # Fastify plugin adapter
│   │   │   └── ws.ts             # WebSocket adapter for subscriptions
│   │   ├── http/
│   │   │   ├── resolveResponse.ts # HTTP request → procedure call resolution
│   │   │   ├── contentType.ts    # Content type negotiation (JSON, FormData)
│   │   │   └── batchStreamFormatter.ts # Streaming batch response formatting
│   │   ├── observable.ts         # Observable pattern for subscriptions
│   │   ├── subscription.ts       # Subscription lifecycle management
│   │   └── initTRPC.ts           # initTRPC — factory for creating router/procedure
│   └── package.json
├── client/
│   ├── src/
│   │   ├── createTRPCClient.ts   # createTRPCClient — typed client factory
│   │   ├── links/
│   │   │   ├── httpBatchLink.ts  # Batching HTTP link (combines requests)
│   │   │   ├── httpLink.ts       # Single-request HTTP link
│   │   │   ├── wsLink.ts         # WebSocket link for subscriptions
│   │   │   ├── splitLink.ts      # Conditional link routing
│   │   │   ├── loggerLink.ts     # Debug logging link
│   │   │   └── types.ts          # Link and operation types
│   │   ├── internals/
│   │   │   ├── TRPCUntypedClient.ts # Runtime client without type inference
│   │   │   └── dataLoader.ts     # Request batching and deduplication
│   │   └── index.ts              # Public exports
│   └── package.json
├── react-query/
│   ├── src/
│   │   ├── createTRPCReact.ts    # createTRPCReact — React hooks factory
│   │   ├── shared/
│   │   │   ├── hooks/
│   │   │   │   ├── createHooksInternal.ts  # useQuery, useMutation, etc.
│   │   │   │   └── createRootHooks.ts      # Provider and context hooks
│   │   │   ├── proxy/
│   │   │   │   └── decorationProxy.ts      # Proxy-based type-safe API access
│   │   │   └── queryClient.ts              # QueryClient integration
│   │   ├── internals/
│   │   │   └── context.ts                  # React context for tRPC client
│   │   └── index.ts
│   └── package.json
├── next/
│   ├── src/
│   │   ├── app-dir/
│   │   │   ├── server.ts         # RSC (React Server Components) caller
│   │   │   └── client.ts         # Client-side hooks for App Router
│   │   ├── withTRPC.ts           # HOC for Pages Router SSR/SSG
│   │   ├── createTRPCNext.ts     # Next.js-specific tRPC client factory
│   │   └── ssrPrepass.ts         # SSR data prefetching
│   └── package.json
└── core/                          # (internal) Shared type utilities
    ├── src/
    │   ├── transformer.ts        # Data serialization (superjson, etc.)
    │   ├── rpc.ts                # RPC envelope types (request/response)
    │   ├── error.ts              # TRPCError — typed error hierarchy
    │   └── types.ts              # Shared utility types
    └── package.json
```

## Scale indicators

- ~5 publishable packages under `packages/`
- ~25K lines of TypeScript source (excluding tests)
- Deep type-level inference spanning router → client → React hooks
- Adapter layer supporting 5+ server frameworks

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix httpBatchLink not setting Content-Type header for GET batch requests

When `httpBatchLink` constructs a GET request with batched query
parameters, it does not set a `Content-Type` header on the request.
Some CDN or proxy configurations reject requests without an explicit
`Content-Type`. Fix the request construction in `httpBatchLink.ts` to
set `Content-Type: application/json` on GET batch requests so they are
not stripped or rejected by intermediate infrastructure.

### N2: Fix middleware context type not narrowing after validation

When a middleware calls `next()` with an extended context
(`next({ ctx: { user } })`), the `ctx` type in subsequent middleware
in the chain does not include the added properties. The issue is in
the `middleware.ts` type inference for chained `next()` calls. Fix the
generic parameter propagation so the returned context type merges the
extension with the existing context.

### N3: Fix TRPCError not serializing cause property across the wire

When a `TRPCError` is constructed with a `cause` (original error), the
cause is lost during JSON serialization in the HTTP response because
`JSON.stringify` does not serialize Error objects. Fix the error
serialization in `resolveResponse.ts` to include a serialized
representation of the `cause` (message, stack) in the error envelope,
and update client-side error parsing to reconstruct it.

### N4: Add operation metadata passthrough to splitLink branches

When `splitLink` routes an operation to a branch, any metadata added
by prior links (e.g., timing marks or correlation IDs stored in
`op.context`) is passed through. However, there is no mechanism for
branch links to signal back metadata (e.g., cache-hit status) to
downstream links after `splitLink`. Add a `postProcess` callback option
to `splitLink.ts` that receives the operation result and the selected
branch identifier, allowing callers to annotate results with routing
metadata.

### N5: Add structured logging format option to loggerLink

The `loggerLink` in `loggerLink.ts` formats log output for human
consumption using CSS or ANSI coloring, but does not support a
structured JSON format suitable for log aggregation services like
Datadog or CloudWatch. Add a `format: 'structured'` option to
`LoggerLinkOptions` that outputs each operation as a single JSON line
with fields for `type`, `path`, `direction`, `elapsedMs`, and
`status`.

### N6: Fix subscription cleanup not awaiting async teardown functions

When a WebSocket subscription is ended (client disconnects or calls
`unsubscribe()`), the server-side subscription teardown function
returned from the subscription handler is called but not awaited. If
the teardown is async (e.g., closing a database cursor), it may not
complete before the connection is closed. Fix the subscription lifecycle
in `subscription.ts` to properly await async teardown functions.

### N7: Fix dataLoader not deduplicating identical concurrent requests

The `dataLoader` in the client internals batches requests into a single
HTTP call but does not deduplicate identical operations (same path and
input) within the same batch. Two components calling the same query
simultaneously produce two entries in the batch instead of sharing one.
Fix the batching logic in `dataLoader.ts` to deduplicate by operation
key and fan out the result to all callers.

### N8: Add content type validation for procedure input in resolveResponse

The server's `contentType.ts` module parses request bodies based on
detected content types but does not validate that the parsed input
conforms to the procedure's expected input schema before dispatching.
When a request arrives with a valid `Content-Type` but malformed body
(e.g., truncated JSON), the error surfaces as a generic parse failure
rather than a typed `TRPCError`. Add early schema pre-validation in
`resolveResponse.ts` that catches malformed inputs and returns a
`BAD_REQUEST` error with the specific parsing failure details.

### N9: Fix createTRPCReact proxy not supporting Symbol.iterator access

When user code accidentally spreads a tRPC proxy object
(`{ ...trpc.user }`) or passes it to a function expecting an iterable,
the proxy throws an opaque error because `Symbol.iterator` access is
not handled by the decoration proxy. Fix `decorationProxy.ts` to return
`undefined` for well-known Symbol accesses instead of constructing
a procedure path.

### N10: Add static props merging to withTRPC for custom App properties

The `withTRPC()` HOC in `withTRPC.tsx` wraps a Next.js App or Page
component and sets up tRPC providers, but it does not forward custom
static properties (e.g., `getLayout`, `authenticate`) from the wrapped
component to the wrapper. Add a static property copying step to
`withTRPC` using `hoist-non-react-statics` semantics, ensuring that
custom static methods on the wrapped component are accessible on the
returned component.

## Medium

### M1: Implement request-level caching in httpBatchLink

Add a `cache` option to `httpBatchLink` that caches query results by
operation path and serialized input. Support configurable TTL, max
cache size, and stale-while-revalidate semantics. Requires changes to
`httpBatchLink.ts` for cache lookup/store, a new cache storage
abstraction in `client/src/internals/`, and integration with the link
chain so downstream links can signal cache invalidation on mutations.

### M2: Add request deduplication to httpLink for concurrent identical queries

When multiple components simultaneously call the same query procedure
with identical input, `httpLink.ts` sends separate HTTP requests for
each. Implement request deduplication that detects concurrent identical
operations (same path and serialized input) and shares a single
in-flight request across all callers. Requires a deduplication cache
in `httpLink.ts` keyed by operation path and input hash, a reference
counting mechanism in `client/src/internals/`, proper error fan-out
when the shared request fails, and cache cleanup after the response
is delivered.

### M3: Implement procedure-level rate limiting middleware

Add a `rateLimitMiddleware({ windowMs, max, keyBy })` factory that
limits the number of calls to a procedure within a time window, keyed
by a configurable function (e.g., by user ID or IP). Requires a new
middleware module under `server/src/`, a pluggable store interface
(in-memory default, Redis adapter), proper `TRPCError` with
`TOO_MANY_REQUESTS` code, and `Retry-After` header propagation through
the HTTP adapter layer.

### M4: Add client-side response caching with cache tags

Implement a `cacheLink` that sits in the client link chain and caches
query results by operation path and serialized input. Support
configurable TTL, maximum cache size, and tag-based invalidation
(mutations can declare which cache tags they invalidate). Requires a
new `cacheLink.ts` in `client/src/links/`, a `CacheStore` abstraction
in `client/src/internals/`, tag registration on the procedure proxy,
and integration with httpBatchLink to skip cached operations in
outgoing batches.

### M5: Implement typed error handling with discriminated union error types

Add the ability to define custom error types per procedure using a
discriminated union pattern: `procedure.error(z.union([authError, validationError]))`. The error type should flow through to the client so
that `error.data` is typed with the procedure's declared error shape.
Requires changes to the procedure builder, the error serialization in
`resolveResponse.ts`, the client error parsing, and React Query's
`onError` type inference.

### M6: Add request batching to wsLink for subscription operations

Currently `wsLink` sends each subscription as an individual WebSocket
message. Implement message batching that groups multiple subscription
start/stop messages sent within the same tick into a single WebSocket
frame. Requires changes to `wsLink.ts` for message buffering, the
server-side `ws.ts` adapter for batch message parsing, and the
`batchStreamFormatter.ts` for WebSocket batch envelope formatting.

### M7: Add request timeout support to httpLink and httpBatchLink

Neither `httpLink` nor `httpBatchLink` provide a built-in timeout
mechanism for requests that hang indefinitely. Add a `timeoutMs` option
to both links that aborts the fetch request via `AbortController` after
the specified duration and returns a `TRPCClientError` with a
`TIMEOUT` code. Requires changes to `httpLink.ts` and
`httpBatchLink.ts` for signal management, a shared timeout utility in
`client/src/internals/`, proper handling of partial batch timeouts
(abort only timed-out operations), and type updates for the new error
code.

### M8: Add OpenAPI schema generation from tRPC router definitions

Implement `generateOpenAPIDocument(router)` that introspects a tRPC
router and produces an OpenAPI 3.1 JSON schema. Map queries to GET
endpoints, mutations to POST endpoints, extract Zod schemas as JSON
Schema components, and include error response schemas. Requires a new
module under `server/src/`, router introspection utilities, Zod-to-
JSON-Schema conversion, and proper handling of nested routers as
path prefixes.

### M9: Add prefetch utilities for React Server Components

The `react-query` package provides hooks for client-side data fetching
but lacks dedicated utilities for prefetching tRPC queries in React
Server Components. Add `prefetchQuery(router, path, input)` and
`prefetchInfiniteQuery(router, path, input)` functions to
`react-query/src/server/` that populate the QueryClient cache during
SSR. Requires new server-side prefetch functions, integration with
`createTRPCQueryUtils.tsx` for cache hydration, proper TypeScript
types that infer procedure input/output from the router, and
coordination with the existing `rsc.tsx` caller.

### M10: Add end-to-end type testing infrastructure

Build a type-level test suite that verifies tRPC's TypeScript inference
works correctly across the full stack: router definition → client
creation → React hook usage. Use `expectTypeOf` or `tsd` to assert that
procedure input/output types, error types, and context types propagate
correctly. Requires test files across `server/`, `client/`, and
`react-query/` packages, a shared test router fixture, and CI
integration for type-checking tests.

## Wide

### W1: Implement a plugin system for extending router and procedure behavior

Add a plugin architecture to tRPC that allows third-party packages to
hook into the router lifecycle: procedure registration, request parsing,
response serialization, and error handling. Plugins should be able to
add custom procedure types (beyond query/mutation/subscription), inject
middleware, and extend the client proxy with custom methods. Requires
changes to `initTRPC.ts` for plugin registration, `router.ts` for hook
dispatch, `procedure.ts` for extensible procedure types, the client
proxy for custom method support, and a plugin type contract in `core/`.

### W2: Add full-stack observability with distributed tracing

Implement OpenTelemetry integration across all tRPC packages. Server-
side: automatically create spans for procedure calls with input/output
attributes, propagate trace context through middleware, and record
errors. Client-side: create spans for link operations, propagate trace
headers in HTTP requests, and correlate WebSocket subscriptions. Requires
changes to `resolveResponse.ts`, all server adapters, all client links,
a new `@trpc/opentelemetry` package, and W3C Trace Context header
propagation.

### W3: Implement a tRPC Gateway for multi-service routing

Build a gateway layer that aggregates multiple tRPC services behind a
single router. The gateway should proxy requests to upstream services,
merge their type information at compile time via declaration merging,
handle service discovery and health checks, and support request routing
based on procedure path prefixes. Requires a new `@trpc/gateway`
package, a proxy link that forwards to upstream services, type-level
router merging utilities, connection pooling, and circuit breaker
patterns for upstream failures.

### W4: Implement real-time collaboration infrastructure

Add a collaboration layer built on tRPC subscriptions: presence tracking
(who is online), cursors (where users are), and conflict-free document
operations via CRDTs. Requires a new `@trpc/collab` package with
presence pub/sub, a CRDT library integration for state synchronization,
server-side session management in the WebSocket adapter, React hooks for
presence and document state in `react-query/`, and cleanup on
disconnection in `subscription.ts`.

### W5: Add multi-framework client support (Vue, Svelte, Solid)

Implement framework-specific client packages alongside `react-query`:
`@trpc/vue-query`, `@trpc/svelte-query`, and `@trpc/solid-query`. Each
must provide framework-idiomatic hooks (composables for Vue, stores for
Svelte, signals for Solid), integrate with the framework's SSR story,
and reuse the core client and link infrastructure. Requires new packages,
framework-specific proxy layers mirroring `decorationProxy.ts`, and
shared test utilities across frameworks.

### W6: Implement a code generation pipeline for tRPC routers

Build a CLI tool `@trpc/codegen` that generates client code from a tRPC
router: standalone TypeScript client without proxy magic (for non-TS
consumers), REST client wrappers for each procedure, API documentation
in Markdown, and SDK packages for specific languages (Python, Go) via
an OpenAPI intermediate step. Requires router introspection, template-
based code generation, AST manipulation for TypeScript output, and
integration with the OpenAPI schema generator.

### W7: Implement connection multiplexing and channel-based subscriptions

Add a channel abstraction to tRPC subscriptions that allows multiple
logical subscription channels over a single WebSocket connection. Support
channel groups with shared authentication, per-channel backpressure,
message ordering guarantees, and automatic reconnection with state
recovery. Requires changes to `ws.ts` for channel framing, `wsLink.ts`
for client-side channel management, `subscription.ts` for channel
lifecycle, `observable.ts` for channel-scoped observables, and a new
channel registry on the server.

### W8: Add edge runtime support across all packages

Ensure all tRPC packages work in edge runtimes (Cloudflare Workers,
Vercel Edge Functions, Deno Deploy) that lack Node.js APIs. Replace
Node.js-specific code (streams, Buffer, crypto) with Web API
equivalents, add conditional imports for platform-specific features,
implement a Deno adapter alongside the existing fetch adapter, and
add edge runtime integration tests. Changes span all packages, the
build configuration for dual CJS/ESM output, and the adapter layer.

### W9: Implement a visual debugging and inspection tool

Build `@trpc/devtools` — a browser extension and standalone web UI that
inspects tRPC traffic in real time. Display procedure calls with
input/output, timing, cache status, and WebSocket subscription state.
Support time-travel debugging by replaying past operations, filtering
by procedure path, and displaying the inferred type schema. Requires a
devtools link in the client, a WebSocket-based devtools server, a React
UI for the inspector panel, and integration with the React Query
devtools.

### W10: Implement API versioning and backward compatibility layer

Add API versioning support to tRPC routers: version-prefixed procedure
paths, automatic input/output migration transforms between versions,
deprecation warnings with sunset dates, and a compatibility layer that
routes old client requests through migration functions to the latest
procedure implementation. Requires changes to `router.ts` for versioned
namespacing, `resolveResponse.ts` for version detection from headers,
migration transform infrastructure, client-side version negotiation in
the links, and type-level version discrimination.
