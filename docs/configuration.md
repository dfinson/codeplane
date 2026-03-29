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

## Remote & Mobile Access

CodePlane is designed for remote supervision — run the agent on your workstation, control it from your phone.

### Dev Tunnels (default)

```bash
cpl up --remote                              # auto-generates password
cpl up --remote --password your-secret       # explicit password
cpl info                                     # print URL + QR code
```

Scan the QR code from `cpl info` to open CodePlane on your phone instantly.

### Cloudflare Tunnels

```bash
export CPL_CLOUDFLARE_TUNNEL_TOKEN=your-token
export CPL_CLOUDFLARE_HOSTNAME=codeplane.yourdomain.com
cpl up --remote --provider cloudflare
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CPL_DEVTUNNEL_PASSWORD` | Password for Dev Tunnels remote access | auto-generated with `--remote` |
| `CPL_CLOUDFLARE_TUNNEL_TOKEN` | Cloudflare Tunnel token | — |
| `CPL_CLOUDFLARE_HOSTNAME` | Cloudflare public hostname | — |
| `OTEL_EXPORTER_ENDPOINT` | OTLP endpoint for exporting metrics/traces | — (local only) |

## UI Settings

Additional preferences are available in **Settings** (`Ctrl+,`): registered repositories, default agent, and model preferences.
