# sinatra/sinatra

| Field | Value |
|-------|-------|
| **URL** | https://github.com/sinatra/sinatra |
| **License** | MIT |
| **Language** | Ruby |
| **Scale** | Medium |
| **Category** | Web DSL / micro-framework |
| **Set** | eval |
| **Commit** | `b2c6e2087d5e12c6bddcdfa8703ac94c7c4cfad7` |

## Why this repo

- **Monorepo with three gems**: Core sinatra, sinatra-contrib (extensions), rack-protection (security middleware)
- **Dense DSL core**: Routing, templates, filters, error handlers, helpers, settings, and middleware all defined in a single 2100-line `base.rb`
- **Rich middleware layer**: 15+ rack-protection strategies (CSRF, session hijacking, IP spoofing, XSS, etc.)

## Structure overview

```
sinatra/
├── lib/sinatra/
│   ├── base.rb              # Core: Request, Response, Helpers, Templates, Base, Application
│   ├── main.rb              # Top-level DSL (delegates to Sinatra::Application)
│   ├── indifferent_hash.rb  # Hash subclass with indifferent access
│   ├── show_exceptions.rb   # Debug exception page
│   ├── middleware/logger.rb  # Rack-compatible logger middleware
│   └── version.rb
├── rack-protection/lib/rack/protection/
│   ├── base.rb              # Base protection middleware
│   ├── authenticity_token.rb # CSRF token validation
│   ├── content_security_policy.rb
│   ├── cookie_tossing.rb
│   ├── escaped_params.rb
│   ├── form_token.rb
│   ├── frame_options.rb
│   ├── host_authorization.rb
│   ├── http_origin.rb
│   ├── ip_spoofing.rb
│   ├── json_csrf.rb
│   ├── path_traversal.rb
│   ├── referrer_policy.rb
│   ├── remote_referrer.rb / remote_token.rb
│   ├── session_hijacking.rb
│   ├── strict_transport.rb
│   └── xss_header.rb
└── sinatra-contrib/lib/sinatra/
    ├── namespace.rb          # URL namespace scoping
    ├── reloader.rb           # Code reloading in development
    ├── streaming.rb          # EventSource / streaming responses
    ├── json.rb               # JSON response helper
    ├── respond_with.rb       # Content negotiation
    ├── cookies.rb            # Cookie jar helper
    ├── content_for.rb        # Template content blocks
    ├── config_file.rb        # YAML config loader
    ├── capture.rb            # Template capture helper
    ├── multi_route.rb        # Multiple route patterns
    ├── required_params.rb    # Parameter validation
    ├── custom_logger.rb      # Logger injection
    ├── link_header.rb        # Link header helper
    ├── webdav.rb             # WebDAV verb support
    └── extension.rb          # Extension registration DSL
```

## Scale indicators

- ~150 Ruby source files across all three gems
- ~7 600 lines of library code
- 2 100-line core file (`base.rb`) with DSL, routing, templates, filters, and settings
- 15+ independent rack-protection middleware strategies
- 20+ sinatra-contrib extensions

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `IndifferentHash#merge` not returning an `IndifferentHash`

In `lib/sinatra/indifferent_hash.rb`, `merge` delegates to `Hash#merge` which returns a plain `Hash`. Downstream code that calls `params.merge(extra).key?(:foo)` with symbol keys silently fails because the returned hash is no longer indifferent.

### N2: Fix `Request#preferred_type` returning `nil` when all `Accept` entries have `q=0`

In `base.rb`, `preferred_type` calls `accept.detect` which returns `nil` when no `AcceptEntry` matches. The method should return the first type passed by the caller as a fallback when the client explicitly rejects everything, rather than `nil` which causes a `NoMethodError` on `to_s`.

### N3: Fix `Response#finish` not setting `content-length` for streaming bodies

In `base.rb`, `Response#finish` calculates `content-length` by calling `body.map(&:bytesize)`. If body is a streaming `IO` or `Enumerator`, `map` consumes the stream and the response body is empty. The length calculation should be skipped for non-rewindable bodies.

### N4: Fix `Rack::Protection::AuthenticityToken` not rotating tokens on session regeneration

