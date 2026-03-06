| Field    | Value                                              |
|----------|----------------------------------------------------|
| URL      | https://github.com/heartcombo/devise                |
| License  | MIT                                                 |
| Language | Ruby                                                |
| Scale    | Medium                                              |
| Category | Authentication / Rails                              |
| Set      | cutoff                                              |

## Why this repo

Devise is the most widely adopted authentication solution for Ruby on Rails. It provides a full-stack authentication framework built on top of Warden with a modular architecture of ten independent "modules" (database_authenticatable, registerable, recoverable, confirmable, lockable, timeoutable, trackable, validatable, omniauthable). Its deep integration with Rails routing, controllers, mailers, and generators makes it an excellent test subject for cross-cutting code navigation tasks. The codebase exercises ORM callbacks, Warden strategies, ActionMailer, Rails engines, and Rack middleware.

## Structure overview

```
devise/
├── app/
│   ├── controllers/
│   │   └── devise/
│   │       ├── sessions_controller.rb
│   │       ├── registrations_controller.rb
│   │       ├── passwords_controller.rb
│   │       ├── confirmations_controller.rb
│   │       ├── unlocks_controller.rb
│   │       └── omniauth_callbacks_controller.rb
│   ├── mailers/
│   │   └── devise/
│   │       └── mailer.rb
│   ├── helpers/
│   │   └── devise_helper.rb
│   └── views/
│       └── devise/
│           ├── sessions/
│           ├── registrations/
│           ├── passwords/
│           ├── confirmations/
│           ├── unlocks/
│           ├── mailer/
│           └── shared/
├── config/
│   └── locales/
│       └── en.yml
├── lib/
│   ├── devise.rb
│   ├── devise/
│   │   ├── controllers/
│   │   │   ├── helpers.rb
│   │   │   ├── sign_in_out.rb
│   │   │   ├── store_location.rb
│   │   │   ├── url_helpers.rb
│   │   │   └── responder.rb
│   │   ├── hooks/
│   │   │   ├── activatable.rb
│   │   │   ├── csrf_cleaner.rb
│   │   │   ├── lockable.rb
│   │   │   ├── timeoutable.rb
│   │   │   ├── trackable.rb
│   │   │   └── proxy.rb
│   │   ├── models/
│   │   │   ├── database_authenticatable.rb
│   │   │   ├── registerable.rb
│   │   │   ├── recoverable.rb
│   │   │   ├── confirmable.rb
│   │   │   ├── lockable.rb
│   │   │   ├── timeoutable.rb
│   │   │   ├── trackable.rb
│   │   │   ├── validatable.rb
│   │   │   └── omniauthable.rb
│   │   ├── strategies/
│   │   │   ├── authenticatable.rb
│   │   │   ├── database_authenticatable.rb
│   │   │   └── rememberable.rb
│   │   ├── encryptor.rb
│   │   ├── failure_app.rb
│   │   ├── mapping.rb
│   │   ├── models.rb
│   │   ├── parameter_sanitizer.rb
│   │   ├── rails.rb
│   │   ├── routes.rb
│   │   ├── secret_key_finder.rb
│   │   ├── test_helpers.rb
│   │   ├── token_generator.rb
│   │   └── version.rb
│   └── generators/
│       └── devise/
│           ├── devise_generator.rb
│           ├── install_generator.rb
│           ├── orm_helpers.rb
│           └── views_generator.rb
└── test/
    ├── controllers/
    ├── generators/
    ├── integration/
    ├── models/
    └── support/
```

## Scale indicators

- ~15k lines of Ruby source
- 10 pluggable authentication modules
- 6 controllers with full request lifecycle
- Warden strategy layer with multiple strategies
- ActionMailer integration for transactional emails
- Custom Rails route DSL (devise_for)
- Generator framework for scaffolding
- Comprehensive i18n with locale files
- Rack-level failure app for unauthenticated requests

## Tasks

## Narrow

### N1 – Fix password minimum length not applying on update

Users report that the minimum password length validation fires correctly during registration but is silently skipped when updating a password through the registrations controller. The validatable module should enforce length constraints uniformly on both create and update paths.

### N2 – Confirmation token expiry returns wrong HTTP status

When a user clicks a confirmation link after the token has expired, the confirmations controller renders the form with validation errors but returns a 200 status code. It should return 422 so API consumers can distinguish between an invalid token and a successful page load.

### N3 – Unlock instructions email not respecting custom mailer sender

The global mailer_sender configuration is ignored when sending unlock instructions. The unlock mailer action falls back to the default from address instead of reading the configured sender, unlike all other mailer actions which correctly pick it up.

