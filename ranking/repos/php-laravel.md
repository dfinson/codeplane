# laravel/framework

| Field | Value |
|-------|-------|
| **URL** | https://github.com/laravel/framework |
| **License** | MIT |
| **Language** | PHP |
| **Scale** | Large (multi-team project) |
| **Category** | Full-stack web framework |

## Why this repo

- **No single developer knows it all**: Routing, Eloquent ORM, Blade
  templating, queue system, event broadcasting, mail, notifications, cache,
  filesystem abstraction, authentication/authorization, validation, console
  (Artisan), scheduling, HTTP client, database migrations, testing helpers —
  each a deep subsystem with multiple implementations and integration points.
- **Well-structured**: Source under `src/Illuminate/` with each subsystem as
  a top-level namespace directory. Each component is designed as a standalone
  package (Illuminate/Database, Illuminate/Routing, etc.) with clear
  contracts (interfaces) and implementations.
- **Rich history**: 35K+ commits, most popular PHP framework. Extremely rich
  PR history with every type of development pattern. Regular major versions
  with breaking changes documented.
- **Permissive**: MIT license.

## Structure overview

```
src/Illuminate/
├── Routing/              # URL routing, middleware pipeline
├── Database/             # Eloquent ORM, query builder, migrations
│   ├── Eloquent/         # Active record ORM
│   │   ├── Model.php     # Base model
│   │   ├── Builder.php   # Query builder
│   │   └── Relations/    # Has many, belongs to, etc.
│   ├── Query/            # Raw query builder
│   ├── Migrations/       # Schema migration engine
│   └── Schema/           # Schema builder
├── View/                 # Blade template engine
├── Queue/                # Job queue (Redis, SQS, database, etc.)
├── Events/               # Event dispatcher
├── Mail/                 # Email sending
├── Notifications/        # Multi-channel notifications
├── Cache/                # Cache manager (Redis, Memcached, file, etc.)
├── Filesystem/           # Storage abstraction (local, S3, etc.)
├── Auth/                 # Authentication and authorization
├── Validation/           # Input validation
├── Console/              # Artisan CLI framework
├── Http/                 # Request, response, middleware
├── Broadcasting/         # Event broadcasting (Pusher, Redis)
├── Foundation/           # Application bootstrap, service providers
└── Support/              # Utilities, collections, helpers
```

## Scale indicators

- ~2,000 PHP source files
- ~250K+ lines of code
- Deep namespace hierarchies (4-5 levels)
- Cross-cutting service container and event system

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

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

### N4: Fix `BelongsToMany` pivot timestamps not respecting custom format

When a pivot table has `created_at` and `updated_at` with
`withTimestamps()`, Eloquent always formats them using the default
`Y-m-d H:i:s` format, ignoring the model's `$dateFormat` property
and the `$casts` array on the pivot model. Fix `BelongsToMany` to
respect the pivot model's date format configuration when writing
timestamps.

### N5: Fix `Cache::remember` not refreshing TTL on read for Redis store

`Cache::remember('key', 3600, fn() => ...)` returns the cached value
correctly but does not update the TTL on subsequent reads, unlike a
true cache-aside pattern. The Redis store's `get` method does not
issue a TTL refresh command. Add an optional `touch` parameter to
the Redis cache store that resets the key's expiration on read.

### N6: Fix `Validator` not validating nested array wildcard indices consistently

Using `data.*.email` in validation rules skips indices with `null`
values in the input array instead of reporting them as failing. When
the input is `['data' => [['email' => 'a@b.com'], null, ['email' => '']]]`,
index 1 is silently ignored. Fix the validation rule compilation to
treat null array elements as present but failing validation.

### N7: Fix Artisan `schedule:list` not showing timezone-adjusted times

`schedule:list` always shows scheduled times in UTC regardless of the
application's configured timezone or the individual event's
`->timezone('America/New_York')` setting. Fix the console command to
display the next due time in each event's configured timezone, falling
back to `app.timezone`.

### N8: Fix `HasManyThrough` not supporting soft deletes on intermediate model

When the intermediate model in a `HasManyThrough` relationship uses
`SoftDeletes`, the query does not add a `whereNull('intermediate.deleted_at')`
constraint. This causes rows through soft-deleted intermediates to
appear in results. Fix `HasManyThrough` to check for the `SoftDeletes`
trait on the intermediate model and apply the constraint automatically.

### N9: Fix `Blade::component` alias registration failing for nested directories

Registering a Blade component class from a nested namespace like
`App\View\Components\Forms\Input` with `Blade::component('forms.input', Input::class)` fails to resolve the view, because the component
resolver does not translate dots to directory separators when looking
for the inline or view-based template. Fix the component tag compiler
to handle dotted alias names.

### N10: Fix `Mail::fake` not capturing mailable attachments for assertions

`Mail::assertSent(OrderConfirmation::class, fn ($m) => $m->hasAttachment('invoice.pdf'))` always returns false because the fake
mail transport never calls `build()` on the mailable, so attachments
are never populated. Fix `MailFake` to call `build()` before storing
the sent mailable so attachment assertions work correctly.

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

### M5: Add query builder `upsert` with configurable conflict resolution

Implement `DB::table('users')->upsert($rows, $uniqueBy, $updateColumns)`
across MySQL, PostgreSQL, and SQLite grammars. MySQL should use
`INSERT ... ON DUPLICATE KEY UPDATE`, PostgreSQL `INSERT ... ON CONFLICT
... DO UPDATE`, and SQLite `INSERT OR REPLACE`. Support composite
unique keys, expressions in update values (e.g., `DB::raw('count + 1')`),
and an `upserted` event on Eloquent models.

