---
hide:
  - navigation
---

# Security

## Overview

CodePlane runs coding agents that execute real shell commands on your machine.
Security is critical — an exposed or misconfigured instance gives an attacker
(or a misbehaving agent) the ability to read, modify, and delete files with
your user permissions.

This page describes the threat model, the security features built into
CodePlane, and recommended practices for safe operation.

## Threat Model

The risk depends on how you run CodePlane. There are four access modes:

| Mode | Command | Who can connect | Auth layers | Risk |
|------|---------|-----------------|-------------|------|
| **Localhost** (default) | `cpl up` | Local processes only | Auto-password (optional) | ✅ Low |
| **Dev Tunnels** | `cpl up --remote` | Tunnel owner only | Microsoft login + password | ✅ Low |
| **Cloudflare + Access** | `cpl up --remote --provider cloudflare` | Cloudflare Access policy | Identity gate (OTP/SSO) + password | ✅ Low |
| **Cloudflare (no Access)** | `cpl up --remote --provider cloudflare` | Anyone who knows the hostname | Password only | ⚠️ Medium |
| **All interfaces** | `cpl up --host 0.0.0.0` | Any device on your network | Password required | ⚠️ Medium |

### Safe Defaults

Out of the box, CodePlane is configured conservatively:

- ✅ **Localhost-only** — server binds to `127.0.0.1`, inaccessible from the network
- ✅ **Password auto-generated** — remote access always requires a password, regardless of provider
- ✅ **Dangerous combos blocked** — `--host 0.0.0.0 --no-password` is rejected at startup; `--remote --no-password` is also rejected
- ✅ **Dev Tunnels identity gate** — `--remote` with the default provider creates a Dev Tunnel that requires Microsoft account login (tunnel-owner only); the URL is unguessable and access is gated by both MS auth and the CodePlane password
- ⚠️ **Cloudflare Tunnels** — `--remote --provider cloudflare` has **no built-in identity gate** at the relay level. A startup warning is emitted recommending Cloudflare Access. See [Configuration > Cloudflare Tunnels](configuration.md#cloudflare-tunnels) for setup details
- ✅ **Worktree isolation** — agents operate in a dedicated Git worktree, never on your main branch
- ✅ **Approval system** — destructive Git commands always require operator approval
- ✅ **Secure cookies** — httpOnly, SameSite=Strict, Secure (when over HTTPS)
- ✅ **Rate-limited login** — 5 attempts per minute per IP
- ✅ **Security headers** — X-Content-Type-Options, X-Frame-Options, CSP, Referrer-Policy on all responses
- ✅ **WebSocket origin validation** — cross-origin terminal connections rejected

### Dangerous Configurations

| Flag | What it does | When it's acceptable |
|------|-------------|---------------------|
| `--host 0.0.0.0` | Binds to all network interfaces | Trusted home network with password enabled |
| `--no-password` | Disables password authentication | Localhost-only on a single-user machine |
| `full_auto` permission mode | Agent runs without approval prompts | Repos you fully trust with no CI/CD write access |

!!! warning
    Never use `--host 0.0.0.0` without a password on a shared or public network.
    Anyone on the network could access your terminal and files.

## Security Features

### Authentication & Sessions

- **Password hashing**: PBKDF2-HMAC-SHA256 with 600,000 iterations and a 16-byte random salt
- **Session tokens**: 512-bit cryptographically random tokens (`secrets.token_hex(32)`)
- **Token expiry**: 24-hour TTL, lazily purged during validation
- **Cookie security**: `httpOnly` (no JavaScript access), `SameSite=Strict` (no cross-site sending), `Secure` flag set dynamically when accessed over HTTPS
- **Localhost bypass**: requests from `127.0.0.1` / `::1` are trusted without a cookie (same-machine access)
- **Rate limiting**: 5 failed login attempts per minute per IP (sliding window)

### Permission Modes

CodePlane supports three permission modes per job, controlling what the agent can do without approval:

| Mode | Agent can | Approval for |
|------|-----------|-------------|
| `full_auto` | Read + write freely within worktree | Hard-gated commands only |
| `review_and_approve` | Read freely | All writes and mutations |
| `observe_only` | Read-only tools (grep, ls, cat) | Everything else |

**Hard-gated commands** always require approval regardless of mode:
`git merge`, `git pull`, `git rebase`, `git cherry-pick`, `git reset --hard`

**Protected paths**: per-repo `.codeplane.yml` can define paths (e.g., `infra/`, `.github/workflows/`) where writes always trigger an approval request, even in `full_auto`.

### Worktree Isolation

Every job runs in a dedicated Git worktree under `.codeplane-worktrees/`. The agent never operates directly on your main branch or working directory.

- Concurrent jobs are fully isolated (separate worktrees and branches)
- Path containment enforced: all file operations verified against the worktree boundary
- Consistent use of `Path.resolve()` + `.is_relative_to()` prevents path traversal via symlinks or `../`

### Terminal Security

The built-in terminal uses **WebSocket** for bidirectional I/O:

- **Authentication**: `check_websocket_auth()` validates session cookie before accepting the connection
- **Origin validation**: cross-origin WebSocket connections are rejected (close code 1008) unless the origin is an allowed CORS origin or localhost
- **Rate limiting**: WebSocket auth failures are rate-limited at 5/min per IP (mirrors login rate limiter)
- **Resize bounds**: terminal resize messages validate columns (1–500) and rows (1–200); invalid values are silently dropped
- **PTY isolation**: each terminal session is an independent PTY process
- **Prompt injection prevention**: shell prompt labels are sanitized before injection into `PS1` assignments

### Upload Validation

- **Voice uploads**: MIME type whitelist (`audio/webm`, `audio/ogg`, `audio/wav`, `audio/mpeg`, `audio/mp4`) enforced by both Content-Type header and magic-byte verification
- **Size limits**: voice uploads configurable (default limit enforced with streaming early-abort), workspace file reads capped at 5 MB, artifacts at 100 MB
- **Concurrency**: max 2 simultaneous transcriptions (returns 429 when full)

### Path Traversal Prevention

All file-serving endpoints use the same secure pattern:

```python
target = (boundary / user_path).resolve()
if not target.is_relative_to(boundary):
    raise HTTPException(400, "Invalid path")
```

This protects: workspace file listing, workspace file reads, artifact downloads, and the SPA fallback handler.

### HTTP Security Headers

Every HTTP response includes:

| Header | Value | Purpose |
|--------|-------|---------|
| `X-Content-Type-Options` | `nosniff` | Prevent MIME sniffing |
| `X-Frame-Options` | `DENY` | Prevent clickjacking |
| `Content-Security-Policy` | `default-src 'self'; ...` | Prevent XSS |
| `Referrer-Policy` | `no-referrer` | No referrer leaks |
| `Cache-Control` | `no-store` | Prevent caching sensitive responses |

### CORS Policy

- **Origins**: restricted to `localhost:5173` (dev) and the tunnel origin (when active)
- **Methods**: `GET`, `POST`, `PUT`, `DELETE`, `PATCH`
- **Headers**: `Content-Type`, `Authorization`, `Last-Event-ID`
- **Credentials**: enabled (cookies sent cross-origin to configured origins only)

### SSE Event Streaming

- Authentication checked inline (not via middleware, which would buffer and break SSE)
- Connection limit: max 5 concurrent SSE connections per server
- Reconnection replay bounded: max 500 events or 5 minutes
- Job-scoped streams: clients only receive events for their requested job

## Best Practices

1. **Keep password enabled** for any non-localhost access
2. **Use `review_and_approve`** for repositories you don't fully trust
3. **Define protected paths** in `.codeplane.yml` for infrastructure and CI/CD files
4. **Don't leave CodePlane running unattended** with active agents
5. **Review approval requests carefully** — agents can be persuasive
6. **Use defaults on shared networks** — avoid `--host 0.0.0.0` on Wi-Fi you don't control
7. **Close when done** — CodePlane is a development tool, not a persistent service

## Reporting Vulnerabilities

Please **do not** open a public GitHub issue for security vulnerabilities.

Report vulnerabilities privately via our
[GitHub Security Advisories](https://github.com/codeplane-dev/codeplane/security/advisories)
page — click **Report a vulnerability** and provide a detailed description
including steps to reproduce.
