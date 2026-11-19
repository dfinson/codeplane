---
hide:
  - navigation
---

# Security

By default, CodePlane binds to localhost only. Remote access always requires application authentication: a password session or a validated Cloudflare Access JWT. This page documents the threat model and configuration options.

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
| **Cloudflare Tunnels** | `cpl up --remote --provider cloudflare` | Cloudflare Access policy | Identity gate (OTP/SSO) + password | ✅ Low |
| **Cloudflare (external tunnel)** | `cpl up --remote --provider cloudflare --tunnel-ownership external` | Cloudflare Access policy | Application password session or verified JWT | ✅ Low |
| **Cloudflare Access-only** | `cpl up --remote --provider cloudflare --access-only` | Cloudflare Access policy | Verified JWT required by CodePlane | ✅ Low |
| **All interfaces** | `cpl up --host 0.0.0.0` | Any device on your network | Password required | ⚠️ Medium |

### Safe Defaults

Out of the box, CodePlane is configured conservatively:

- ✅ **Localhost-only** — server binds to `127.0.0.1`, inaccessible from the network
- ✅ **Password auto-generated** — remote mode retains the selected password, or generates one by default. Cloudflare startup never removes it.
- ✅ **Dangerous combos blocked** — `--host 0.0.0.0 --no-password` is rejected at startup; `--remote --no-password` is also rejected
- ✅ **Dev Tunnels identity gate** — `--remote` with the default provider creates a Dev Tunnel that requires Microsoft account login (tunnel-owner only); the URL is unguessable and access is gated by both MS auth and the CodePlane password
- ✅ **Cloudflare Tunnels** — the application gate is installed at construction, before deferred tunnel checks. A failed external check can shut down the tunnel but never opens the application gate. See [Configuration > Cloudflare Tunnels](configuration.md#cloudflare-tunnels).
- ✅ **CF Access JWT verification** — with `CPL_CF_ACCESS_TEAM` and `CPL_CF_ACCESS_AUD`, each protected HTTP request, SSE connection and WebSocket upgrade validates the JWT's RS256 signature, issuer, audience and expiry. Keys are fetched lazily and refreshed hourly. Hostnames, forwarded headers and successful health probes are not credentials.
- ✅ **Explicit Access-only** — `--access-only` requires Cloudflare remote mode and both Access settings; it conflicts with an explicit password, including `CPL_PASSWORD`. Session cookies do not authenticate this mode.
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
| `autonomous` preset | Agent runs with minimal approval prompts | Repos you fully trust with no CI/CD write access |

!!! warning
    Never use `--host 0.0.0.0` without a password on a shared or public network.
    Anyone on the network could access your terminal and files.

## Security Features

### Authentication & Sessions

- **Password hashing**: PBKDF2-HMAC-SHA256 with 600,000 iterations and a 16-byte random salt
- **Session tokens**: 512-bit cryptographically random tokens (`secrets.token_hex(32)`)
- **Token expiry**: 24-hour TTL, lazily purged during validation
- **Cookie security**: `httpOnly` (no JavaScript access), `SameSite=Strict` (no cross-site sending), `Secure` flag set dynamically when accessed over HTTPS
- **Localhost trust**: retained only without remote mode. Tunnel connectors use loopback too, so remote mode requires credentials even from `127.0.0.1` / `::1`.
- **Internal HTTP calls**: MCP self-calls use a process-only credential for the resolved loopback listener. It is not exported to agents, logged, saved in launch profiles, or forwarded to previews.
- **Rate limiting**: 5 failed login attempts per minute per IP (sliding window)

### Action Policy

CodePlane's standalone AGT policy combines strict assessment with local constraints. Model claims cannot invent Git recovery, erase known content loss or bypass hard gates:

| Preset | Agent can | Approval required for |
|--------|-----------|----------------------|
| `autonomous` | Known reads and evidenced Git-recoverable workspace mutations | Hard gates, external mutations and incomplete evidence |
| `supervised` | Known reads; evidenced Git mutations with a savepoint | Hard gates, external mutations and incomplete evidence |
| `locked` | Trusted native reads; known shell reads with a savepoint | Mutations and incomplete evidence |

**Recommended actions:**

| Action | Symbol | Behavior |
|------|--------|----------|
| allow | ○ | Action proceeds immediately |
| warn | ◐ | Git savepoint created, then proceeds |
| escalate | ● | Sent for review before continuing |
| deny | ● | Blocks until operator approves |

**Hard-gated commands** always require approval regardless of preset:
`git merge`, `git pull`, `git rebase`, `git cherry-pick`, and `git reset --hard`

**Protected paths**: the governance profile escalates writes to sensitive paths (e.g., `infra/`, `.github/workflows/`) regardless of preset.

**USD thresholds** apply only to verified USD. Native cost units, historical estimates and missing currency are not silently converted to dollars.

**Settings → Policy** selects presets, approval batching and verified-USD warning/escalation overrides. Zero is an active threshold and null disables a line. Spend guards are soft, not security hard gates. Routine approval grants last 24 hours within the same Job, policy configuration and reason. Hard gates and incomplete evidence always require individual approval and cannot be waived.

### Authorization Model

CodePlane is a **single-user, local-first application**. All authenticated requests have full access to all jobs and resources — there is no per-job ownership or role-based access control.

**Implications:**

- Any authenticated session can view, cancel, or interact with any job
- The authentication boundary (password + session cookie) is the sole access control layer
- If you expose CodePlane on a network (`--host 0.0.0.0`), anyone with the password has full access

**If multi-user support is needed in the future**, the API layer would need per-job ownership checks and a `get_job_for_user(job_id, user_id)` pattern in the service layer.

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

- Authentication checked before streaming using the same fixed posture as HTTP and WebSocket connections
- Connection limit: max 5 concurrent SSE connections per server
- Reconnection replay bounded: max 500 events or 5 minutes
- Job-scoped streams: clients only receive events for their requested job

### Share Token Security

Share tokens provide **read-only** access to a single job:

- 256-bit cryptographically random tokens (`secrets.token_urlsafe(32)`)
- 24-hour TTL, stored in-memory only (invalidated on server restart)
- Share endpoints bypass CodePlane's password auth but expose only job metadata and SSE events
- Mutation operations (approvals, cancellation, messages, resolution) are not accessible via share tokens
- The port preview proxy is not accessible via share tokens
- **Share tokens do not bypass tunnel-level identity gates** — Dev Tunnels still requires Microsoft login; Cloudflare still requires the Access policy
- No audit trail is kept for token creation or access — treat share URLs as sensitive

### Preview Boundary

Preview forwarding strips `Cookie`, `Authorization`, `Cf-Access-Jwt-Assertion`,
and the internal self-call credential. Shared HTTP-client cookies cannot be
replayed between requests or ports, and upstream `Set-Cookie` is not returned
to the CodePlane origin. Redirects are returned without being followed.
Decoded response bytes, including compressed responses, are counted during
streaming against the 50 MiB cap; overflow, errors and cancellation close the
upstream response.

### Authentication Across Restart

`run.json` records `authMode` (`local`, `password`, or `access_only`) and secret
source references, never password, session, JWT or internal credential values.
Access-only replay retains `--access-only`; legacy remote records without a
password source are refused rather than silently interpreted as Access-only.
Generated or command-line-only passwords remain unreplayable.

The detached restart helper can authenticate using a resolvable `CPL_PASSWORD`
and keeps its resulting session only in memory. For Access-only, the initiating
process privately captures the operator's existing cached JWT with
`cloudflared access token --app <recorded-origin>`. CodePlane never initiates
login. Missing, empty, expired or invalid credentials fail before mutation.

The initiating process validates the recorded process/listener and HTTPS origin,
then passes the JWT through an inherited anonymous pipe using a versioned,
length-prefixed frame capped at 64 KiB. No credential enters argv, environment,
request/profile files, status receipts or logs. Credential retrieval precedes
the adoption timer. The helper validates the bound request/server identity and
authenticates a protected read on the exact old listener before acknowledging
adoption. A distinct parent commit frame is required before pause/stop.
EOF, malformed input, deadline expiry or parent death before commit aborts.

After commit the helper can survive parent exit. It authenticates every allowed
job-list/pause request with the JWT, without redirects or environment proxies.
It closes the channel and drops token references before stopping the old process
or starting the replacement. Replacement retains Access-only. The transfer is
single-use; the JWT remains an ordinary application credential until its existing
expiry, not a server-enforced restart-scoped token.

### Push Notification Security

- VAPID EC P-256 key pair generated at first use, persisted in `~/.codeplane/vapid.json` (chmod 600)
- Browsers verify VAPID signatures on push messages — forged messages are rejected
- Subscriptions stored in-memory; stale subscriptions (HTTP 410/404) auto-pruned
- Notification payloads contain only: title, body text, a tag, and a relative URL — no secrets

### Port Preview Proxy Security

- Only proxies to `127.0.0.1` — prevents SSRF against internal networks
- Only ports 1024–65535 — system/privileged ports blocked
- Requires normal authentication (not accessible via share tokens)
- Returns 502 when target port is not listening

## Best Practices

1. **Keep password enabled** for any non-localhost access
2. **Use `supervised` or `locked` preset** for repositories you don't fully trust
3. **Rely on protected-path escalation** — the governance profile gates writes to sensitive paths (e.g., `infra/`, `.github/workflows/`)
4. **Review the verified-USD thresholds** in Settings → Policy and select the appropriate preset
5. **Don't leave CodePlane running unattended** with active agents
6. **Review approval requests carefully** — agents can be persuasive
7. **Use defaults on shared networks** — avoid `--host 0.0.0.0` on Wi-Fi you don't control
8. **Close when done** — CodePlane is a development tool, not a persistent service
9. **Treat share links as sensitive** — they bypass the CodePlane password for read-only access; tokens expire after 24 hours but can't be revoked individually. The viewer must still pass the tunnel identity gate (if any)

## Reporting Vulnerabilities

Please **do not** open a public GitHub issue for security vulnerabilities.

Report vulnerabilities privately via our
[GitHub Security Advisories](https://github.com/codeplane-dev/codeplane/security/advisories)
page — click **Report a vulnerability** and provide a detailed description
including steps to reproduce.
