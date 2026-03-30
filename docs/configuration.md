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
  permission_mode: auto             # auto | read_only | approval_required
```

| Permission Mode | Behavior |
|-----------------|----------|
| `auto` | All agent actions within the worktree are auto-approved — no prompts (default) |
| `read_only` | Agent can read files and run safe commands (grep, ls, find); all writes and mutations are blocked |
| `approval_required` | Reads always allowed; file writes, shell commands (except grep/find), and network access pause for your approval |

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
  permission_mode: approval_required
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

A password is always required for remote access. By default one is auto-generated; set it explicitly via `--password` or the `CPL_DEVTUNNEL_PASSWORD` env var.

After startup, run `cpl info` to print the tunnel URL and a QR code you can scan from your phone.

### Cloudflare Tunnels

**Prerequisites:** Install [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/), create a named Cloudflare Tunnel, and route a public hostname to `localhost`.

```bash
export CPL_CLOUDFLARE_TUNNEL_TOKEN=your-token
export CPL_CLOUDFLARE_HOSTNAME=codeplane.yourdomain.com
cpl up --remote --provider cloudflare
```

Both env vars are required. The hostname should point at the tunnel you created in the Cloudflare dashboard.

### All Remote Options

| CLI Flag | Env Var | Description |
|----------|---------|-------------|
| `--remote` | — | Enable remote access (required) |
| `--provider` | — | `devtunnel` (default) or `cloudflare` |
| `--password SECRET` | `CPL_DEVTUNNEL_PASSWORD` | Auth password (auto-generated if omitted) |
| `--tunnel-name NAME` | `CPL_DEVTUNNEL_NAME` | Reuse a named Dev Tunnel across restarts |
| — | `CPL_CLOUDFLARE_TUNNEL_TOKEN` | Cloudflare Tunnel token |
| — | `CPL_CLOUDFLARE_HOSTNAME` | Cloudflare public hostname |

## Other Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OTEL_EXPORTER_ENDPOINT` | OTLP endpoint for exporting metrics/traces | — (local only) |

## UI Settings

Additional preferences are available in **Settings** (`Ctrl+,`): registered repositories, default agent, and model preferences.
