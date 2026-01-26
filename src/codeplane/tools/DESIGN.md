# Tools Module — Design Spec

## Scope

The tools module contains implementations for all 12 MCP tools. Each tool is a thin handler that validates input, delegates to domain services, and formats output.

### Responsibilities

- Input validation (parameter schemas)
- Delegation to appropriate domain modules
- Output formatting (including `_session` block)
- Error mapping to CodePlane error codes
- Streaming setup for SSE variants

### Tools (from SPEC.md §20.4–20.5)

| Tool | Delegates To |
|------|--------------|
| `codeplane_search` | `index/` |
| `codeplane_map` | `index/` |
| `codeplane_read` | filesystem + `index/` metadata |
| `codeplane_mutate` | `mutation/` |
| `codeplane_git` | `git/` |
| `codeplane_refactor` | `refactor/` |
| `codeplane_test` | `testing/` |
| `codeplane_task` | `ledger/` + session |
| `codeplane_status` | daemon + `index/` + `refactor/` |
| `codeplane_refactor_stream` | `refactor/` (SSE) |
| `codeplane_test_stream` | `testing/` (SSE) |
| `codeplane_reindex_stream` | `index/` (SSE) |

---

## Design Options

### Option A: Function per tool

```python
# tools/search.py
async def codeplane_search(params: SearchParams, session: Session) -> SearchResult:
    results = await index_service.search(params.query, params.mode, params.scope)
    return SearchResult(results=results, _session=session.state())
```

**Pros:** Simple, direct
**Cons:** May duplicate validation/error handling

### Option B: Tool class with base

```python
# tools/base.py
class Tool:
    name: str
    def validate(self, params: dict) -> Params: ...
    async def execute(self, params: Params, session: Session) -> Result: ...

# tools/search.py
class SearchTool(Tool):
    name = "codeplane_search"
    async def execute(self, params, session):
        ...
```

**Pros:** Consistent interface, easy to enumerate tools
**Cons:** More boilerplate

### Option C: Decorated functions with registry

```python
# tools/registry.py
TOOLS = {}

def tool(name: str):
    def decorator(fn):
        TOOLS[name] = fn
        return fn
    return decorator

# tools/search.py
@tool("codeplane_search")
async def search(params: SearchParams, session: Session) -> SearchResult:
    ...
```

**Pros:** Simple functions, automatic registration
**Cons:** Global state

---

## Recommended Approach

**Option C (Decorated functions)** — keeps implementations simple, automatic discovery, easy to test individual tools.

---

## File Plan

```
tools/
├── __init__.py      # Export TOOLS registry
├── registry.py      # @tool decorator, TOOLS dict
├── search.py        # codeplane_search
├── map.py           # codeplane_map
├── read.py          # codeplane_read
├── mutate.py        # codeplane_mutate
├── git.py           # codeplane_git
├── refactor.py      # codeplane_refactor, codeplane_refactor_stream
├── test.py          # codeplane_test, codeplane_test_stream
├── task.py          # codeplane_task
├── status.py        # codeplane_status
└── reindex.py       # codeplane_reindex_stream
```

## Dependencies

- `pydantic` — Parameter validation and response models
- Domain modules (`index`, `refactor`, `mutation`, `git`, `testing`, `ledger`)

## Key Interfaces

```python
# registry.py
ToolFn = Callable[[dict, Session], Awaitable[dict]]
TOOLS: dict[str, ToolFn] = {}

def tool(name: str) -> Callable[[ToolFn], ToolFn]: ...

def get_tool(name: str) -> ToolFn | None: ...

def list_tools() -> list[str]: ...
```

## Open Questions

1. How to handle streaming vs non-streaming variants?
   - Separate functions, or single function with `stream: bool` param?
   - **Recommendation:** Separate functions (`codeplane_refactor` vs `codeplane_refactor_stream`)
2. Where does parameter validation live?
   - In tool function (Pydantic model), or in MCP handler?
   - **Recommendation:** Pydantic model in tool file, MCP handler catches ValidationError
