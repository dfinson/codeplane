# rails/rails

| Field | Value |
|-------|-------|
| **URL** | https://github.com/rails/rails |
| **License** | MIT |
| **Language** | Ruby |
| **Scale** | Large (multi-team project) |
| **Category** | Full-stack web framework |

## Why this repo

- **No single developer knows it all**: Active Record (ORM, migrations,
  validations), Action Pack (routing, controllers, rendering), Action View
  (templates, helpers, partials), Action Mailer, Action Cable (WebSockets),
  Active Job (background jobs), Active Storage (file uploads), Action Text
  (rich text), Active Support (core extensions) — each a substantial,
  independently complex framework.
- **Well-structured**: Each framework is its own gem directory with independent
  `lib/`, tests, and gemspec. Clear separation — you can use Active Record
  without Action Pack. Internal structure within each gem follows consistent
  patterns.
- **Rich history**: 90K+ commits, 20+ years of development, 5K+ contributors.
  The definitive Ruby project. Every type of PR pattern imaginable.
- **Permissive**: MIT license.

## Structure overview

```
.
├── activerecord/            # ORM framework
│   └── lib/active_record/
│       ├── base.rb          # Model base class
│       ├── relation/        # Query builder (chainable scopes)
│       ├── connection_adapters/ # Database adapters (PG, MySQL, SQLite)
│       ├── migration/       # Schema migrations
│       ├── associations/    # has_many, belongs_to, etc.
│       └── validations/     # Model validations
├── actionpack/              # Controllers and routing
│   └── lib/action_controller/
│       ├── base.rb          # Controller base class
│       ├── metal/           # Routing, rendering, params
│       └── ...
├── actionview/              # View rendering
│   └── lib/action_view/
│       ├── template/        # Template loading, compilation
│       ├── helpers/         # Built-in view helpers
│       └── renderer.rb      # Rendering pipeline
├── activesupport/           # Core extensions and utilities
├── actionmailer/            # Email sending
├── actioncable/             # WebSockets
├── activejob/               # Background job framework
├── activestorage/           # File upload management
├── actiontext/              # Rich text content
└── actionmailbox/           # Inbound email processing
```

## Scale indicators

- ~4,000 Ruby source files
- ~400K+ lines of code
- Deep module hierarchies (4-5 levels)
- Cross-cutting Active Support used everywhere

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `has_many :through` with `scope` ignoring ORDER BY

When a `has_many :through` association uses a scope with an `order`
clause, the ORDER BY is silently dropped when the association is loaded
via `includes` (eager loading). The eager loader's join query does not
preserve the scope's order. Fix the eager loading to incorporate the
scoped association's order clause.

### N2: Add `assert_enqueued_email_with` test helper

Active Job has `assert_enqueued_with` for jobs, but there's no dedicated
email assertion helper. Add `assert_enqueued_email_with(mailer, method, args:, params:)` that validates the mailer class, method, arguments,
and Action Mailer params in one assertion. Include a clear failure
message showing expected vs actual.

### N3: Fix `ActiveStorage::Blob#download` not respecting `Range` header

When downloading an Active Storage blob with a `Range` header, the full
blob is returned instead of the requested byte range. The blob download
method does not pass the range to the storage service. Fix `Blob#download`
and the `DiskService` / `S3Service` to support partial content responses.

### N4: Fix `ActionController::Live` SSE stream not flushing headers immediately

When using `ActionController::Live` with `SSE`, the response headers
are not sent to the client until the first `stream.write` call. This
means clients cannot detect the content type or establish the SSE
connection until data arrives. Fix the `Live::Response` to flush
headers as soon as the streaming thread starts, before any writes.

### N5: Fix `ActiveRecord::Migration#change` reversibility for `remove_column` with type

When `remove_column :users, :age, :integer` is used inside a `change`
migration, the reversal fails because the column type information is
lost during rollback. The `CommandRecorder` does not capture the column
type argument for `remove_column`. Fix it so rollback correctly calls
`add_column` with the original type and options.

### N6: Fix `ActiveSupport::Cache::MemoryStore` race condition on `#fetch`

Under concurrent requests, `MemoryStore#fetch` can execute the block
multiple times for the same key when the cache entry expires. The
current implementation checks existence and writes without holding a
lock across both operations. Add a per-key mutex to ensure the block
executes exactly once per expiry cycle.

### N7: Fix `ActionView::Template` digest cache not invalidating on partial rename

