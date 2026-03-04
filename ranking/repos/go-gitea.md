# go-gitea/gitea

| Field | Value |
|-------|-------|
| **URL** | https://github.com/go-gitea/gitea |
| **License** | MIT |
| **Language** | Go |
| **Scale** | Large (multi-team project) |
| **Category** | Self-hosted Git service |

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

10 tasks (3 narrow, 4 medium, 3 wide) for the self-hosted Git service.

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

## Medium

### M1: Implement issue templates with YAML frontmatter

Add support for issue templates defined in `.gitea/ISSUE_TEMPLATE/`
with YAML frontmatter for metadata (title prefix, labels, assignees,
projects). Support template types: bug report, feature request, and
custom. Render the template form in the "New Issue" UI with typed
form fields (text input, textarea, dropdown, checkboxes) defined
in the YAML. Validate required fields before submission.

### M2: Add code owners with required reviews

Implement a CODEOWNERS file (`.gitea/CODEOWNERS`) that maps file
patterns to responsible teams or users. When a PR modifies files
matching a pattern, automatically request reviews from the code
owners. Add a "code owner approval required" branch protection rule
that blocks merging until at least one code owner approves. Show
code owner status on the PR files changed view.

### M3: Implement repository archival with read-only mode

Add a "repository archive" feature that puts a repository into a
read-only state — no new pushes, PRs, or issues, but all existing
content remains accessible. Show an "archived" banner on the
repository page. Allow un-archiving by the repository owner. Update
the API, webhooks, and repository listing/search to respect archive
status.

### M4: Add CI/CD pipeline status badges

Implement status badge generation for Gitea Actions pipelines.
Provide badge URLs in Markdown and HTML formats showing the current
status (passing/failing/running) of a specified workflow. Support
branch-specific badges. Cache badge SVGs with appropriate TTL.
Add a "Get badge" UI in the repository Actions settings.

## Wide

### W1: Implement project boards with Kanban and table views

Add project management boards that span across repositories within
an organization. Support Kanban view (draggable cards between columns)
and table view (sortable/filterable list). Cards can be issues or
PRs from any repository in the organization. Support custom fields
(status, priority, size), swimlanes by assignee or label, and WIP
limits per column. Add API endpoints for all project board operations.

### W2: Add full-text code search with syntax-aware indexing

Implement a code search feature that indexes repository contents
with syntax awareness. Support exact matches, regex, and symbol
search. Use the existing bleve indexer infrastructure but add
language-specific tokenization (camelCase splitting, identifier
extraction). Show results with syntax-highlighted context. Support
searching across all repositories the user has access to. Add
search filters for language, file path, and repository.

### W3: Implement SSO with SAML 2.0 support

Add SAML 2.0 identity provider integration alongside the existing
OAuth2 and LDAP authentication sources. Implement SP-initiated and
IdP-initiated SSO flows. Support SAML attribute mapping to Gitea
user fields (email, username, groups → teams). Add JIT (just-in-time)
provisioning that creates Gitea accounts on first SAML login. Implement
single logout (SLO). Add admin UI for SAML configuration with
metadata import/export.
