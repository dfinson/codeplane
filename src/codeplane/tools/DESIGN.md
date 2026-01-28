# Tools Module — Design Spec

## Scope

The tools module contains MCP tool implementations using FastMCP. Each tool is a decorated function that validates input, delegates to domain services, and returns structured output wrapped in a response envelope.

### Responsibilities

- MCP tool registration via `@codeplane_tool(mcp)` decorator
- Input validation (auto-generated from type hints)
- Delegation to appropriate domain modules
- Output formatting via `ToolResponse[T]` envelope
- Progress reporting via `Context.report_progress()`

### Architecture Decision: FastMCP-Native Design

**Decision:** Use FastMCP's native patterns instead of custom MCP protocol handling.

**Rationale:** See SPEC.md section 22 and GitHub issue #123 for full ADR.

**Key points:**
1. **Tool granularity:** Split mega-tools into namespaced families (e.g., `git_*`, `search_*`)
2. **Response envelope:** All responses wrapped in `ToolResponse[T]` with `meta` field
3. **Progress reporting:** Use `Context.report_progress()` instead of separate `_stream` tools
4. **Pagination:** Use `Page[T]` Pydantic models with cursor support

### Tool Families (from SPEC.md §22.4)

| Family | Count | Delegates To |
|--------|-------|--------------|
| `git_*` | ~20 | `git/` (GitOps) |
| `search_*` | 1 | `index/` |
| `file_*` | 3 | filesystem + `index/` |
| `refactor_*` | 3 | `refactor/` |
| `test_*` | 2 | `testing/` |
| `session_*` | 3 | `ledger/` + session |
| `status_*` | 1 | daemon + `index/` |

**Total: ~33 tools**

---

## Implementation Pattern

```python
# tools/git.py
from codeplane.mcp import mcp, codeplane_tool
from codeplane.git import GitOps

@codeplane_tool(mcp)
def git_status(repo_path: str, include_untracked: bool = True) -> RepoStatus:
    """Get repository status including staged, unstaged, and untracked files."""
    return GitOps(repo_path).status(include_untracked=include_untracked)
```

### Response Envelope

All tools return `ToolResponse[T]`:

```python
@dataclass
class ToolResponse[T]:
    result: T           # The actual tool result
    meta: ResponseMeta  # session_id, request_id, timestamp_ms
```

### Progress Reporting

Long operations use FastMCP's native progress:

```python
@codeplane_tool(mcp)
async def test_run(paths: list[str], ctx: Context) -> TestResult:
    """Run tests with progress updates."""
    tests = discover_tests(paths)
    for i, test in enumerate(tests):
        await ctx.report_progress(progress=i, total=len(tests))
        run_test(test)
    return TestResult(...)
```

---

## File Plan

```
tools/
├── __init__.py      # Export mcp instance and codeplane_tool decorator
├── server.py        # FastMCP("CodePlane") instance
├── envelope.py      # ToolResponse, ResponseMeta, @codeplane_tool
├── git.py           # git_* tools (~20)
├── search.py        # search_* tools
├── file.py          # file_* tools
├── refactor.py      # refactor_* tools
├── test.py          # test_* tools
├── session.py       # session_* tools
└── status.py        # status_* tools
```

---

## Dependencies

- `mcp` package (FastMCP)
- Domain modules: `git/`, `index/`, `mutation/`, `refactor/`, `testing/`, `ledger/`

---

## References

- SPEC.md section 22 (MCP Integration)
- GitHub issue #123 (ADR: FastMCP-native tool design)


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
