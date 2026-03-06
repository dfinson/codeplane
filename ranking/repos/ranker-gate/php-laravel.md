# laravel/framework

| Field | Value |
|-------|-------|
| **URL** | https://github.com/laravel/framework |
| **License** | MIT |
| **Language** | PHP |
| **Scale** | Large (multi-team project) |
| **Category** | Full-stack web framework |
| **Set** | ranker-gate |
| **Commit** | `bddeb4a5cc576202723ffcfe607260d86a05aee2` |

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

### N2: Fix `MorphTo` eager loading not applying global scopes on related models

When using `MorphTo::with()` to eager-load a polymorphic relationship,
global scopes on the related models (such as `SoftDeletes` on some
morph targets) are not applied to the eager-loading query. This causes
soft-deleted records to appear in morphTo eager-load results even
though direct queries on those models correctly exclude them. Fix
`MorphTo::getResultsByType()` to apply the target model's global
scopes when building the eager-loading query.

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

### N7: Fix `RedisStore::flush` not respecting cache prefix

`Cache::store('redis')->flush()` calls Redis `FLUSHDB`, which
deletes all keys in the current database — including keys belonging
to other applications sharing the same Redis database. The flush
operation should only delete keys matching the configured
`cache.prefix`. Use `SCAN` with the prefix pattern and `DEL` in
batches instead of `FLUSHDB` to scope the flush to the
application's cache keys only.

### N8: Fix `Validator::sometimes` not resolving wildcard context for sibling fields

When using `$validator->sometimes('items.*.price', 'required',
fn($input) => ...)`, the callback receives the entire top-level
input array rather than the current wildcard element. This makes it
impossible to write conditions referencing sibling fields (e.g.,
checking `type === 'physical'` for the same item). Fix `sometimes()`
to pass the current array element scope when the rule path contains
wildcards, allowing the callback to evaluate sibling fields.

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

### M1: Implement Eloquent model versioning with change history

Add a `HasVersions` trait that automatically records a snapshot of
model attributes in a `model_versions` table on every update.
Each version stores the changed attributes, the authenticated
user who made the change, and a timestamp. Add
`$model->versions()` to retrieve the history,
`$model->revertTo($versionId)` to restore a previous state, and
`$model->diff($versionId)` to compare two versions. Add a
migration generator `php artisan make:version-table` and integrate
with Eloquent events (`updating`, `updated`) to capture changes
automatically.

### M2: Add queue job chaining with shared state propagation

Extend `Bus::chain()` to support a shared state bag that passes
data between jobs in the chain. Each job can read values set by
its predecessor via `$this->chainState->get('key')` and write
values for the next job via `$this->chainState->set('key', $val)`.
Persist chain state in the queue payload so it survives worker
restarts. Add `ChainState` as a serializable value object. Support
conditional branching where a job can skip the next N jobs in the
chain based on state. Integrate with `PendingChain`, `Dispatcher`,
and the queue `CallQueuedHandler`.

### M3: Implement Eloquent query result caching with automatic invalidation

Add `User::where('active', true)->remember(60)->get()` that caches
query results for the given number of seconds using the configured
cache store. Generate cache keys from the SQL query and bindings.
Add automatic cache invalidation when the model fires `created`,
`updated`, or `deleted` events by clearing cached queries that
reference the model's table. Support `rememberForever()` and
`dontRemember()` overrides. Add a `config/database.php` option
to enable/disable query caching per connection. Implement in a
`CachesQueries` trait used by both `Query\Builder` and
`Eloquent\Builder`, integrate with `Cache\Repository` and
the Eloquent event dispatcher.

### M4: Add rate limiting improvements with sliding window

Replace the current fixed-window rate limiter with a sliding window
algorithm. Add per-route rate limit configuration via route middleware:
`throttle:rate_limit_name`. Support rate limit headers
(`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`).
Add Redis-backed sliding window implementation. Support rate limiting
by authenticated user, API key, or custom resolvers.

### M5: Add database-level advisory locks across MySQL, PostgreSQL, and SQLite