### N4 – Trackable module records sign-in before Warden callback completes

The trackable hook updates sign_in_count and timestamps before the Warden after_set_user callback chain finishes. If a later callback aborts authentication by throwing :warden, the tracking data is persisted for a session that was never established.

### N5 – Token generator generate loop not bounding retry attempts

The `generate` method in `token_generator.rb` uses an unbounded
`loop do` block that retries until `find_first` confirms the generated
token is unique in the database. If the database connection drops, the
query times out, or the token column lacks an index causing slow
lookups, this loop runs indefinitely without any iteration limit or
timeout. Fix the `generate` method to cap retry iterations at a
reasonable bound and raise a descriptive error when uniqueness cannot
be achieved within the limit.

### N6 – flash message key mismatch for already-confirmed accounts

When a user who is already confirmed clicks the confirmation link again, the flash message uses a key that does not exist in the default locale file, resulting in a raw i18n key being displayed instead of a human-readable message.

### N7 – Timeoutable does not clear remember-me cookie on timeout

When a session times out due to the timeoutable module, the remember-me cookie is left intact. The user is immediately re-authenticated by the rememberable strategy, making the timeout effectively inoperative for users who checked "remember me."

### N8 – ParameterSanitizer raises on nested strong parameters

When a model has custom devise parameter sanitizer rules that include nested attributes (e.g., address attributes inside registration), the sanitizer raises a NoMethodError because it calls permit on a symbol array without handling hash entries.

### N9 – Confirmable module does not clear stale confirmation_token after successful confirmation

When `confirm()` succeeds in `models/confirmable.rb`, it sets
`confirmed_at` and saves the record but does not nil out
`confirmation_token`. Although replay is prevented by the `confirmed?`
guard in `pending_any_confirmation`, the stale HMAC token digest
remains in the database indefinitely. If the database is compromised,
these stale digests reveal information about the token derivation.
Fix `models/confirmable.rb` to set `self.confirmation_token = nil`
in the `confirm` method after successfully setting `confirmed_at`.

### N10 – Route helpers undefined inside engine-mounted devise

When Devise is mounted inside a Rails engine rather than the main application, the URL helpers generated by the routes module raise NoMethodError because they reference main_app instead of the engine's route proxy.

## Medium

### M1 – Add rate limiting to the sessions controller

The sessions controller does not limit login attempts at the application layer. Add a configurable rate-limiting mechanism that tracks failed sign-in attempts per IP and locks out further attempts for a configurable cooldown period, integrating with the existing lockable module's accounting where possible.

### M2 – Support multiple simultaneous devise models in failure app

The failure app assumes only one resource is being authenticated per request. When an application uses two devise models (e.g., User and Admin), a failed authentication for Admin incorrectly redirects to the User sign-in path. The failure app should inspect the attempted mapping and route to the correct scope's sign-in page.

### M3 – Add password breach detection using haveibeenpwned integration

Integrate a password breach check that queries the Have I Been Pwned
passwords API (k-anonymity model) during registration and password
change. When a password appears in known breaches, reject it with a
configurable validation error. This requires changes to the validatable
module for the new validation rule, the registrations controller for
flash message handling, the database_authenticatable model for the API
client call, the configuration module for the opt-in flag and threshold,
and new i18n entries in the locale file.

### M4 – Integrate CSRF token rotation on sign-in

Rails has the ability to rotate CSRF tokens on sign-in to prevent session-fixation attacks. Devise's sign-in flow does not trigger this rotation. Wire the csrf_cleaner hook and the sessions controller to call the appropriate Rails method so the CSRF token is regenerated after every successful authentication.

### M5 – Custom Warden strategy for API token authentication

Add a new Warden strategy that authenticates requests via a bearer token in the Authorization header. The strategy should look up the token in a configurable model column, fall through to the next strategy on failure, and integrate with Devise's mapping and scope system so it works alongside database_authenticatable.

### M6 – Generate scoped mailer previews

The install generator creates mailer views but does not generate ActionMailer preview classes. Add a generator command that creates preview classes for each devise mailer action scoped to the resource, allowing developers to preview confirmation, reset password, and unlock emails in the Rails mailer preview UI.

### M7 – Extend lockable module with exponential backoff

The lockable module currently locks an account after a fixed number of failed attempts with a fixed unlock-after duration. Extend it to support exponential backoff: each successive lockout should double the unlock delay, and the attempt counter should decay over a configurable window rather than resetting only on successful sign-in.

### M8 – Add webauthn as a devise module