In `rack-protection/lib/rack/protection/authenticity_token.rb`, the CSRF token is derived from `session[:csrf]`. When the session ID is regenerated (e.g., after login), the old session hash persists and the CSRF token remains valid for the old session, allowing session fixation attacks to reuse the token. Document the token rotation behavior and mitigation in `SECURITY.md`.

### N5: Fix `Rack::Protection::HostAuthorization` not normalising port numbers in permitted hosts

In `rack-protection/lib/rack/protection/host_authorization.rb`, `permitted_hosts` are compared with exact string match against `request.host_with_port`. When the default port (80/443) is omitted from the Host header but included in the permitted list (or vice versa), the check incorrectly rejects legitimate requests.

### N6: Fix `Rack::Protection::PathTraversal` not decoding percent-encoded sequences before checking

In `rack-protection/lib/rack/protection/path_traversal.rb`, the middleware checks for `..` in the request path. Attackers can bypass this by double-encoding (`%252e%252e`). The middleware should decode the path fully before checking for traversal sequences.

### N7: Fix `Base.route` not unescaping pattern captures for UTF-8 path segments

In `base.rb`, `route!` uses Mustermann to match the pattern but the captured params are URL-encoded byte strings. When the route pattern is `/users/:name` and the path is `/users/caf%C3%A9`, `params[:name]` is `"caf%C3%A9"` instead of `"café"`. The captures should be decoded with `URI.decode_www_form_component`.

### N8: Fix `ShowExceptions` HTML template not escaping exception messages

In `lib/sinatra/show_exceptions.rb`, the error page interpolates `exception.message` directly into HTML. If the message contains user input (e.g., from a `raise "Invalid param: #{params[:q]}"`), it creates an XSS vector in development mode. The message should be HTML-escaped.

### N9: Fix `Sinatra::Cookies` helper not setting `SameSite` attribute

In `sinatra-contrib/lib/sinatra/cookies.rb`, cookies set via the `cookies` helper do not include the `SameSite` attribute. Modern browsers default to `Lax`, but the helper should explicitly set `SameSite=Lax` (or a configurable value) to match the application's CSRF protection expectations from rack-protection.

### N10: Fix `before` filter halt not preventing `after` filters from running

In `base.rb`, when a `before` filter calls `halt`, the `after` filters in `dispatch!` still execute because the `catch(:halt)` in `invoke` unwinds to `call!` which then runs `after_filter!`. After-filters should be skipped when the response was halted from a before-filter.

### N11: Fix `CHANGELOG.md` not attributing security fixes to CVE identifiers

The `CHANGELOG.md` lists security fixes as regular bug fixes without linking to CVE identifiers or the `SECURITY.md` advisory process. Add CVE cross-references to existing security-related changelog entries, establish a changelog format convention in `CONTRIBUTING.md`, and update `RELEASING.md` with a security release checklist that includes CVE attribution and `VERSION` file bump procedures.

## Medium

### M1: Add conditional route matching with request-header predicates

Implement `get '/api/data', provides: 'json', user_agent: /Mobile/i do ... end` where route matching considers arbitrary request-header conditions. Requires changes to `Base.route` (to store header conditions), `Base.route!` (to check conditions during matching), and `Base.compile!` (to compile condition procs).

### M2: Implement rate limiting middleware for rack-protection

Add `Rack::Protection::RateLimiter` that throttles requests per IP using a sliding-window algorithm. Store counters in Rack session or a configurable backend. Changes span a new `rate_limiter.rb` in `rack-protection/lib/rack/protection/`, `protection.rb` registration, and configuration in `base.rb` default middleware.

### M3: Add streaming Server-Sent Events with heartbeat and reconnection

Extend `sinatra-contrib/lib/sinatra/streaming.rb` to support SSE with `event:`, `id:`, and `retry:` fields. Add automatic heartbeat comments to keep connections alive and client reconnection via `Last-Event-ID` header. Changes span `streaming.rb` (new `EventStream` class), `Helpers` in `base.rb` (new `event_stream` method), and response header handling.

### M4: Implement route versioning through Accept header

Add `get '/users', version: 2 do ... end` that matches when the `Accept` header contains `application/vnd.app.v2+json`. Requires changes to `Base.route` (version metadata), `Base.route!` (accept-header version extraction), and `Helpers` (version accessor), and `respond_with.rb` for content negotiation integration.

