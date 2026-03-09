| Field | Value |
|-------|-------|
| **URL** | https://github.com/heartcombo/devise |
| **License** | MIT |
| **Language** | Ruby |
| **Scale** | Medium |
| **Category** | Authentication / Rails |
| **Set** | cutoff |
| **Commit** | `c9e655e13253dc53e3c0981a8345f134bcda1fc5` |

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

### N2 – Confirmations create action returns 200 on failure for API consumers

When a JSON API client calls `POST /users/confirmation` to resend confirmation instructions and the email is not found in the database, the confirmations controller calls `respond_with(resource)` without an explicit HTTP status. Rail's default responder returns 200 rather than 422 when the resource has errors, so JSON API clients cannot distinguish a successful send from a failure. Fix the `create` action in `app/controllers/devise/confirmations_controller.rb` to return a 422 status code when the resource has errors.

### N3 – Mailer headers_for sets reply-to to same value as from with no override

The `headers_for` method in `lib/devise/mailers/helpers.rb` always sets both the `from` and `reply_to` email headers to the value returned by `mailer_sender(devise_mapping)`. When applications configure `Devise.mailer_sender` to a no-reply address (e.g., `"noreply@example.com"`), the `reply_to` header also points to that unmonitored address. There is no Devise configuration option for a separate reply-to address. Add a `Devise.mailer_reply_to` configuration attribute to `lib/devise.rb` (defaulting to `nil` so no reply-to is set by default) and update `headers_for` in `lib/devise/mailers/helpers.rb` to use it when present.

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

### N6 – Already-confirmed users get a form error instead of a redirect with flash

When a user who is already confirmed clicks a confirmation link again, `confirm_by_token` in `models/confirmable.rb` calls `pending_any_confirmation` which adds an `already_confirmed` error to the resource and returns false. The confirmations controller `show` action then renders the `:new` confirmation form with a 422 status, which is confusing because the form cannot be used to fix the situation. The controller should detect the `already_confirmed` error and instead redirect to the sign-in page with a user-friendly flash message. Fix the `show` action in `app/controllers/devise/confirmations_controller.rb` to redirect with `set_flash_message!(:notice, :already_confirmed)` when the resource has only the `already_confirmed` error, and add the `devise.confirmations.already_confirmed` key to `config/locales/en.yml`.

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

### N11 – CHANGELOG.md missing security entry for confirmation token hardening

The `CHANGELOG.md` has no entry documenting the confirmation-token-clearing change introduced by the `confirmable.rb` fix in N9. Security-relevant changes must be called out in the changelog so downstream users know to upgrade. Additionally, the `ISSUE_TEMPLATE.md` does not ask reporters which Devise modules are enabled on their model (e.g., `:database_authenticatable`, `:confirmable`, `:lockable`), making it harder to reproduce bugs that only affect specific module combinations. Fix `CHANGELOG.md` to add a Security section entry for the confirmation-token-clearing change, and update `ISSUE_TEMPLATE.md` to include a "Devise modules enabled" field in the Environment section.

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
new i18n entries in `config/locales/en.yml`, a feature section added to
`README.md` documenting the opt-in configuration, and a `CHANGELOG.md`
entry under a new "Features" heading.

### M4 – No session ID regeneration on sign-in allows session fixation

After a successful sign-in, Devise does not rotate the session identifier, leaving users vulnerable to session fixation attacks. An attacker who can plant a known session cookie before a victim authenticates can reuse that session after the victim signs in. The `sign_in` helper in `lib/devise/controllers/sign_in_out.rb` clears only `devise.*` session keys via `expire_data_after_sign_in!` but does not regenerate the underlying session ID. Fix this by adding a configurable `Devise.reset_session_on_sign_in` flag (defaulting to `true`) in `lib/devise.rb`, updating `expire_data_after_sign_in!` in `lib/devise/controllers/sign_in_out.rb` to call `request.reset_session` and restore the Devise session data when the flag is enabled, updating `app/controllers/devise/sessions_controller.rb` and `app/controllers/devise/registrations_controller.rb` to correctly preserve the after-sign-in redirect URL across the session reset, and verifying that the integration test helpers in `lib/devise/test/integration_helpers.rb` continue to work correctly.

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

### M11 – Overhaul CONTRIBUTING.md and bug report template for modern workflows

The `CONTRIBUTING.md` file references outdated Ruby versions and does not mention the GitHub Actions CI pipeline defined in `.github/workflows/test.yml`. The bug report template in `guides/bug_report_templates/integration_test.rb` hard-codes an old Devise version. Update `CONTRIBUTING.md` to document the current CI setup, link to the GitHub Actions workflow, and describe how to run the full test suite locally. Update the bug report template to dynamically detect the installed Devise version instead of hard-coding it, and add a section to `CODE_OF_CONDUCT.md` with updated enforcement contact information.

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

### W11 – Comprehensive documentation and locale overhaul across all devise modules

Several documentation and internationalization gaps exist across the repository. `SECURITY.md` does not exist; the security vulnerability reporting process (emailing heartcombo.oss@gmail.com) is buried in `CONTRIBUTING.md` and not discoverable via GitHub's standard security advisory flow. `CONTRIBUTING.md` does not mention the GitHub Actions CI pipeline defined in `.github/workflows/test.yml` nor how to run the test suite locally with the correct `DEVISE_ORM` and `BUNDLE_GEMFILE` environment variables. The bug report template at `guides/bug_report_templates/integration_test.rb` hard-codes old gem versions (`gem 'rails', '~> 4.2.0'` and `gem 'devise', '~> 4.0'`) that no longer reflect the minimum supported versions. `README.md` does not document the `:omniauth_providers` configuration key or how to configure multiple OmniAuth providers, leaving the omniauthable module's configuration options undocumented. `config/locales/en.yml` is the only locale file shipped with the gem. Create `SECURITY.md` documenting the responsible disclosure process, update `CONTRIBUTING.md` to explain the CI setup and local test invocation, update `guides/bug_report_templates/integration_test.rb` to use current version constraints, update `README.md` to add a complete omniauthable configuration reference, and add `config/locales/fr.yml` and `config/locales/es.yml` with translations for all keys present in `en.yml`.
