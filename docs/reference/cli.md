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
| `--password SECRET` | Tunnel authentication password | from `CPL_DEVTUNNEL_PASSWORD` env var |
| `--provider PROVIDER` | Tunnel provider (`devtunnel` or `cloudflare`) | `devtunnel` |
| `--tunnel-name NAME` | Dev Tunnel name (reused across restarts) | random |
| `--skip-preflight` | Skip preflight checks | disabled |

**Examples:**

```bash
# Basic local server
cpl up

# Remote access with password
cpl up --remote --password my-secret

# Development mode on custom port
cpl up --dev --port 9090
```

On startup, the server:

1. Runs preflight checks (dependencies, SDK auth)
2. Runs database migrations (Alembic)
3. Builds the frontend (unless `--dev`)
4. Starts the FastAPI server
5. Opens tunnel (Dev Tunnels or Cloudflare, if `--remote`)
6. Launches the Rich console dashboard (TTY only)
7. Recovers previously-running jobs (marks as failed with restart reason)

### `cpl down`

Gracefully stop the CodePlane server.

```bash
cpl down [options]
```

| Option | Description | Default |
|--------|-------------|---------|
| `--host HOST` | Server host | from config or `127.0.0.1` |
| `--port PORT` | Server port | from config or `8080` |
| `--force` | Skip session pausing; stop immediately | disabled |

On shutdown, active sessions are paused for recovery on next start.

### `cpl restart`

Stop and restart the server, preserving sessions for recovery.

```bash
cpl restart [options]
```

Accepts all `cpl up` options plus:

| Option | Description | Default |
|--------|-------------|---------|
| `--force` | Skip session pausing on shutdown | disabled |

### `cpl version`

Display the current CodePlane version.

```bash
cpl version
```

### `cpl setup`

Run the interactive first-time setup wizard.

```bash
cpl setup
```

Walks you through:

- Registering your first repository
- Selecting a default SDK
- Configuring preferences

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

Useful for retrieving the tunnel URL and QR code after the server is already running.

### `cpl doctor`

Diagnose environment issues.

```bash
cpl doctor
```

Checks for:

- Python version compatibility
- Node.js version compatibility
- Required dependencies
- SDK availability
- Git configuration

## Using Make Targets

The `Makefile` provides convenience targets:

| Target | Command |
|--------|---------|
| `make install` | `uv sync` + `cd frontend && npm ci` |
| `make run` | Build frontend + `cpl up --remote` |
| `make lint` | `ruff check` + `eslint` |
| `make format` | `ruff format` |
| `make typecheck` | `mypy` + `tsc` |
| `make test` | `pytest` (70% coverage) + `vitest` |
| `make ci` | All of the above |
| `make clean` | Remove build artifacts and caches |
