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
  permission_mode: full_auto        # full_auto | observe_only | review_and_approve
```

| Permission Mode | Behavior |
|-----------------|---------|
| `full_auto` | All agent actions within the worktree are auto-approved — no prompts (default) |
| `observe_only` | Agent can read files and run safe commands (grep, ls, find); all writes and mutations are blocked |
| `review_and_approve` | Reads always allowed; file writes, shell commands (except grep/find), and network access pause for your approval |

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

A password is always required for remote access. By default one is auto-generated; set it explicitly via `--password` or the `CPL_PASSWORD` env var.

After startup, run `cpl info` to print the tunnel URL and a QR code you can scan from your phone.

### Cloudflare Tunnels

Use Cloudflare Tunnels when you want a stable public hostname (e.g., `codeplane.yourdomain.com`) instead of the auto-provisioned Dev Tunnels URL.

!!! warning "Security: No built-in identity gate"
    Unlike Dev Tunnels (which requires Microsoft account login at the relay), Cloudflare Tunnels have **no identity gate by default**. Anyone who discovers your hostname can reach the CodePlane login page. We strongly recommend adding Cloudflare Access — see step 4 below.

#### Step 1: Install cloudflared

```bash
# macOS
brew install cloudflared

# Linux (Debian/Ubuntu)
curl -L https://pkg.cloudflare.com/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb

# Or see: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
```

#### Step 2: Create a Named Tunnel

In the [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com/) → **Networks** → **Tunnels**:

1. Click **Create a tunnel** → choose **Cloudflared** connector
2. Name it (e.g., `codeplane`)
3. Copy the tunnel token — this goes in `CPL_CLOUDFLARE_TUNNEL_TOKEN`
4. Under **Public Hostnames**, add a route:
   - **Subdomain**: your chosen subdomain (e.g., `codeplane`)
   - **Domain**: select your Cloudflare-managed domain
   - **Service**: `http://localhost:8080`

This creates the DNS record and ingress route automatically.

#### Step 3: Configure and Start

```bash
# Add to your .env file or shell profile:
export CPL_CLOUDFLARE_TUNNEL_TOKEN=eyJhIjo...   # tunnel token from step 2
export CPL_CLOUDFLARE_HOSTNAME=codeplane.yourdomain.com

# Start CodePlane
cpl up --remote --provider cloudflare
```

#### Step 4: Add Cloudflare Access (Recommended)

To add an identity gate (equivalent to Dev Tunnels' Microsoft login):

1. In the Zero Trust dashboard → **Access** → **Applications** → **Add an application**
2. Choose **Self-hosted**, set the domain to your `CPL_CLOUDFLARE_HOSTNAME`
3. Create a policy with **Email OTP** as the identity provider — allow only your email address
4. Save

Now visitors must verify their email before reaching the CodePlane login page. This gives you two-factor security: Cloudflare identity **+** CodePlane password.

### All Remote Options

| CLI Flag | Env Var | Description |
|----------|---------|-------------|
| `--remote` | — | Enable remote access (required) |
| `--provider` | — | `devtunnel` (default) or `cloudflare` |
| `--password SECRET` | `CPL_PASSWORD` | Auth password (auto-generated if omitted) |
| `--tunnel-name NAME` | `CPL_DEVTUNNEL_NAME` | Reuse a named Dev Tunnel across restarts |
| — | `CPL_CLOUDFLARE_TUNNEL_TOKEN` | Cloudflare Tunnel token |
| — | `CPL_CLOUDFLARE_HOSTNAME` | Cloudflare public hostname |

## Other Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OTEL_EXPORTER_ENDPOINT` | OTLP endpoint for exporting metrics/traces | — (local only) |

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

Additional preferences are available in **Settings** (`Ctrl+,`): registered repositories, default agent, and model preferences.
