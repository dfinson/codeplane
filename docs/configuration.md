---
hide:
  - navigation
---

# Configuration

CodePlane works out of the box with sensible defaults. This page covers the settings you're most likely to change.

## First-Time Setup

Run the interactive setup wizard:

```bash
cpl setup
```

This walks you through registering a repository, selecting a default agent, and setting preferences.

## Global Config File

Location: `~/.codeplane/config.yaml` (created on first run or via `cpl setup`).

### Agent Defaults

```yaml
agent:
  default_sdk: copilot              # agent CLI to use: copilot | claude
  default_model: ~                  # model name, or ~ for agent default
```

### Action Policy

The action policy system controls what an agent can do without your approval. Configure the default preset:

| Preset | Behavior |
|--------|---------|
| `autonomous` | Only non-contained actions (outside worktree, external calls) require approval |
| `supervised` | Contained and reversible actions proceed; all others require approval (default) |
| `locked` | Even safe actions create a git checkpoint first; non-contained/irreversible actions require approval |

The preset and all policy rules are managed in **Settings → Policy** in the UI, or via the REST API (`/settings/policy/`). These settings are stored in the database and can be changed at runtime (running jobs reload automatically).

See [Usage Guide > Action Policy](guide.md#action-policy) for full details on tiers, rules, and overrides.

### Server

```yaml
server:
  host: 0.0.0.0
  port: 8080
```

### Retention

```yaml
retention:
  max_completed_jobs: 100           # auto-cleanup oldest when exceeded
  max_worktree_age_hours: 72        # auto-delete old worktrees
```

## Per-Repository Overrides

Place a `.codeplane.yml` file in any repository root to override global settings for jobs in that repo:

```yaml
agent:
  default_sdk: claude
  default_model: claude-sonnet-4-5
```

## Remote Access

Run the agent on your workstation, control it from your phone or any browser. CodePlane supports two tunnel providers.

### Dev Tunnels (default)

**Prerequisite:** Install the [Dev Tunnels CLI](https://aka.ms/devtunnels/cli), or run `cpl setup` which handles it for you.

```bash
cpl up --remote                              # password auto-generated
cpl up --remote --password my-secret         # explicit password
cpl up --remote --tunnel-name my-tunnel      # reuse a named tunnel
```

A password is always required for remote access. By default one is auto-generated; set it explicitly via `--password` or the `CPL_PASSWORD` env var.

After startup, run `cpl info` to print the tunnel URL and a QR code you can scan from your phone.

### Cloudflare Tunnels

Use Cloudflare Tunnels when you want a stable public hostname (e.g., `codeplane.yourdomain.com`) instead of the auto-provisioned Dev Tunnels URL.

!!! danger "Cloudflare Access is required"
    Cloudflare Tunnels have no identity gate by default. CodePlane will **refuse to start** unless it detects a [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/) application on the hostname.

**Prerequisites:**

1. Install [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
2. [Create a named tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/) and route a public hostname to `http://localhost:8080`
3. [Create a Cloudflare Access application](https://developers.cloudflare.com/cloudflare-one/applications/configure-apps/self-hosted-app/) on that hostname with an identity policy. Email OTP is the simplest option; SSO and mTLS are also supported.

**Start CodePlane:**

```bash
export CPL_CLOUDFLARE_TUNNEL_TOKEN=eyJhIjo...      # from tunnel setup
export CPL_CLOUDFLARE_HOSTNAME=codeplane.yourdomain.com
cpl up --remote --provider cloudflare
```

At startup, CodePlane probes the hostname for a Cloudflare Access gate. If none is detected, the server exits with an error.

#### Cloudflare Access JWT Verification

When `cloudflared` is managed externally (e.g., a systemd service or Docker sidecar) and CodePlane is not started with `--remote --provider cloudflare`, you can still delegate authentication to Cloudflare Access by configuring JWT verification:

```bash
export CPL_CF_ACCESS_TEAM=yourteam            # <team> in <team>.cloudflareaccess.com
export CPL_CF_ACCESS_AUD=your-aud-tag-here    # Application Audience Tag from Zero Trust dashboard
```

At startup CodePlane fetches Cloudflare's JWKS signing keys (validating the Access gate exists) and then cryptographically verifies the `Cf-Access-Jwt-Assertion` header on every request — checking signature, audience, and expiry. If either variable is missing, the header is ignored and normal password auth applies.

You can find the AUD tag in the Cloudflare Zero Trust dashboard under **Access → Applications → your app → Overview → Application Audience (AUD) Tag**.

### Tunnel Lifecycle

Both providers are supported on Linux, macOS, native Windows, and WSL.

**The connector is tied to the server.** CodePlane starts the connector process (`devtunnel host` or `cloudflared`) as a child of the server and stops it again when the server stops — via `cpl down`, `cpl restart`, or Ctrl-C. After a normal shutdown no connector is left behind and the public origin reports no host connections.

**Reusing an existing connector.** If a `cloudflared` process is already serving the *configured tunnel* — a systemd/Docker-managed connector, or one orphaned by a previous run — CodePlane detects it at startup, reuses it instead of starting a second one, and leaves it running at shutdown. Detection matches the tunnel token (or the tunnel ID derived from it) on the running process's command line, so a `cloudflared` serving some *other* tunnel on the same machine is ignored and CodePlane starts its own connector. Externally managed connectors are never terminated by CodePlane.

**Named Dev Tunnels are reusable.** `--tunnel-name` reuses the same tunnel and port registration across restarts, so the URL stays stable. Re-running against a tunnel whose port is already registered is expected and handled.

**Connector supervision.** A watchdog restarts the connector if the connector process exits, or if the public relay stops forwarding while the local server is healthy. It deliberately does *not* restart the connector when the local server itself is unreachable (for example during a slow startup) — restarting a connector cannot fix a down origin.

!!! warning "`cpl down` is not graceful on Windows"
    Windows has no process signals: `cpl down` terminates the server and every process below it outright. The connector is stopped first, so the public origin closes promptly and no connector is orphaned — but the server's own shutdown handlers do not run, so worktree cleanup and lease release are skipped. Running sessions are paused over the API before the stop, which is what preserves session state. On Linux, macOS, and WSL, `cpl down` sends `SIGTERM` and the full graceful shutdown runs.

!!! note "Hard kills can orphan the connector"
    If the server is killed abruptly (`kill -9`, Task Manager, a crash), it gets no chance to stop the connector and the process may survive. The next `cpl up` detects the surviving connector and reuses it. To remove it manually, stop the `devtunnel host` / `cloudflared` process for your origin.
    If the server is killed abruptly (`kill -9`, Task Manager, a crash), it gets no chance to stop the connector and the process may survive. The next `cpl up` detects the surviving connector and reuses it. To remove it manually, stop the `devtunnel host` / `cloudflared` process for your origin.

### All Remote Options

| CLI Flag | Env Var | Description |
|----------|---------|-------------|
| `--remote` | — | Enable remote access (required) |
| `--provider` | — | `devtunnel` (default) or `cloudflare` |
| `--password SECRET` | `CPL_PASSWORD` | Auth password (auto-generated if omitted) |
| `--tunnel-name NAME` | `CPL_DEVTUNNEL_NAME` | Reuse a named Dev Tunnel across restarts |
| — | `CPL_CLOUDFLARE_TUNNEL_TOKEN` | Cloudflare Tunnel token |
| — | `CPL_CLOUDFLARE_HOSTNAME` | Cloudflare public hostname |
| — | `CPL_CF_ACCESS_TEAM` | CF Access team name (for JWT verification) |
| — | `CPL_CF_ACCESS_AUD` | CF Access Application Audience tag |

## Other Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OTEL_EXPORTER_ENDPOINT` | Legacy value. Does not enable network export or bypass the explicit export binding below. | Local only |

## Optional story tokenization

Story generation loads `tiktoken` only when needed. A missing package uses the
logged character estimate. With the package installed, uncached story token
counting runs in one owned, short-lived process per Story service, with a
five-second deadline. Cached encodings retain exact tokenization. Loader errors
and deadlines fail story preparation explicitly, without substituting estimates
or caching a generated story. Core HTTP processing stays responsive, and
shutdown kills and reaps an outstanding tokenizer process before closing story
storage. No global tokenizer cache or network settings are changed.

## Optional metadata-only telemetry export

Local history, approvals, governance and telemetry work without exporter extras.
Network export is disabled by default. Install only the required extra:
`codeplane[otlp-export-http]` for HTTP/protobuf traces, or
`codeplane[otlp-export-grpc]` for separately configured gRPC traces/metrics.
These extras qualify OpenTelemetry SDK/exporter 1.45.2. Inbound binary OTLP uses
the independent `codeplane[otlp-ingest]` extra.

Configure the binding in the existing CodePlane configuration, then restart:

```yaml
export:
  enabled: true
  protocol: http/protobuf
  endpoint: https://collector.example.com/v1/traces
  credential_id: your-destination-scoped-credential-id
  approved_models: []
  metrics_endpoint: ""
  metrics_credential_id: null
```

For gRPC traces, set `protocol: grpc` and an explicit `http://host:port` or
`https://host:port` endpoint without a signal path. Metrics require a separately
declared gRPC `metrics_endpoint` and, if needed, `metrics_credential_id`.
A trace-only destination is never assumed to accept metrics.

Create a transport credential through the existing authenticated
`POST /api/settings/credentials` API with `provider: "otlp"`, the exact collector
`baseUrl`, and `exportProtocol: "http/protobuf"` or `"grpc"`. Its write-only `pat`
field contains a JSON object of destination authentication headers, for example
`{"Authorization":"Bearer <collector-token>"}`. The existing credential store
encrypts this value. Never put headers or secrets in the YAML endpoint.
Credentials are bound to their endpoint and protocol, cannot attach to tracker
links, and cannot be deleted while referenced by the loaded export binding.
Omit `credential_id` only when the collector does not require authentication.

Only allowlisted metadata from committed canonical events enters export.
Source identifiers are locally keyed, model IDs require explicit approval, and
missing role or unverified monetary provenance is withheld rather than guessed.
Prompts, commands, results, paths, arbitrary attributes and source records remain
local. Legacy SDK measurements without the required provenance stay local.
The existing local OTel provider/readers are not replaced.

The bounded SDK queue contains at most 2,048 sanitized spans, in batches of 128
with a five-second cadence. Requests have a five-second total timeout.
Transient failures use 1/2/4/8-second backoff within a 30-second delivery window.
Authentication/configuration errors disable only export. Overflow, withholding
and exhausted retries are visible through authenticated
`GET /api/integrations/export-health`. Shutdown permits two seconds to flush.
Export retries never retry agent work, and there is no durable export spool.

## MCP Server Discovery

When a job starts, CodePlane discovers MCP servers to make available to the agent. Servers are merged from two sources (repo-level wins on name conflicts):

1. **Repo-level:** `.vscode/mcp.json` in the repository (VS Code / Copilot convention)
2. **Global:** `tools.mcp` in `~/.codeplane/config.yaml`

### Global config example

```yaml
tools:
  mcp:
    github:
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github"]
    postgres:
      command: uvx
      args: ["mcp-postgres"]
      env:
        DATABASE_URL: "${DATABASE_URL}"
```

### Repo-level example (`.vscode/mcp.json`)

```json
{
  "servers": {
    "github": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"]
    }
  }
}
```

### Disabling servers per-repo

Add a `.codeplane.yml` file to the repository root:

```yaml
tools:
  mcp:
    disabled:
      - postgres
```

This prevents the `postgres` MCP server from starting for jobs in this repo, even if it's defined globally.

## UI Settings

Additional preferences are available in **Settings** (`Ctrl+,`): registered repositories, default agent, model preferences, and push notification settings.

### Push Notifications

Toggle push notifications in **Settings → Notifications**. Notifications are delivered via the Web Push protocol. When enabled for the first time, your browser will ask for permission.

VAPID keys are generated automatically on first startup and stored in `~/.codeplane/vapid.json`. No manual configuration is required.

Notifications fire on three events:

| Event | Notification |
|-------|--------------|
| `approval_requested` | "Approval needed" — a job is waiting for your decision |
| `job_completed` | "Job completed" — the agent finished |
| `job_failed` | "Job failed" — the run encountered an error |

### Job Sharing

Share tokens are generated on demand from the job detail toolbar (**Share** button). Tokens expire after 24 hours and are stored in-memory — a server restart invalidates all tokens.

Shared links bypass CodePlane's password for read-only access to job status and live events. The viewer must still be able to reach the server (e.g. via the same tunnel or LAN). No configuration is required.

### Port Preview Proxy

A reverse proxy at `/api/preview/{port}/` forwards to `127.0.0.1:{port}`. This is useful when combined with `--remote` to access development servers from another device. Only ports 1024–65535 are allowed.