Implement `DB::advisoryLock('migration_batch')` that acquires a
named advisory lock using `GET_LOCK()` on MySQL,
`pg_advisory_lock()` on PostgreSQL, and a file-based fallback on
SQLite. Add `->get($name, $timeout)`, `->release($name)`, and
`->block($name, fn() => ...)` methods. Throw
`LockTimeoutException` when the lock cannot be acquired within
the timeout. Support shared and exclusive lock modes. Implement
grammar-specific lock SQL in `MySqlGrammar`, `PostgresGrammar`,
and `SQLiteGrammar`. Expose through the `Connection` class.
Add an Artisan middleware trait to prevent concurrent command
execution using advisory locks.

### M6: Implement connection-level query timeout with per-query override

Add a configurable query timeout at the database connection level
via `database.connections.mysql.timeout` (in seconds) that
automatically kills queries exceeding the threshold. Implement via
`SET SESSION max_execution_time` on MySQL and `SET
statement_timeout` on PostgreSQL. Add per-query override:
`DB::timeout(5)->select(...)`. When a query exceeds the timeout,
throw a `QueryTimeoutException` containing the query SQL and
elapsed time. Implement in `Connection`, `MySqlConnector`,
`PostgresConnector`, and the query builder. Add
`--timeout` option to Artisan `db:monitor` for detecting
long-running queries.

### M7: Add middleware priority ordering with topological sort

Replace the current middleware priority array with a dependency-based
ordering system. Allow middleware to declare `$before` and `$after`
dependencies: `class RateLimitMiddleware { public $after = [AuthMiddleware::class]; }`. Compute execution order via topological
sort in the router. Detect and report circular dependencies at
boot time. Support priority groups for route-level and global
middleware independently.

### M8: Add Eloquent model factory states with dependencies

Extend Eloquent model factories to support state dependencies
where one state requires another to be applied first. Add
`$factory->state('published')->dependsOn('approved')` so that
calling `->published()` automatically applies `->approved()` first.
Detect circular state dependencies at factory definition time.
Add `afterCreating` hooks that are scoped to specific states. Support
state composition with `$factory->states(['approved', 'featured'])`.
Integrate with `HasFactory` trait and the factory resolution in
`Illuminate\Database\Eloquent\Factories\Factory`. Add state
introspection via `$factory->getAppliedStates()`.

### M9: Add database schema diffing for migration generation

Implement `php artisan make:migration --diff` that compares the current
database schema with the Doctrine DBAL representation and generates a
migration with the changes. Support column type changes, index
additions/removals, foreign key modifications, and table renames.
Generate both `up()` and `down()` methods. Add a `--dry-run` flag
that prints the SQL without creating the migration file.

### M10: Add notification channel throttling with per-user rate limits

Implement per-channel, per-user notification throttling to prevent
spamming users with duplicate or excessive notifications. Add
`throttle($seconds)` to `Notification` classes that limits how
often the same notification type is sent to the same notifiable
via each channel. Use `Cache\RateLimiter` under the hood with
keys derived from the notification class, notifiable ID, and
channel name. Add `shouldThrottle()` and `throttleKey()` methods
for custom throttle logic. Integrate into `NotificationSender`
so throttled notifications are silently dropped. Add
`Notification::assertThrottled()` for testing. Modify
`ChannelManager` to check throttle state before dispatching.

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

### W3: Implement a database query profiler across HTTP, queue, and Artisan

Build a query profiling system that records all database queries with
execution time, caller stack trace, bindings, connection name, and
the context (HTTP request, queue job, or Artisan command) that
triggered them. Add `DB::enableProfiler()` and a `QueryProfiled`
event. Implement an HTTP middleware that collects queries per request
and adds `X-Query-Count` and `X-Query-Time` response headers. Add
an Artisan `db:profile` command that analyzes recorded queries for
N+1 patterns (repeated queries with different bindings), missing
index hints (queries with full table scans), and slow queries above
a threshold. Store profiling data via a `ProfileStore` interface
with database and file drivers. Add a Blade component for rendering
query timelines in development. Cross-cuts Database, Http,
Queue, Console, Events, and View subsystems.

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
