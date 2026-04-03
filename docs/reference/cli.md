# CLI Reference

CodePlane provides the `cpl` command-line interface for managing the server.

## Usage

```bash
cpl <command> [options]
```

## Commands

### `cpl up`

Start the CodePlane server.

```bash
cpl up [options]
```

| Option | Description | Default |
|--------|-------------|---------|
| `--remote` | Enable Dev Tunnels for remote access | disabled |
| `--dev` | Skip frontend build (backend-only development) | disabled |
| `--port PORT` | Server port | `8080` |
| `--password SECRET` | Remote access password | from `CPL_PASSWORD` env var |
| `--provider PROVIDER` | Tunnel provider (`devtunnel` or `cloudflare`) | `devtunnel` |
| `--tunnel-name NAME` | Dev Tunnel name (reused across restarts) | random |
| `--skip-preflight` | Skip preflight checks | disabled |

**Examples:**

```bash
cpl up                                  # local server on :8080
cpl up --remote --password my-secret    # with tunnel access
cpl up --port 9090                      # custom port
```

On startup, the server runs preflight checks, applies database migrations, starts the API server, opens a tunnel (if `--remote`), and recovers any previously-running jobs.

### `cpl down`

Gracefully stop the server.

```bash
cpl down [options]
```

| Option | Description | Default |
|--------|-------------|---------|
| `--host HOST` | Server host | from config or `127.0.0.1` |
| `--port PORT` | Server port | from config or `8080` |
| `--force` | Skip session pausing; stop immediately | disabled |

Active sessions are paused for recovery on next start.

### `cpl restart`

Stop and restart the server, preserving sessions.

```bash
cpl restart [options]
```

Accepts all `cpl up` options plus `--force` to skip session pausing on shutdown.

### `cpl setup`

Interactive first-time setup: register a repository, select a default agent, and set preferences.

```bash
cpl setup
```

### `cpl doctor`

Check that dependencies, agent CLIs, and Git are correctly configured.

```bash
cpl doctor
```

### `cpl info`

Print server connection details and QR code.

```bash
cpl info [options]
```

| Option | Description | Default |
|--------|-------------|---------|
| `--host HOST` | Server host | `127.0.0.1` |
| `--port PORT` | Server port | `8080` |
| `--tunnel-url URL` | Tunnel URL | auto-detected |
| `--password PWD` | Access password | — |

### `cpl version`

Display the installed CodePlane version.

```bash
cpl version
```