### M5: Add request body validation with schema support

Implement `post '/items', schema: ItemSchema do ... end` that validates the parsed request body against a schema object before invoking the route block. Requires changes to `Base.route` (schema storage), `Base.process_route` (validation step), error handling integration in `Base.dispatch!`, and a new `sinatra/validation.rb` contrib extension.

### M6: Implement session encryption for rack-protection

Add `Rack::Protection::EncryptedSession` middleware that encrypts session data at rest using AES-256-GCM. Changes span a new `encrypted_session.rb` in `rack-protection/lib/rack/protection/`, key derivation from the app secret in `base.rb`, and integration with session middleware. Update `README.md` with session encryption configuration instructions and add a security hardening section to `.github/copilot-instructions.md`.

### M7: Add template caching with dependency invalidation for the reloader

`sinatra-contrib/lib/sinatra/reloader.rb` reloads Ruby source files but does not invalidate cached templates that depend on modified helpers. Implement dependency tracking between templates and their helper modules. Changes span `reloader.rb` (file-dependency graph), `Templates` module in `base.rb` (cache invalidation hook), and `engine_tracking.rb`.

### M8: Implement multi-route error handling with typed exception matchers

Extend `error` blocks to support exception class patterns: `error DatabaseError, status: 503 do ... end` with priority ordering by exception class specificity. Requires changes to `Base.error` (store exception class → handler mapping), `Base.dispatch!` (match by `Exception#is_a?` hierarchy), and `Helpers#error` (status code integration).

### M9: Add namespace-scoped middleware support

Extend `sinatra-contrib/lib/sinatra/namespace.rb` to allow `namespace '/admin' do use AdminAuth; ... end` where middleware only applies to routes within that namespace. Requires changes to `namespace.rb` (middleware storage), `Base.route!` (conditional middleware insertion), and `Base.build` (middleware stack scoping).

### M10: Implement content security policy nonce injection

Extend `rack-protection/lib/rack/protection/content_security_policy.rb` to auto-generate a per-request nonce, inject it into the CSP `script-src` directive, and expose it via `env['rack.csp_nonce']` for use in templates. Changes span `content_security_policy.rb` (nonce generation and header rewriting), `Helpers` in `base.rb` (nonce accessor), and `show_exceptions.rb` (use nonce for inline scripts).

### M11: Modernize CI test matrix and release workflow

Extend `.github/workflows/test.yml` with a Ruby version matrix (3.1, 3.2, 3.3) and platform matrix (ubuntu, macos). Add an automated release workflow in `.github/workflows/release.yml` that reads the `VERSION` file, builds all three gems (`sinatra`, `sinatra-contrib`, `rack-protection`), and publishes to RubyGems. Update `Gemfile` with development dependency groups, configure `.rubocop.yml` with monorepo-specific settings per gem directory, and add a `.github/CODEOWNERS` file mapping gem directories to maintainers. Changes span `.github/workflows/test.yml`, `.github/workflows/release.yml`, `.github/CODEOWNERS`, `Gemfile`, `.rubocop.yml`, `VERSION`, and `sinatra.gemspec`.

## Wide

### W1: Implement WebSocket support with route DSL integration

Add `websocket '/chat' do |ws| ... end` to the routing DSL with full lifecycle callbacks (`on_open`, `on_message`, `on_close`). Requires changes to `base.rb` (new route verb, upgrade detection in `route!`), a new `sinatra/websocket.rb` extension, rack hijacking integration, `streaming.rb` adaptation for bi-directional IO, and `namespace.rb` support.

### W2: Add background job processing framework

Implement `perform_later :send_email, user_id: 1` with an in-process thread pool and optional Redis-backed persistence. Changes span a new `sinatra/jobs.rb` contrib extension (job registration, queue, worker pool), `base.rb` (helper method, settings), `reloader.rb` (job class reloading), error handling integration in `dispatch!`, and a `sinatra/jobs/dashboard.rb` status route.

### W3: Implement modular application composition with mount DSL

Add `mount AdminApp, at: '/admin'` that mounts sub-applications with isolated middleware stacks and settings inheritance. Changes span `base.rb` (mount registration, routing delegation in `route!`, settings cascade), `main.rb` (top-level mount support), `namespace.rb` (interaction with mounted apps), rack-protection (per-mount security policies), and `reloader.rb` (sub-app reloading).