When a partial is renamed (e.g., `_form.html.erb` to `_edit_form.html.erb`),
templates that reference the old partial name continue using the stale
digest from the template digest cache. The `Digestor` dependency tracker
does not detect that the referenced partial no longer exists. Fix the
cache invalidation to detect missing partials and force a recompute.

### N8: Fix `ActiveRecord::Enum` not raising on conflicting method definitions

When an enum declares a value that conflicts with an existing model
method (e.g., `enum status: [:new]` shadowing `Object#new`), no warning
is raised and the model silently breaks. Add a conflict check in
`enum` that raises `ArgumentError` listing the conflicting methods
before they are defined.

### N9: Fix `ActionMailer::Preview` not rendering multipart emails correctly

When previewing a multipart email (text + HTML) in the Rails mailer
preview UI, only the HTML part is displayed and there is no way to
toggle to the plain-text part. Fix the mailer preview controller and
its view template to add a format switcher that lets developers view
each MIME part independently.

### N10: Fix `ActiveStorage::Variant` not applying transformations in declared order

When multiple transformations are chained on a variant (e.g.,
`blob.variant(resize_to_limit: [100, 100], rotate: 90)`), the
transformations are applied in hash key insertion order, which may
differ across Ruby versions. Fix `ActiveStorage::Transformers::ImageProcessingTransformer`
to use an ordered list so transformations apply in the developer's
declared order.

## Medium

### M1: Implement Action Cable testing improvements

Add integration testing support for Action Cable: test that a channel
broadcasts the expected data when a controller action runs, test
WebSocket authentication flows, and test channel rejection. Add
`assert_broadcast_on(channel, data)` that works in integration tests
(not just channel unit tests). Support testing multiple concurrent
connections.

### M2: Add database query analytics in development

Implement a development-mode query analytics panel that shows: N+1
query detection with source location, duplicate queries, slow queries
(with EXPLAIN output), and total query count per request. Surface
this through the existing Rails debug bar. Add
`ActiveRecord::QueryAnalyzer` that can be configured with custom
analyzers.

### M3: Implement Action Mailbox routing improvements

Add pattern-based routing to Action Mailbox beyond the current
`routing` DSL. Support regex-based address matching, domain-based
routing, and catch-all routes. Add routing by email headers (Subject,
X-Headers). Support route priorities when multiple routes match.
Add a test helper that simulates inbound email with full header
construction.

### M4: Add encrypted credentials per-environment

Extend `credentials.yml.enc` to support per-environment credential
files: `credentials/production.yml.enc`, `credentials/staging.yml.enc`.
Environment-specific credentials should merge on top of the shared
credentials file. Add `rails credentials:edit --environment staging`.
Support key rotation that re-encrypts a credential file with a new
master key without changing the decrypted content.

### M5: Add multi-database automatic role switching

Extend `ActiveRecord::DatabaseConfigurations` to automatically switch
between a primary (writer) and replica (reader) database based on
request method. GET/HEAD requests should use the replica after a
configurable delay following the last write. Add connection pooling
per role, health checks that fall back to the primary when the
replica is unavailable, and instrumentation events for role switches.

### M6: Implement Action View component slots

Add a slot-based content projection API to `ActionView::Base` so that
view components can declare named slots (`header`, `body`, `footer`)
and callers can fill them from the rendering template. Support default
slot content, required slots with helpful error messages, and slot
enumeration for repeated content. Integrate with the existing
`content_for` / `yield` mechanism without breaking backward
compatibility.

### M7: Add Active Record query result caching with automatic invalidation

Implement a query-level cache in `ActiveRecord::Relation` that stores
result sets keyed by the SQL and bind parameters. Automatically
invalidate cached results when any `INSERT`, `UPDATE`, or `DELETE`
touches the cached table, using `ActiveSupport::Notifications`
instrumentation. Support cache backends (memory, Redis) and
per-model opt-in via `cache_queries ttl: 30.seconds`.

### M8: Implement Action Cable channel authorization

Add an authorization layer to Action Cable channels so that
`reject` can be called based on fine-grained policies, not just
in `subscribed`. Support `authorize_action :speak, :moderate`
per channel action, policy objects that receive the current user
and channel params, and denial callbacks that send structured
rejection messages to the client. Add test helpers to assert
authorization outcomes.

### M9: Add Active Job middleware stack

Implement a middleware stack for Active Job, similar to Rack middleware
for HTTP. Each middleware wraps job execution and can modify arguments,
log context, add retry logic, or enforce rate limits. Support
per-queue and per-job-class middleware configuration.
Add built-in middlewares for logging, unique-job deduplication,
and execution timeout.

### M10: Add `ActionDispatch::Routing` constraint groups

