# Refactor Module — Design Spec

## Scope

The refactor module provides LSP-based semantic refactoring: rename, move, delete. It handles multi-context repos, divergence detection, and patch merging.

### Responsibilities

- LSP client management (start, stop, restart per language)
- Refactor operation planning via LSP (`textDocument/rename`, etc.)
- Multi-context handling (parallel execution, patch merging)
- Divergence detection and reporting
- Preview generation before apply
- Comment/docstring sweep (non-semantic post-refactor)
- Coordination with mutation engine for atomic apply

### From SPEC.md

- §7.11: LSP management
- §8: Deterministic refactor engine
- §8.3a: Architecture overview
- §8.5: Single vs multi-context modes
- §8.6: Divergence handling
- §8.11: Comment sweep

---

## Design Options

### Option A: Monolithic RefactorEngine

```python
class RefactorEngine:
    def __init__(self, config):
        self.lsp_clients = {}  # lang -> LSPClient
    
    async def rename(self, symbol, new_name, contexts) -> RefactorResult: ...
    async def move(self, from_path, to_path) -> RefactorResult: ...
    async def preview(self, refactor_id) -> Preview: ...
    async def apply(self, refactor_id) -> Delta: ...
```

**Pros:** Single entry point
**Cons:** Large class, LSP management mixed with refactor logic

### Option B: Separated concerns

```python
class LSPManager:
    async def get_client(self, language: str) -> LSPClient: ...
    async def restart(self, language: str) -> None: ...

class RefactorPlanner:
    async def plan_rename(self, symbol, new_name, contexts) -> RefactorPlan: ...
    async def plan_move(self, from_path, to_path) -> RefactorPlan: ...

class PatchMerger:
    def merge(self, patches: list[Patch]) -> MergedPatch | Divergence: ...

class RefactorExecutor:
    async def preview(self, plan: RefactorPlan) -> Preview: ...
    async def apply(self, plan: RefactorPlan) -> Delta: ...
```

**Pros:** Clear responsibilities, testable units
**Cons:** More coordination code

### Option C: Actor model

```python
# Each LSP is an actor/subprocess
class LSPActor:
    async def send_request(self, method, params) -> Response: ...

class RefactorCoordinator:
    async def execute(self, operation: RefactorOp) -> Result:
        # Fan out to relevant LSP actors
        # Collect and merge results
```

**Pros:** Natural fit for LSP subprocesses
**Cons:** Complexity, debugging harder

---

## Recommended Approach

**Option B (Separated concerns)** — LSPManager handles subprocess lifecycle, RefactorPlanner handles operation logic, PatchMerger handles multi-context, RefactorExecutor handles preview/apply.

---

## File Plan

```
refactor/
├── __init__.py
├── engine.py        # High-level RefactorEngine facade
├── lsp_client.py    # Single LSP client (JSON-RPC over stdio)
├── lsp_manager.py   # LSP lifecycle (start, stop, restart, health)
├── planner.py       # Refactor planning (rename, move, delete)
├── contexts.py      # Multi-context detection and selection
├── patch.py         # Patch representation and merging
└── sweep.py         # Comment/docstring sweep (non-semantic)
```

## Dependencies

- `pygls` — LSP utilities (optional, may use raw JSON-RPC)
- `subprocess` + `asyncio` — LSP subprocess management
- Standard library `json` for JSON-RPC

## Key Interfaces

```python
# engine.py
class RefactorEngine:
    async def rename(self, symbol: str, new_name: str, options: RefactorOptions) -> RefactorResult: ...
    async def move(self, from_path: Path, to_path: Path, options: RefactorOptions) -> RefactorResult: ...
    async def delete(self, target: str, options: RefactorOptions) -> RefactorResult: ...
    async def preview(self, refactor_id: str) -> Preview: ...
    async def apply(self, refactor_id: str) -> Delta: ...
    async def cancel(self, refactor_id: str) -> None: ...

# lsp_manager.py
class LSPManager:
    async def ensure_started(self, language: str) -> LSPClient: ...
    async def stop(self, language: str) -> None: ...
    async def stop_all(self) -> None: ...
    def get_status(self) -> dict[str, LSPStatus]: ...
    def get_pending_installs(self) -> list[str]: ...

# lsp_client.py
class LSPClient:
    async def initialize(self, root_uri: str) -> InitializeResult: ...
    async def text_document_rename(self, uri: str, position: Position, new_name: str) -> WorkspaceEdit: ...
    async def shutdown(self) -> None: ...
```

## LSP Lifecycle (from SPEC.md §7.11)

1. On `cpl up`, LSPManager starts LSPs for configured languages
2. LSPs are lazy-started on first refactor operation
3. LSP crash → automatic restart (max 3 retries)
4. On `cpl down`, all LSPs terminated
5. New language detected → flag `pending_lsp_install`, don't auto-download

## Open Questions

1. LSP binary discovery: PATH vs explicit config?
   - **Recommendation:** Config first, then PATH fallback
2. File virtualization: inject via `didOpen` or let LSP read disk?
   - **Recommendation:** `didOpen` for full control (per SPEC.md §8.3a)
3. Worktree isolation: actual Git worktrees or virtual?
   - **Recommendation:** Start with in-memory virtualization; Git worktrees if needed for complex multi-context