### W4: Add GraphQL endpoint support with schema DSL

Implement `graphql '/graphql', schema: AppSchema do ... end` integrating schema definition, query execution, and subscription support. Changes span a new `sinatra/graphql.rb` contrib extension (schema DSL, execution), `base.rb` routing (GraphQL verb handling), `streaming.rb` (subscription streaming), `respond_with.rb` (GraphQL content type), and error handling integration.

### W5: Implement request tracing with OpenTelemetry integration

Add distributed tracing across the request lifecycle: middleware → filters → route execution → template rendering → streaming. Changes span `base.rb` (span creation in `call!`, `route!`, `render`), rack-protection middlewares (trace context propagation), `streaming.rb` (span lifecycle for long-running streams), `namespace.rb` (scoped span naming), and a new `sinatra/tracing.rb` contrib extension.

### W6: Add comprehensive test helpers framework

Implement `Sinatra::TestHelpers` with mock session management, request factory methods, response assertions, streaming test support, and time-travel for cookie expiration testing. Changes span a new `test_helpers.rb` redesign in sinatra-contrib (request builder, assertion helpers), `base.rb` (test mode hooks), `cookies.rb` (test accessors), `streaming.rb` (synchronous test mode), and rack-protection (test bypass mode).

### W7: Implement hot code reloading with zero-downtime for production

Extend `sinatra-contrib/lib/sinatra/reloader.rb` to support production-grade reloading: fork-and-replace worker model, connection draining, middleware stack rebuild, template cache invalidation, and route table swap. Changes span `reloader.rb` (worker management), `base.rb` (atomic route table swap, middleware rebuild), `main.rb` (signal handling), rack-protection (session migration), and `streaming.rb` (connection handoff).

### W8: Add internationalization (i18n) framework

Implement `t(:greeting, name: user.name)` with locale detection from Accept-Language, URL prefix, and session. Changes span a new `sinatra/i18n.rb` contrib extension (translation loading, locale resolution, pluralization), `base.rb` (locale-aware routing, helper registration), `respond_with.rb` (locale-based content negotiation), `config_file.rb` (translation file loading), and template rendering integration.

### W9: Implement API documentation generation from route metadata

Add `desc 'List users'; param :page, Integer, default: 1` annotations that generate OpenAPI specs. Changes span `base.rb` (route metadata storage in `route`, `desc` DSL method, `param` DSL method), a new `sinatra/api_doc.rb` contrib extension (OpenAPI schema generation), `respond_with.rb` (documentation endpoint), `namespace.rb` (namespace-level metadata), and `json.rb` (schema serialization).

### W10: Add multi-tenant support with per-tenant configuration isolation

Implement `tenant { |req| req.host.split('.').first }` with isolated settings, middleware stacks, sessions, and CSRF tokens per tenant. Changes span `base.rb` (tenant resolution in `call!`, settings isolation, middleware scoping), `main.rb` (top-level tenant DSL), rack-protection (per-tenant CSRF tokens in `authenticity_token.rb`, per-tenant sessions in `session_hijacking.rb`), `cookies.rb` (tenant-scoped cookie jar), and `namespace.rb` (tenant-aware namespaces).

### W11: Create unified contributor documentation and maintenance guides

Consolidate and overhaul the project's extensive non-code documentation: unify `CONTRIBUTING.md`, `MAINTENANCE.md`, and `RELEASING.md` into a coherent contributor handbook with cross-references; update `AUTHORS.md` with a contribution attribution process; modernize `CODE_OF_CONDUCT.md` to the latest Contributor Covenant; add development setup instructions to `CONTRIBUTING.md` covering all three gems; update `CHANGELOG.md` format to follow Keep a Changelog with per-gem sections for `sinatra`, `sinatra-contrib`, and `rack-protection`; document the security advisory process in `SECURITY.md` with responsible disclosure timeline; and update `README.md` with badges, monorepo structure overview, and links to all documentation files. Changes span `CONTRIBUTING.md`, `MAINTENANCE.md`, `RELEASING.md`, `AUTHORS.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, `SECURITY.md`, `README.md`, and `VERSION`.
