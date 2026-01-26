# Daemon Module — Design Spec

## Scope

The daemon module runs the long-lived HTTP server that exposes MCP tools and REST endpoints. One daemon per repository.

### Responsibilities

- HTTP server lifecycle (start, graceful shutdown)
- MCP JSON-RPC protocol handling
- REST endpoint routing (`/health`, `/ready`, `/metrics`, `/status`)
- SSE streaming for long operations
- Session management (auto-create, timeout, override)
- Authentication (bearer token validation)
- Request logging and correlation IDs

### From SPEC.md

- §4.2: Daemon lifecycle, HTTP localhost, ephemeral port, token auth
- §20.2: Protocol architecture
- §20.3: Session model
- §20.7: REST endpoints

---

## Design Options

### Option A: Starlette + Uvicorn

```python
from starlette.applications import Starlette
from starlette.routing import Route
import uvicorn

app = Starlette(routes=[
    Route('/health', health_handler),
    Route('/mcp', mcp_handler, methods=['POST']),
])

uvicorn.run(app, host='127.0.0.1', port=0)
```

**Pros:** Lightweight, ASGI, good SSE support, production-ready
**Cons:** Manual MCP protocol handling

### Option B: FastAPI

```python
from fastapi import FastAPI
app = FastAPI()

@app.get('/health')
async def health():
    return {'status': 'ok'}
```

**Pros:** Auto OpenAPI docs, validation, popular
**Cons:** Heavier, OpenAPI not useful for MCP

### Option C: aiohttp

```python
from aiohttp import web

app = web.Application()
app.router.add_get('/health', health_handler)
web.run_app(app)
```

**Pros:** Mature async, good SSE
**Cons:** Different API style, less common now

---

## Recommended Approach

**Starlette + Uvicorn** — minimal, ASGI-native, excellent SSE support, no unnecessary features.

---

## File Plan

```
daemon/
├── __init__.py
├── server.py        # Starlette app, startup/shutdown, port/token file management
├── session.py       # Session lifecycle, timeout, state tracking
├── mcp.py           # MCP JSON-RPC 2.0 protocol handler
├── rest.py          # REST endpoints (/health, /ready, /metrics, /status)
└── sse.py           # SSE response helpers for streaming tools
```

## Dependencies

- `starlette` — ASGI framework
- `uvicorn` — ASGI server
- `sse-starlette` — SSE support (or manual implementation)

## Key Interfaces

```python
# server.py
async def start_daemon(repo_root: Path) -> None:
    """Start HTTP server, write port/token files."""

async def stop_daemon() -> None:
    """Graceful shutdown, delete port/token files."""

# session.py
class SessionManager:
    def get_or_create(self, connection_id: str) -> Session: ...
    def get_by_id(self, session_id: str) -> Session | None: ...
    def close(self, session_id: str, reason: str) -> None: ...
    def cleanup_expired(self) -> None: ...

# mcp.py
async def handle_mcp_request(request: JSONRPCRequest, session: Session) -> JSONRPCResponse:
    """Route to appropriate tool handler."""
```

## Open Questions

1. How to get ephemeral port before server starts?
   - Uvicorn with `port=0`, then read `server.sockets[0].getsockname()[1]`
2. Session timeout background task?
   - Use Starlette `on_startup` to schedule periodic cleanup
3. Graceful shutdown signal handling?
   - `SIGTERM`/`SIGINT` → set shutdown flag → drain connections → exit
