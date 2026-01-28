# Daemon Module — Design Spec

## Scope

The daemon module runs the long-lived HTTP server that exposes MCP tools and REST endpoints. One daemon per repository.

### Responsibilities

- HTTP server lifecycle (start, graceful shutdown)
- FastMCP server hosting (via `mcp` package)
- REST endpoint routing (`/health`, `/ready`, `/metrics`, `/status`)
- Authentication (bearer token validation)
- Request logging and correlation IDs

### From SPEC.md

- §4.2: Daemon lifecycle, HTTP localhost, ephemeral port, token auth
- §22: MCP Integration (FastMCP-native design)

---

## Architecture Decision

**FastMCP + Starlette** — MCP protocol handled by FastMCP SDK, supplementary REST endpoints via Starlette.

FastMCP handles:
- MCP JSON-RPC protocol
- Tool registration and dispatch
- Progress reporting via `Context.report_progress()`
- SSE streaming for tool execution

Starlette handles:
- REST endpoints (`/health`, `/ready`, `/metrics`, `/status`)
- Bearer token authentication middleware

See SPEC.md §22 and `tools/DESIGN.md` for MCP tool architecture.

---

## File Plan

```
daemon/
├── __init__.py
├── server.py        # Server lifecycle, port/token file management
├── rest.py          # REST endpoints (/health, /ready, /metrics, /status)
└── auth.py          # Bearer token validation middleware
```

MCP tool definitions live in `tools/` module (see `tools/DESIGN.md`).

## Dependencies

- `mcp` — FastMCP SDK
- `starlette` — ASGI framework for REST endpoints
- `uvicorn` — ASGI server

## Key Interfaces

```python
# server.py
async def start_daemon(repo_root: Path) -> None:
    """Start HTTP server, write port/token files."""

async def stop_daemon() -> None:
    """Graceful shutdown, delete port/token files."""
```

## Open Questions

1. How to get ephemeral port before server starts?
   - Uvicorn with `port=0`, then read `server.sockets[0].getsockname()[1]`
2. Graceful shutdown signal handling?
   - `SIGTERM`/`SIGINT` → set shutdown flag → drain connections → exit

## References

- SPEC.md §22 (MCP Integration)
- `tools/DESIGN.md` (MCP tool architecture)
- GitHub issue #123 (ADR: FastMCP-native tool design)
