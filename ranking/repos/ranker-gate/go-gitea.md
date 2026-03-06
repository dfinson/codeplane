# go-gitea/gitea

| Field | Value |
|-------|-------|
| **URL** | https://github.com/go-gitea/gitea |
| **License** | MIT |
| **Language** | Go |
| **Scale** | Large (multi-team project) |
| **Category** | Self-hosted Git service |
| **Set** | ranker-gate |
| **Commit** | `5d87bb3d4566e71b791a8114bfc9e25c037ab5fe` |

## Why this repo

- **No single developer knows it all**: Git operations, issue tracker, pull
  requests, code review, CI/CD (Actions), package registry, wiki, OAuth/SSO,
  API (REST + GraphQL-like), webhooks, notifications, admin dashboard, LFS —
  each a deep subsystem with its own models, services, and routes.
- **Well-structured**: Clean Go package layout with `models/`, `services/`,
  `routers/`, `modules/` separation. Models further split by domain
  (issues, repo, user, packages, actions, etc.).
- **Rich history**: 15K+ commits, highly active PR flow, Hacktoberfest
  participation. Feature-rich with varied development patterns.
- **Permissive**: MIT license.

## Structure overview

```
.
├── models/              # Database models and queries
│   ├── issues/          # Issues, labels, milestones, comments
│   ├── repo/            # Repository metadata, stars, forks
│   ├── user/            # User accounts, orgs, teams
│   ├── packages/        # Package registry
│   ├── actions/         # CI/CD actions
│   └── git/             # Git-specific models
├── services/            # Business logic layer
│   ├── repository/      # Repo creation, deletion, transfer
│   ├── issue/           # Issue lifecycle
│   ├── pull/            # PR merge, review, conflicts
│   ├── auth/            # Authentication sources
│   └── webhook/         # Webhook delivery
├── routers/             # HTTP route handlers
│   ├── api/             # REST API v1
│   ├── web/             # Web UI routes
│   └── private/         # Internal API
├── modules/             # Shared infrastructure
│   ├── git/             # Git command interface
│   ├── markup/          # Markdown, org-mode rendering
│   ├── indexer/         # Search indexing (bleve/elasticsearch)
│   └── notification/    # Notification delivery
└── cmd/                 # CLI commands
```

## Scale indicators

- ~1,500 Go source files
- ~300K+ lines of code
- Deep module hierarchies (3-5 levels)
- Cross-cutting auth, notification, indexing concerns

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix issue comment count not updated after bulk delete

When an admin bulk-deletes spam comments from an issue, the issue's
`num_comments` field is not decremented. The count only updates when
comments are deleted individually through the API or UI. Fix the bulk
delete operation to update the issue's comment count atomically.

### N2: Add "copy branch name" button to PR page

On the pull request page, there's no easy way to copy the source branch
name (for checking out locally). Add a clipboard copy button next to
the branch name display, similar to the existing "copy clone URL"
button on the repository page.

### N3: Fix webhook delivery retries using wrong HTTP method

When a webhook delivery fails and is retried, the retry uses POST
regardless of what method was configured for the webhook. The original
HTTP method is not persisted in the webhook delivery record. Fix the
retry logic to use the configured HTTP method.

### N4: Fix milestone progress percentage rounding to zero for 1-2 issues

When a milestone has only 1 or 2 issues and one is closed, the progress
bar shows 0% because integer division truncates the percentage. Fix the
milestone progress calculation to use proper rounding for small issue
counts.

### N5: Add `Closes #N` auto-linking in commit messages for wiki pages

Commit messages on repository code support auto-closing issues via
`Closes #123`, but commits via the wiki editor do not trigger issue
auto-close. Fix the wiki commit handler to parse and process issue
close keywords.

### N6: Fix user profile heatmap showing wrong day for timezone edge cases

The contribution heatmap on user profiles shows the wrong day for
contributions made near midnight in timezones far from UTC. The
server-side date grouping uses UTC without adjusting for the user's
configured timezone. Fix the heatmap query to group by the user's
local date.

### N7: Fix repository transfer not updating webhook delivery URLs

When a repository is transferred to a new owner, webhooks configured
with repository-relative URLs still point to the old owner's URL path.
The transfer operation does not update webhook URLs. Fix repository
transfer to update webhook URLs or mark them for review.

### N8: Add "Copy commit SHA" button on the commit detail page

The commit detail page shows the full SHA but has no clipboard button.
Add a click-to-copy button next to the commit SHA, consistent with
the existing copy buttons on the repository clone URL and branch name.

### N9: Fix LFS file edit in web editor creating corrupt LFS pointer

When editing an LFS-tracked file through Gitea's web editor, the saved
content is written directly to the repository instead of going through
the LFS pipeline. The result is a non-LFS file that looks like a
corrupt LFS pointer. Fix the web editor to route LFS file saves
through the LFS storage backend.

### N10: Fix email notification subject truncating unicode characters mid-codepoint

When an issue title contains multi-byte unicode characters and the
subject is truncated to fit the email subject length limit, the
truncation can split a multi-byte character. This produces invalid
UTF-8 in the email subject. Fix the truncation to respect unicode
codepoint boundaries.