### M6: Implement Eloquent lazy collection cursors with memory-bounded chunking

Add `Model::cursor()` that returns a `LazyCollection` backed by a
server-side cursor (PDO unbuffered query). Integrate with chunk
processing so `User::cursor()->each(...)` never holds more than one
Eloquent model in memory. Support cursor-based pagination for large
result sets. Add cursor support to all database drivers including
SQLite (simulated with LIMIT/OFFSET) and SQL Server.

### M7: Add middleware priority ordering with topological sort

Replace the current middleware priority array with a dependency-based
ordering system. Allow middleware to declare `$before` and `$after`
dependencies: `class RateLimitMiddleware { public $after = [AuthMiddleware::class]; }`. Compute execution order via topological
sort in the router. Detect and report circular dependencies at
boot time. Support priority groups for route-level and global
middleware independently.

### M8: Implement conditional validation rules with cross-field dependencies

Add `required_if_accepted`, `required_unless_declined`, and a
general-purpose `Rule::when(fn ($input) => ..., 'required|email')`
syntax that evaluates the condition closure against the full input.
Support dependency tracking so validation error messages reference the
controlling field. Add circular dependency detection when rule A
depends on rule B and vice versa. Integrate with form request classes.

### M9: Add database schema diffing for migration generation

Implement `php artisan make:migration --diff` that compares the current
database schema with the Doctrine DBAL representation and generates a
migration with the changes. Support column type changes, index
additions/removals, foreign key modifications, and table renames.
Generate both `up()` and `down()` methods. Add a `--dry-run` flag
that prints the SQL without creating the migration file.

### M10: Implement queue worker graceful shutdown with job completion guarantees

Add `SIGTERM` and `SIGQUIT` handling to the queue worker that allows
the current job to complete before shutting down. Track in-progress
jobs in a Redis set so a supervisor can verify all jobs finished.
Add a `--drain` mode that processes remaining jobs and exits when the
queue is empty. Support configurable shutdown timeout after which the
job is released back to the queue with an incremented attempt count.

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

### W4: Add multi-tenancy support across database, cache, queue, and filesystem

Implement tenant identification via subdomain, header, or path prefix
in the routing layer. Automatically scope Eloquent queries with a
`tenant_id` global scope. Switch database connections, cache prefixes,
queue names, and filesystem disks per tenant. Add `Tenant` model with
migration, tenant-aware `artisan` commands (`--tenant=`), and a
`TenantServiceProvider` that bootstraps all scoping. Support tenant
seeding and per-tenant migration state.

### W5: Implement full-text search across Eloquent, Scout, and database drivers

Add `Model::search('query')` that dispatches to a full-text search
driver. Implement a database driver using MySQL `MATCH AGAINST` and
PostgreSQL `tsvector/tsquery`. Add Scout integration so models can
switch between database and Algolia/Meilisearch drivers transparently.
Support field weighting, fuzzy matching, highlighting, and faceted
search results. Add a Blade component for rendering highlighted
search results. Cross-cuts Database, Scout, View, and Collections.

### W6: Add end-to-end request tracing across HTTP, queue, events, and mail

Generate a trace ID at the start of each HTTP request and propagate it
through queued jobs, event listeners, notification dispatches, and
mail sends. Store the trace ID in a context singleton bound in the
container. Add middleware that sets `X-Trace-Id` response header and
injects it into all log entries via a Monolog processor. Support
distributed tracing by accepting an incoming `X-Trace-Id` header.
Add an Artisan command to query logs by trace ID.

### W7: Implement a feature flag system spanning config, middleware, Blade, and routing

Add a `Feature` facade with `Feature::active('new-checkout')` backed
by database, config, or custom drivers. Gate routes with
`Route::middleware('feature:new-checkout')`. Add Blade directives
`@feature('new-checkout') ... @endfeature`. Support percentage
rollouts, user-segment targeting, and A/B variants. Integrate with
the authentication system for user-scoped flags. Add Artisan commands
for flag management and a scheduled cleanup of stale flags.

### W8: Add comprehensive database monitoring and query analysis pipeline

Implement a query logger that captures all queries with bindings,
execution time, caller file/line, and connection name. Add a slow-query
detector with configurable threshold that fires `SlowQueryDetected`
events. Build an N+1 query detector that tracks repeated queries per
request. Add a debug bar integration and an Artisan command
`db:analyze` that reports slow queries, missing indexes, and N+1
patterns from a recorded session. Touches Database, Events, Http
middleware, Console, and the service container.

### W9: Implement a workflow engine on top of queues, events, and Eloquent

Add a `Workflow` class that defines a directed acyclic graph of
queue jobs with dependencies. `Workflow::define()->addJob(A::class)->addJob(B::class, after: A::class)->addJob(C::class, after: [A::class, B::class])`. Persist workflow state in the database
with an Eloquent model. Dispatch jobs only when predecessors complete.
Support failure policies (retry, skip, halt), workflow-level events,
and a real-time progress channel via broadcasting. Cross-cuts Queue,
Database, Events, and Broadcasting subsystems.

### W10: Add pluggable authentication with multi-guard SSO and session migration

Implement an SSO bridge that delegates authentication to SAML and
OIDC providers, mapping external claims to local user attributes.
Support multiple guards simultaneously (e.g., `web` guard with
session, `api` guard with JWT, `admin` guard with SSO). Add session
migration on login so existing anonymous session data (cart, preferences)
transfers to the authenticated session. Integrate with notifications
to send login alerts via mail and SMS. Touches Auth, Session, Routing,
Notifications, and Mail subsystems.
