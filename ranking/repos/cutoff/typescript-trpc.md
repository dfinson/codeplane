# trpc/trpc

| Field | Value |
|-------|-------|
| **URL** | https://github.com/trpc/trpc |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Large |
| **Category** | End-to-end typesafe API framework |
| **Set** | Cutoff |
| **Commit** | `2bf122da0b2875dc46a037095f69382524205407` |

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

33 tasks (11 narrow, 11 medium, 11 wide).

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
`status`. Update `eslint.config.js` to add a no-console override for
the loggerLink source file, since structured mode intentionally writes
to `process.stdout` directly.

### N6: Fix subscription cleanup not awaiting async teardown functions

When a WebSocket subscription is ended (client disconnects or calls
`unsubscribe()`), the server-side subscription teardown function
returned from the subscription handler is called but not awaited. If
the teardown is async (e.g., closing a database cursor), it may not
complete before the connection is closed. The `TeardownLogic` type in
`observable/types.ts` currently only supports synchronous teardown
(`UnsubscribeFn = () => void`). Fix the subscription lifecycle in
`observable/observable.ts` to extend `TeardownLogic` to include
`() => Promise<void>` and properly await async teardown functions in
the `unsubscribe` function.

### N7: Fix dataLoader not deduplicating identical concurrent requests

The `dataLoader` in the client internals batches requests into a single
HTTP call but does not deduplicate identical operations (same path and
input) within the same batch. Two components calling the same query
simultaneously produce two entries in the batch instead of sharing one.
Fix the batching logic in `dataLoader.ts` to deduplicate by operation
key and fan out the result to all callers.

### N8: Add content type validation for procedure input in resolveResponse

The server's `contentType.ts` module parses request bodies based on
detected content types but does not eagerly validate that the parsed
input conforms to the procedure's expected schema before dispatching.
When a request body is syntactically valid JSON but structurally
invalid (e.g., an object where a string is expected), the error is
surfaced deep inside the procedure invocation rather than as an early
request-level check. Add early schema pre-validation in
`resolveResponse.ts` that calls the procedure's input parser before
dispatching and returns a `BAD_REQUEST` `TRPCError` with specific
parsing failure details when the input does not match the declared
schema.

### N9: Fix createTRPCReact proxy not guarding against intrinsic method access

When user code accidentally coerces a tRPC proxy to a string or
number (e.g., template literal `` `prefix-${trpc.user}` `` or a
`console.log` that triggers `toString()`), the `createRecursiveProxy`
in `createProxy.ts` treats `'valueOf'` and `'toString'` as procedure
path segments (since they are strings) and dispatches the callback.
Inside `decorationProxy.ts`, the callback tries to call
`hooks['valueOf']` or `hooks['toString']` as a tRPC hook, leading to a
confusing `TypeError`. Fix `decorationProxy.ts` to explicitly return
`undefined` for well-known intrinsic method names (`'valueOf'`,
`'toString'`, `'toJSON'`) before attempting to dispatch to hooks,
matching the existing special-case handling of `'_def'`.

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
Add a `cache` task definition to `turbo.json` for caching test results
of the new cache module, and add the peer dependency declaration to
the client `packages/client/package.json`.

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
frame. Requires changes to `wsLink/wsLink.ts` for message buffering,
the server-side `ws.ts` adapter for batch message parsing, and
`wsEncoder.ts` for WebSocket batch envelope encoding and formatting.

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
server-side session management in the WebSocket adapter (`adapters/ws.ts`),
React hooks for presence and document state in `react-query/`, and cleanup
on disconnection via the observable teardown in `observable/observable.ts`.

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
recovery. Requires changes to `ws.ts` for channel framing, `wsLink/wsLink.ts`
for client-side channel management, `observable/observable.ts` for
channel-scoped observables and channel lifecycle, and a new
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

### N11: Fix codecov.yml not configuring per-package coverage thresholds

The `codecov.yml` at the repo root defines global coverage settings but
does not set per-package coverage targets for `packages/server/`,
`packages/client/`, `packages/react-query/`, and `packages/next/`. Each
package has different inherent coverage characteristics — the server
package has higher coverage expectations than framework adapters. Add
package-level `flags` and `coverage.status.project` entries in
`codecov.yml` with appropriate thresholds per package, and update
`.github/workflows/main.yml` to upload coverage reports with the correct
flag labels matching each package directory.

### M11: Add comprehensive monorepo developer documentation

Expand the existing `CONTRIBUTING.md` at the repo root to document the
monorepo development workflow more fully: how to use `turbo.json` task
pipelines for building and testing, the relationship between
`pnpm-workspace.yaml` packages, `tsconfig.build.json` vs `tsconfig.json`
usage for build vs editor type checking, and the release process
configured in `lerna.json`. Add a `docs/architecture.md` describing the
package dependency graph (server → core, client → core, react-query →
client, next → react-query + server). Update `README.md` to link to the
new developer documentation. Update `.github/pull_request_template.md` to
include a contribution checklist referencing the new guide.

### W11: Overhaul CI/CD pipeline and developer tooling configuration

Restructure `.github/workflows/` by consolidating overlapping workflows:
merge `dependabot-approve.yml` and `lock-issues.yml` into a single
`maintenance.yml` workflow, and split `main.yml` into separate `test.yml`
and `release.yml` workflows (a `lint.yml` already exists) with proper job
dependencies between them. Add a `.github/workflows/docs.yml` that builds
and deploys documentation from the `www/` directory on pushes to `main`.
Update `.github/renovate.json` (already present) to enable auto-merge for
patch version updates across all packages. Update `prettier.config.js` and
`.prettierignore` to include consistent formatting for all `.yml`, `.yaml`,
and `.json` configuration files across the monorepo. Update
`.vscode/settings.json` with recommended extensions and editor settings for
the monorepo. Update `vitest.config.ts` to add coverage thresholds matching
the `codecov.yml` per-package targets.