## Medium

### M1: Add scheduled release publishing

The `Release` model in `models/repo/release.go` has `IsDraft` and
`IsPrerelease` fields, but no way to schedule a draft release for
automatic publishing at a future time. Add a `ScheduledPublishUnix`
field to the `Release` struct and a cron task in `services/cron/`
that publishes releases when their scheduled time is reached.
Update `services/release/release.go` to handle the scheduling logic
and `routers/api/v1/repo/release.go` to expose the field in the API.

### M2: Add saved replies for issue and PR comments

Users currently have no way to save frequently used comment
responses. Add a `SavedReply` model (title + content) linked to
`models/user/` with CRUD operations. Add API endpoints in
`routers/api/v1/user/` and a UI dropdown in the comment editor
(`templates/repo/issue/view_content/`) that inserts a saved
reply's Markdown content into the textarea. Support per-user
saved replies and organization-level shared replies via
`models/organization/`.

### M3: Add stale issue auto-close with configurable policy

Implement automatic closing of stale issues after a configurable
period of inactivity. Add a `StalePolicy` struct to the repository
settings in `models/repo/repo.go` with fields for inactivity
threshold, warning label, and exempt labels. Add a cron task in
`services/cron/tasks_extended.go` that queries `models/issues/`
for issues whose `UpdatedUnix` exceeds the threshold, applies a
"stale" label via `models/issues/issue_label.go`, posts a warning
comment, and closes them after a second grace period.

### M4: Add issue task list progress tracking

When an issue body contains Markdown task lists (`- [ ]` / `- [x]`),
track the completion count in the issue model. Add `TasksDone` and
`TasksTotal` fields to the `Issue` struct in `models/issues/issue.go`.
Parse task lists in `services/issue/content.go` when the issue body
is created or updated. Display a progress bar on issue list views
in `templates/shared/issuelist.tmpl`. Update the REST API response
in `services/convert/issue.go` to include task progress fields.

### M5: Add IP-based access restrictions for repositories

Add IP allowlist support for repository access. Create an
`AllowedIPRange` model linked to repositories and organizations
with CIDR range fields. Enforce the allowlist in the Git HTTP
handler (`routers/web/repo/githttp.go`), the API middleware
(`routers/api/v1/api.go`), and the SSH key authentication path
(`routers/private/serv.go`). Add management UI in the repository
settings and organization admin pages. Support both IPv4 and IPv6
ranges with validation in `modules/validation/`.

### M6: Add repository dependency graph visualization

Implement a dependency graph page that visualizes package dependencies
declared in the repository's manifest files (go.mod, package.json,
Gemfile, requirements.txt, etc.). Show a DAG of direct and transitive
dependencies. Highlight known security vulnerabilities. Link to the
dependency's repository if hosted on the same Gitea instance.

### M7: Add push mirror with tag and branch filtering

The `PushMirror` model in `models/repo/pushmirror.go` mirrors all
refs to the remote. Add configurable branch and tag pattern filters
(include/exclude globs) to the `PushMirror` struct. Update the
mirror push logic in `services/mirror/mirror_push.go` to apply
the filters when building the refspec list. Add filter configuration
fields to the push mirror API in `routers/api/v1/repo/mirror.go`
and the web UI form in `routers/web/repo/setting/` templates.

### M8: Add deploy key scoping with read/write per-branch permissions

Deploy keys in `models/asymkey/` currently grant repository-wide
read or read-write access. Add per-branch permission scoping so a
deploy key can be restricted to push only to specific branches.
Extend the `DeployKey` model with a `BranchPattern` field. Enforce
branch restrictions in the pre-receive hook (`routers/private/
hook_pre_receive.go`) by checking the deploy key's allowed branches
against the pushed ref. Update the deploy key API in
`routers/api/v1/repo/key.go` and web UI in repository settings.

### M9: Add time tracking reports with CSV export and label breakdown

The `TrackedTime` model in `models/issues/tracked_time.go` records
per-issue time entries, but there is no aggregated reporting or
export capability. Add a time tracking report page that aggregates
tracked time by user, milestone, and label for a repository over a
configurable date range. Implement CSV export of the report data.
Add the report route in `routers/web/repo/` and a corresponding
API endpoint in `routers/api/v1/repo/`. Query tracked times using
`FindTrackedTimesOptions` and join with `models/issues/issue_label.go`
for the label breakdown. Render the report using a new template in
`templates/repo/issue/`.

### M10: Add GPG signature verification for release tag assets

Release assets uploaded via `services/release/release.go` have no
integrity verification. Add GPG signature verification for release
tags by checking the tag signature against the uploader's GPG keys
in `models/asymkey/gpg_key.go`. Display a verification badge on the
release page template showing whether the tag is signed and by whom.
Extend `services/convert/release.go` to include signature status in
the API response. Add a `.asc` signature file auto-detection for
uploaded assets paired with their corresponding release attachment
in `models/repo/attachment.go`.

## Wide

