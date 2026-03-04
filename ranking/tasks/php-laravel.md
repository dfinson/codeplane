# Tasks — laravel/framework

10 tasks (3 narrow, 4 medium, 3 wide) for the PHP full-stack web framework.

## Narrow

### N1: Fix `whereJsonContains` not working with boolean values on SQLite

`whereJsonContains('settings->notifications', true)` generates invalid
SQL on SQLite because SQLite's JSON functions represent booleans as
integers (1/0), not as JSON true/false. The SQLite query grammar does
not convert PHP booleans to SQLite-compatible JSON boolean values.
Fix the SQLite grammar's JSON contains compilation.

### N2: Add `withSum` and `withAvg` eager loading aggregate methods

`withCount` is available for eager-loading relationship counts, but
there's no equivalent for sum and average. Add `withSum('relationship', 'column')` and `withAvg('relationship', 'column')` to Eloquent's query
builder that add a subquery for the aggregate value. The result should
be accessible as `$model->relationship_sum_column`.

### N3: Fix `Route::fallback` not triggered for OPTIONS requests

The fallback route registered with `Route::fallback()` is not invoked
for OPTIONS requests that don't match any defined route. Instead, a
plain 405 is returned without CORS headers, breaking preflight requests
for undefined routes. Fix the router to run the fallback route for
unmatched OPTIONS requests.

## Medium

### M1: Implement model attribute encryption

Add built-in attribute encryption for Eloquent models. A `$encrypted`
property on the model lists columns that should be encrypted at rest.
Use `APP_KEY` with AES-256-GCM encryption. Encrypt on `setAttribute`,
decrypt on `getAttribute`. Support querying encrypted columns with
deterministic encryption mode (same plaintext → same ciphertext) for
equality searches. Add migration helper to encrypt existing data.

### M2: Add queue job batching with progress tracking

Enhance `Bus::batch()` with per-batch progress tracking: percentage
complete, estimated time remaining, jobs succeeded/failed/pending. Add
a `BatchProgress` event that fires on each job completion. Surface
progress through an API endpoint and a Blade component for real-time
UI updates. Support nested batches (a batch within a batch) with
aggregated progress.

### M3: Implement database query builder macro system

Add a macro system to the database query builder that allows registering
custom query methods. `Builder::macro('active', fn ($q) => $q->where('active', true))` should add `->active()` to all query builders.
Support per-connection macros, macros with parameters, and macro
chaining. Add built-in macros for common patterns: `whereNotNull`,
`orWhereNot`, `orderByDesc`.

### M4: Add rate limiting improvements with sliding window

Replace the current fixed-window rate limiter with a sliding window
algorithm. Add per-route rate limit configuration via route middleware:
`throttle:rate_limit_name`. Support rate limit headers
(`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`).
Add Redis-backed sliding window implementation. Support rate limiting
by authenticated user, API key, or custom resolvers.

## Wide

### W1: Implement real-time model synchronization with broadcasting

Add automatic model change broadcasting via WebSockets. When an
Eloquent model implements `ShouldBroadcastChanges`, all create/update/delete
operations automatically broadcast to a private channel. Subscribe
from JavaScript with `Echo.model('App.Models.User', userId)`.
Support filtered attributes (don't broadcast sensitive fields),
batch change coalescing, and authorization via channel policies.
Changes span Eloquent events, the broadcasting system, and the
JavaScript Echo client.

### W2: Add comprehensive API resource improvements

Overhaul API Resources with: conditional relationships (only load
if requested via `?include=`), sparse fieldsets (`?fields=id,name`),
automatic pagination with cursor support, resource-level caching
with ETags, and batch resource loading. Support JSON:API specification
compliance mode. Changes span the resource classes, routing, query
string parsing, and response formatting.

### W3: Migrate the test suite to support parallel execution

Refactor `TestCase` and all test infrastructure to support
`php artisan test --parallel`. Fix database migration state isolation
(each parallel process gets its own test database), fix filesystem
state (temp directories per process), fix cache isolation, and fix
queue fake isolation. Add `ParallelTestCase` base class that handles
resource isolation automatically.
