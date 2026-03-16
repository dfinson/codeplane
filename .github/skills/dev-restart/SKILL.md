---
name: dev-restart
description: Use this skill when explicitly instructed to restart the Tower server to apply frontend or backend changes.
---

# `dev-restart` Skill Instructions

> **⚠️ Do not use this skill unless the operator has explicitly asked you to restart the Tower server.**
>
> Restarting the server pauses all active agent sessions (including other agents working on this repo), shuts down the server, rebuilds the frontend, and then restarts everything. This is disruptive. Only proceed when instructed.

## When to use

Only invoke this skill when the operator says something like:

- "Restart the server"
- "Rebuild and restart Tower"
- "Run the dev restart script"
- "Deploy the changes"

Do **not** run this script speculatively, as part of routine testing, or as a "just in case" step after making frontend changes. The operator will ask explicitly when they want a restart.

---

## How to run

From the repository root (`/home/dave01/wsl-repos/agent-tower`):

```bash
python tools/dev_restart.py
```

### Options

| Flag           | Default     | Description                                                        |
|----------------|-------------|--------------------------------------------------------------------|
| `--host`       | `127.0.0.1` | Tower bind host                                                    |
| `--port`       | `8080`      | Tower port                                                         |
| `--pause-wait` | `10`        | Seconds to wait after pausing agents before killing the server     |

---

## What the script does

1. Collects all running and waiting-for-approval agent sessions via the Tower API.
2. Pauses each running session and waits `--pause-wait` seconds for agents to reach a stopping point.
3. Stops the Tower server (SIGTERM → SIGKILL if needed).
4. Builds the frontend (`npm run build`). If the build fails the server is **not** restarted — fix the error and run again.
5. Restarts the server in the background, waits for `/health` to return 200.
6. Resumes all previously active sessions with a context message explaining the restart.

---

## What to do if it fails

- **Build failure**: The server is already stopped. Fix the build error, then re-run the script (or start manually with `tower up --tunnel --password aerosonic101`).
- **Server didn't become healthy**: Check Tower logs for startup errors.
- **A session failed to resume**: Resume it from the Tower UI or via `POST /api/jobs/{id}/resume`.