### W1: Implement audit log for security-sensitive operations

Add a comprehensive audit log that records security-sensitive events
across the instance: login attempts, permission changes, repository
transfers (`services/repository/transfer.go`), team membership
changes (`models/organization/team_user.go`), deploy key additions
(`models/asymkey/`), two-factor auth changes (`models/auth/twofactor.go`),
and webhook modifications (`models/webhook/webhook.go`). Create an
`AuditEvent` model with actor, action, target, IP address, and
timestamp fields. Add a searchable admin UI in `routers/web/admin/`,
API endpoints in `routers/api/v1/admin/`, and retention policy
configuration. Hook into the notification service
(`services/notify/`) to capture events across all subsystems.

### W2: Implement secret scanning for committed credentials

Add a secret scanning system that detects accidentally committed
credentials (API keys, private keys, tokens) in repository pushes.
Create a `SecretScanRule` model with regex patterns for common
secret formats (AWS keys, GitHub tokens, private keys). Hook into
the post-receive pipeline (`routers/private/hook_post_receive.go`)
to scan pushed commits via `modules/git/`. Store findings in a
new `SecretScanAlert` model linked to repositories. Add alert
management UI in repository settings, notification delivery via
`services/mailer/`, webhook events in `modules/webhook/`, and
API endpoints. Support custom patterns per organization and
allow-listing of false positives.

### W3: Implement SSO with SAML 2.0 support

Add SAML 2.0 identity provider integration alongside the existing
OAuth2 and LDAP authentication sources. Implement SP-initiated and
IdP-initiated SSO flows. Support SAML attribute mapping to Gitea
user fields (email, username, groups → teams). Add JIT (just-in-time)
provisioning that creates Gitea accounts on first SAML login. Implement
single logout (SLO). Add admin UI for SAML configuration with
metadata import/export.

### W4: Add repository-level custom fields for issues and PRs

Implement user-defined custom fields (text, number, date, dropdown,
multi-select) that can be attached to issues and pull requests at
the repository level. Create a `CustomField` model and
`CustomFieldValue` model in `models/issues/` with field type
validation. Add field definition management in repository settings
(`routers/web/repo/setting/`). Render custom fields in the issue
creation form, issue sidebar, and issue list filtering. Update the
search/filter logic in `models/issues/issue_search.go` to support
querying by custom field values. Add API endpoints in
`routers/api/v1/repo/` and update `services/convert/issue.go` to
include custom fields in API responses. Support field inheritance
from organization-level defaults via `models/organization/`.

### W5: Implement saved search and custom issue dashboards

Add persistent saved searches that users can pin as custom dashboard
views. Create a `SavedSearch` model in `models/user/` storing the
query string, filter parameters (labels, milestones, assignees from
`models/issues/`), sort order, and display columns. Add a dashboard
page in `routers/web/user/` that renders multiple saved searches as
panels with live issue counts. Support organization-wide shared
searches via `models/organization/`. Add notification digests in
`services/mailer/` that email periodic summaries based on saved
search criteria. Expose saved search CRUD through the API in
`routers/api/v1/user/`. Changes span user models, issue search
infrastructure (`models/issues/issue_search.go`), web routes,
API routes, and the mailer service.

### W6: Add federated repository interaction via ActivityPub

Implement ActivityPub federation so users on one Gitea instance can
star, fork, and open issues/PRs on repositories hosted on a different
Gitea instance. Implement the Actor model for users and repositories,
Activity types for git operations, and inbox/outbox endpoints.
Handle authentication across instances. Changes span the user model,
repository model, API routing, notification system, and add a
federation module.

### W7: Implement Gitea Actions with reusable workflows

Add support for reusable workflows in Gitea Actions: a workflow can
call another workflow in the same or a different repository via
`uses: owner/repo/.gitea/workflows/reusable.yaml@ref`. Support input
parameters, output values, secrets passing, and nested workflow calls.
Includes workflow resolution, input validation, output propagation,
and the runner protocol changes.

### W8: Add advanced code search with code navigation

Implement code search with IDE-like features: go-to-definition from
search results, find-all-references in the repository, symbol search
across repositories in an organization, and search within specific
language constructs (search only in function bodies, or only in
comments). Uses the bleve indexer with language-aware tokenization.
Changes span the indexer, search API, web UI, and add a code
navigation module.

### W9: Implement organization-level security policies

Add organization-level security settings that cascade to all member
repositories: required branch protection rules, mandatory code review
policies, required CI checks, allowed merge strategies, secret
scanning rules, and dependency vulnerability policies. Organization
admins configure policies; repository admins cannot weaken them.
Changes span organization models, repository settings, branch
protection, and the admin UI.

### W10: Add built-in CI/CD with pipeline visualization

Extend Gitea Actions with a visual pipeline editor and live execution
visualization. Show a DAG of jobs with status indicators, live log
streaming per job, artifact browsing, and pipeline-level metrics.
Support matrix builds with visual expansion. Add pipeline templates
for common language/framework combinations. Changes span the Actions
runner, web UI, API, and storage for logs and artifacts.
