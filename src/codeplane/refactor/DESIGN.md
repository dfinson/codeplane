# Refactor Module — Design Spec

## Scope

The refactor module provides SCIP-based semantic refactoring: rename, move, delete. It queries pre-indexed SCIP semantic data, handles multi-context repos, divergence detection, and coordinates with the mutation engine.

### Responsibilities

- Query SCIP semantic index for symbol occurrences
- Refactor operation planning from SCIP data
- Multi-context handling (query multiple contexts, merge results)
- Divergence detection and reporting
- Preview generation before apply
- Comment/docstring sweep (non-semantic post-refactor)
- Coordination with mutation engine for atomic apply
- Mutation gate enforcement (all affected files must be CLEAN)

### From SPEC.md

- §7.5: Semantic Layer (SCIP Batch Indexers)
- §8: Deterministic refactor engine
- §8.3a: Architecture overview
- §8.5: Refactor execution flow
- §8.6: Multi-context handling
- §8.11: Comment sweep

---

## Design Options

### Option A: Monolithic RefactorEngine

```python
class RefactorEngine:
    def __init__(self, config, semantic_index):
        self.semantic_index = semantic_index
    
    async def rename(self, symbol, new_name, contexts) -> RefactorResult: ...
    async def move(self, from_path, to_path) -> RefactorResult: ...
    async def preview(self, refactor_id) -> Preview: ...
    async def apply(self, refactor_id) -> Delta: ...
```

**Pros:** Single entry point
**Cons:** Large class, index queries mixed with refactor logic

### Option B: Separated concerns

```python
class SemanticQuery:
    def find_occurrences(self, symbol: str, contexts: list[str]) -> list[Occurrence]: ...
    def get_file_states(self, paths: list[Path]) -> dict[Path, FileState]: ...

class RefactorPlanner:
    def plan_rename(self, symbol, new_name, occurrences) -> RefactorPlan: ...
    def plan_move(self, from_path, to_path, occurrences) -> RefactorPlan: ...

class MutationGate:
    def check(self, paths: list[Path]) -> GateResult: ...

class RefactorExecutor:
    async def preview(self, plan: RefactorPlan) -> Preview: ...
    async def apply(self, plan: RefactorPlan) -> Delta: ...
```

**Pros:** Clear responsibilities, testable units
**Cons:** More coordination code

---

## Recommended Approach

**Option B (Separated concerns)** — SemanticQuery handles SCIP index lookups, MutationGate checks file states, RefactorPlanner generates edit plans, RefactorExecutor handles preview/apply.

---

## File Plan

```
refactor/
├── __init__.py
├── engine.py        # High-level RefactorEngine facade
├── query.py         # SCIP index queries (find occurrences, resolve symbols)
├── planner.py       # Refactor planning (rename, move, delete)
├── gate.py          # Mutation gate (check file states)
├── contexts.py      # Multi-context detection and selection
├── patch.py         # Patch representation and merging
└── sweep.py         # Comment/docstring sweep (non-semantic)
```

## Dependencies

- `protobuf` — SCIP format parsing
- Standard library only for core logic

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

# query.py
class SemanticQuery:
    def find_symbol(self, name: str, position: Position) -> Symbol | None: ...
    def find_occurrences(self, symbol: Symbol, contexts: list[str] | None = None) -> list[Occurrence]: ...
    def get_file_state(self, path: Path) -> FileState: ...

# gate.py
class MutationGate:
    def check(self, paths: list[Path]) -> GateResult: ...
    def wait_for_clean(self, paths: list[Path], timeout: float) -> bool: ...
```

## Refactor Execution Flow (from SPEC.md §8.5)

1. User requests refactor (e.g., rename symbol)
2. MutationGate checks all affected files are CLEAN
3. SemanticQuery finds all occurrences from SCIP index
4. RefactorPlanner generates edit plan from occurrences
5. Preview edits to user
6. Apply edits atomically via mutation engine
7. Mark affected files as DIRTY, enqueue semantic refresh

## Open Questions

1. SCIP index access: direct file read vs index service?
   - **Recommendation:** SQLite-backed index with SCIP data imported
2. Multi-context query: parallel vs sequential?
   - **Recommendation:** Sequential for simplicity; parallelize if slow
3. Force syntactic mode: how to expose?
   - **Recommendation:** Option in RefactorOptions, bypasses mutation gate
