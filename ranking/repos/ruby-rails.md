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

10 tasks (3 narrow, 4 medium, 3 wide) for the Ruby full-stack web framework.

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
