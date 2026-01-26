# CodePlane – CLI, Daemon Lifecycle, Distribution, and Operability

## 8.1 CLI Command Map

CodePlane uses a single binary CLI, `cpl`, as the operator interface. It is explicitly **not** agent-facing.

### Core Commands

| Command            | Description                                                                 |
|--------------------|-----------------------------------------------------------------------------|
| `cpl init`         | Initialize CodePlane in a repository, build index, generate `.cplignore`.   |
| `cpl start`        | Start the CodePlane daemon (global or repo-scoped).                         |
| `cpl stop`         | Gracefully stop the daemon.                                                 |
| `cpl status`       | Show daemon status, port, repo state, index revision.                       |
| `cpl doctor`       | Run diagnostics: daemon health, index state, config checks.                 |
| `cpl logs`         | View or tail daemon logs.                                                   |
| `cpl inspect`      | Show index metadata (commit hash, indexed file count, overlay presence).    |
| `cpl fetch-index`  | Download latest shared index artifact from remote.                          |
| `cpl rebuild`      | Wipe and rebuild the local overlay index.                                   |
| `cpl config`       | Set or query layered config (global, repo, CLI override).                   |
| `cpl upgrade`      | Replace current binary with the latest version (checksum-verified).         |

All commands are idempotent. Human-readable output is derived from the same structured JSON as `--json`.

---

## 8.2 Installation and Update Model

- **Install Modes**: User-level only; no root/system install. Options:
  - `pipx install codeplane`
  - Static binary from GitHub Releases
  - Optional: Homebrew, Winget

- **Upgrades**:
  - Manual via `cpl upgrade`
  - No auto-updates
  - Safe hot-restart of daemon
  - Index artifacts are forward-compatible if schema unchanged

---

## 8.3 Daemon Model and Lifecycle

### Default: **Single Global Daemon Per User**

- Manages multiple repositories
- Communicates over:
  - TCP (`127.0.0.1:<port>`)
  - Unix socket (Linux/macOS)
  - Windows named pipe

### Repo Activation

- Repo must be registered via `cpl init`
- Creates `.codeplane/`, repo UUID, config
- Index is lazily built or fetched

### Auto-Start Options

- Manual: `cpl start`
- OS service: 
  - macOS: `launchd`
  - Linux: `systemd --user`
  - Windows: user-mode service

Daemon startup includes:
- Git HEAD verification
- Overlay index diff
- Index consistency check

Daemon shutdown:
- Flushes writes
- Releases locks
- Leaves repo unchanged

---

## 8.4 Diagnostics and Operability

### `cpl doctor` Checks

- Daemon reachable
- Index integrity (shared + overlay)
- Commit hash matches Git HEAD
- Port/socket availability
- Config sanity
- Git clean state

### Runtime Introspection

- `cpl logs` with `--follow`
- `cpl inspect index`: paths, size, commit, overlay state
- Healthcheck: `/health` MCP endpoint (returns JSON)

Optional metrics or task history available from the SQLite operation ledger (see convergence section).

---

## 8.5 Shared Index Artifact Handling

- CI builds index artifact (shared, no secrets)
- Hosted via:
  - GitHub Releases
  - S3 / Azure Blob
- Downloaded via `cpl fetch-index`
- Stored in local cache, checksum-verified
- Replaces shared index if commit is newer
- Overlay never included, always local

---

## 8.6 Config Precedence and Structure

- **Layers**:
  1. CLI flags / env vars
  2. Per-repo (`.codeplane/config.toml`)
  3. Global (`~/.config/codeplane/config.toml`)
  4. Built-in defaults

- All config access via `cpl config`
- Safe defaults prevent footguns:
  - `.cplignore` auto-generated
  - Dangerous paths excluded
  - Overlay disabled by default in CI

---

## 8.7 Failure Recovery Playbooks

| Failure              | Detection                     | Recovery Command         |
|----------------------|-------------------------------|--------------------------|
| Corrupt index        | `cpl doctor` fails hash check | `cpl rebuild`            |
| Schema mismatch      | Startup error                 | `cpl rebuild`, or upgrade index |
| Port conflict        | `cpl start` error             | `cpl config --global port=...` |
| Stale revision       | `cpl status` shows mismatch   | `cpl rebuild` or re-fetch |
| Daemon crash         | Daemon auto-exits             | Restart manually or via OS service |

Overlay index corruption is always local and recoverable without data loss.

---

## 8.8 Platform Constraints

- **Sockets**:
  - Unix: domain socket default
  - Windows: named pipe fallback
- **Filesystem**:
  - No background watchers
  - Hash-based change detection
- **Locking**:
  - Uses `portalocker` for cross-platform consistency
  - CRLF normalized internally
- **Path casing**:
  - Canonical casing tracked on Windows
  - Case sensitivity honored on Linux