Extend the routing DSL to support named constraint groups that can
be applied to multiple routes declaratively. Support composable
constraints (e.g., `authenticated & admin`), constraints that
set request attributes (like `current_tenant`), and lazy
evaluation that skips constraint checks when previous constraints
already matched. Add integration test helpers to assert constraint
evaluation order.

## Wide

### W1: Implement API-only mode improvements

Enhance `rails new --api` mode with: automatic OpenAPI schema generation
from routes and controllers, request/response body validation against
the schema, API versioning through URL prefix or header, hypermedia
links in responses (JSON:API or HAL), and a built-in API documentation
viewer. Changes span routing, controller rendering, serialization,
and the generator templates.

### W2: Add comprehensive audit logging framework

Implement `ActiveRecord::Auditing` that tracks who changed what and when
for any model. Log create, update, and delete operations with the
previous and new values, the user who made the change (from
`Current.user`), the request context (IP, user agent), and a timestamp.
Store audit records in a dedicated table. Support audit record querying,
diff viewing, and revert operations. Add admin interface integration.

### W3: Migrate from Minitest to support both Minitest and RSpec natively

Refactor the Rails test framework layer to support both Minitest and
RSpec as first-class testing backends. Extract the shared assertion
logic (database fixtures, integration test helpers, system test drivers,
mailer assertions) into a backend-agnostic layer that both Minitest
and RSpec adapters use. Update all generators to produce tests for
the configured backend. Support running mixed test suites.

### W4: Implement full-stack multi-tenancy support

Add tenant isolation across Active Record, Action Cable, Active Job,
and Active Storage. Support schema-based (PostgreSQL schemas) and
row-based (`tenant_id` column) strategies. Automatically scope
queries, job execution, cable subscriptions, and blob URLs to the
current tenant set via `Current.tenant`. Add middleware that
resolves tenant from subdomain or header. Include migration
generators for tenant-aware tables and a test helper to switch
tenants in tests.

### W5: Add GraphQL layer alongside REST

Implement `ActionGraphQL`, a new framework component that generates
a GraphQL schema from Active Record models and their associations.
Support queries, mutations, subscriptions (via Action Cable), field
authorization, N+1 prevention via dataloader, cursor-based
pagination, and schema stitching for multi-engine apps. Add a
generator (`rails g graphql:model Post`), a development-mode
schema explorer, and integration test helpers for GraphQL queries.

### W6: Implement zero-downtime deployment primitives

Add a `ActiveRecord::Migration::Safe` module that wraps dangerous
migration operations (adding a column with a default, renaming a
column, adding an index non-concurrently) with safe multi-step
alternatives. Integrate with a deployment health check endpoint
in `ActionDispatch`. Add connection draining to `ActionCable`,
graceful shutdown to `ActiveJob` workers, and a `rails deploy:check`
command that validates migration safety, pending jobs, and
active cable connections before cutover.

### W7: Add real-time collaboration engine

Implement `ActionSync`, a new framework component that adds
Operational Transformation or CRDT-based real-time document
collaboration. Integrate with Active Record for persistence,
Action Cable for transport, Active Storage for binary assets
in documents, and Action Text for rich-text fields. Support
cursor presence, conflict resolution, undo/redo per client,
and offline buffering with sync-on-reconnect.

### W8: Implement cross-framework observability pipeline

Add structured tracing, metrics, and logging across all Rails
frameworks. Instrument Active Record queries, Action Controller
request handling, Action View rendering, Active Job execution,
and Action Cable message delivery with OpenTelemetry-compatible
spans. Add a `Rails::Telemetry` configuration that supports
multiple exporters (OTLP, Prometheus, Datadog). Include
request-scoped correlation IDs that propagate through background
jobs and cable broadcasts.

### W9: Implement Progressive Web App support

Add PWA support spanning multiple frameworks: a service worker
generator in Action View that caches assets and API responses,
offline fallback pages, push notification integration through
Action Mailer's delivery infrastructure, a web app manifest
generator, background sync via Active Job for offline form
submissions, and an install prompt helper. Update the asset
pipeline to produce precache manifests and update `rails new`
templates with PWA-ready defaults.

### W10: Add end-to-end type checking with RBS integration

Integrate RBS type signatures across all Rails frameworks. Generate
RBS files for Active Record models from database schema, for
controller actions from route definitions, and for view helpers
from their method signatures. Add a `rails types:generate` command
that produces a complete RBS type definition for the application.
Support type-checked `params`, association return types, and
scope chain types. Add CI integration that runs `steep check`
as part of `rails test`.
