# Configuration

CodePlane works out of the box with sensible defaults. This page covers the settings you're most likely to change.

## First-Time Setup

Run the interactive setup wizard:

```bash
cpl setup
```

This walks you through registering a repository, selecting a default SDK, and setting preferences.

## Global Config File

Location: `~/.codeplane/config.yaml` (created on first run or via `cpl setup`).

### Agent Defaults

```yaml
agent:
  default_sdk: copilot              # copilot | claude
  default_model: ~                  # model name, or ~ for SDK default
  permission_mode: auto             # auto | read_only | approval_required
```

| Permission Mode | Behavior |
|-----------------|----------|
| `auto` | SDK handles permissions automatically (default) |
| `read_only` | Agent can only read; all writes require approval |
| `approval_required` | File writes, shell commands, and network access require approval |

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

### Dev Tunnels (default)

```bash
cpl up --remote
cpl up --remote --password your-secret
```

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

Additional preferences are available in **Settings** (`Ctrl+,`): registered repositories, default SDK, and model preferences.