Create a new devise module (webauthn_authenticatable) that allows users to register hardware security keys and authenticate with them. The module needs a model concern for credential storage, a Warden strategy that verifies assertions, controller actions for registration and authentication ceremonies, and routes.

### M9 – Refactor test helpers for system tests

The existing test helpers (sign_in and sign_out) manipulate the Warden proxy directly, which works for integration tests but fails in Rails system tests that drive a real browser through Capybara. Refactor the helpers to detect the test type and, for system tests, perform sign-in through the actual sessions controller form submission.

### M10 – Support per-model mailer classes

All devise models currently share a single Devise::Mailer class. Add the ability to configure a custom mailer class per model (e.g., AdminMailer for the Admin resource). The mailer resolution logic, the generator, and the controller deliver calls all need to respect this per-model override.

## Wide

### W1 – Extract Devise modules into independent Rails engines

Refactor each of the ten devise modules (database_authenticatable, registerable, recoverable, confirmable, lockable, timeoutable, trackable, validatable, omniauthable, and rememberable) into individual Rails engine gems within the repository. Each engine should register its own routes, controllers, model concerns, Warden strategies, mailer actions, hooks, generators, locale entries, and views while preserving the ability to compose them via the devise method in a model.

### W2 – Full passwordless authentication flow

Implement end-to-end passwordless authentication. This spans the models layer (a new passwordless module that generates magic-link tokens), the mailer (a new magic-link email template), the routes (a dedicated endpoint for magic-link verification), a new Warden strategy, controller actions, token expiry and single-use enforcement, the failure app (handling expired magic links), i18n entries, generators, and test coverage across all layers.

### W3 – Multi-tenancy support across all devise components

Add first-class multi-tenancy support so that devise configuration, routes, mailers, failure-app behavior, strategies, and lockable/timeoutable thresholds can all be scoped to a tenant. This requires changes to the global configuration module, the mapping system, route generation, controller filters, the Warden proxy, and the parameter sanitizer.

### W4 – Migrate all callbacks from ActiveRecord to ActiveModel

Devise's model modules use ActiveRecord-specific callbacks (before_save, after_create) which break when using alternative ORMs like Mongoid. Systematically replace every ActiveRecord callback across all ten modules with ActiveModel equivalents, update the ORM helpers in the generators, adjust the test suite's ORM setup, and verify that both ActiveRecord and Mongoid pass the full test suite.

### W5 – Introduce event-driven audit logging

Add a cross-cutting audit log system that records all authentication-related events: sign-in, sign-out, failed attempts, password changes, email changes, account locks, unlocks, confirmations, and token generations. This requires hooking into every controller action, every Warden callback, every model lifecycle event, adding a log storage backend, a query interface, and configuration to control retention and verbosity.

### W6 – Internationalize all flash messages with pluralization and context

The i18n layer currently uses simple key lookups without pluralization or contextual variants. Overhaul every flash message across all controllers, the failure app, and mailer subjects to use Rails' full i18n pluralization and context features. Add locale files for the top five languages, update the generators to scaffold locale files, and ensure the test suite validates every key exists in every locale.

### W7 – Replace hand-rolled token generation with a unified token service

Token generation is duplicated across the recoverable, confirmable, lockable, and unlock modules, each with slightly different expiry and hashing semantics. Extract a single TokenService class that handles generation, hashing, storage, validation, expiry, and single-use semantics. Migrate all four modules plus the rememberable strategy to use this service, update the routes and controllers that consume tokens, and adapt the test suite.

### W8 – Add OAuth2 provider capability

Extend Devise so that an application can act as an OAuth2 authorization server. This requires new models (Application, AccessToken, AuthorizationCode), new controllers (authorizations, tokens), new routes, a token strategy, scope enforcement across all existing controllers, mailer notifications for authorized applications, views for the authorization consent screen, generators, and i18n.

### W9 – Build a mountable admin dashboard engine

Create a Devise::Admin Rails engine that provides a web dashboard for managing users across all devise-enabled models. The engine must read from every model module (lockable status, confirmation state, trackable data, etc.), provide controllers for listing, searching, locking, unlocking, confirming, and impersonating users, integrate with the route generator, respect authorization scopes, and include its own views and assets.

### W10 – End-to-end two-factor authentication with recovery codes

Implement full TOTP-based two-factor authentication as a new devise module. This spans model concerns (secret storage, recovery code generation), a Warden strategy (TOTP verification after primary auth), new controllers (2FA setup, verification, recovery), new routes, new mailer actions (2FA enabled notification, recovery code regeneration), views, generators for migration and views, i18n entries, hooks to enforce 2FA on sign-in, and updates to the failure app for 2FA-required redirects.
