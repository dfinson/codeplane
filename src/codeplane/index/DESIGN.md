# Index Module — Design Spec

## Scope

The index module builds and queries the hybrid index: lexical (Tantivy), structural (SQLite), and symbol graph.

### Responsibilities

- Lexical index via Tantivy (paths, identifiers, content)
- Structural metadata in SQLite (symbols, relations, chunks)
- Symbol graph construction and traversal
- Incremental updates based on Git blob hash / content hash
- Atomic index swaps (temp dir → replace)
- Reconciliation with filesystem (change detection)
- Repo map generation (structure, languages, entry points)

### From SPEC.md

- §5: Repository reconciliation
- §7: Indexing & retrieval architecture
- §7.2: Lexical index (Tantivy)
- §7.3: Structural metadata (SQLite)
- §7.6: Graph index
- §7.8: Atomic update protocol

---

## Design Options

### Option A: Single IndexEngine class

```python
class IndexEngine:
    def __init__(self, repo_root: Path):
        self.tantivy = TantivyIndex(...)
        self.sqlite = SQLiteMetadata(...)
        self.graph = SymbolGraph(...)
    
    def search(self, query, mode) -> list[Result]: ...
    def get_symbol(self, name) -> Symbol | None: ...
    def reindex(self, paths: list[Path]) -> None: ...
```

**Pros:** Single entry point, easy to coordinate
**Cons:** Large class, mixes concerns

### Option B: Separate engines with coordinator

```python
class LexicalIndex:
    def search(self, query) -> list[Hit]: ...
    def update(self, docs: list[Doc]) -> None: ...

class StructuralIndex:
    def get_symbols(self, path) -> list[Symbol]: ...
    def get_relations(self, symbol_id) -> list[Relation]: ...

class SymbolGraph:
    def expand(self, symbol_id, depth=2) -> set[Symbol]: ...

class IndexCoordinator:
    def __init__(self, lexical, structural, graph): ...
    def search(self, query, mode) -> list[Result]: ...
```

**Pros:** Clear separation, testable components
**Cons:** More wiring

### Option C: Functional approach

```python
# lexical.py
def search_lexical(index_path: Path, query: str) -> list[Hit]: ...
def update_lexical(index_path: Path, docs: list[Doc]) -> None: ...

# structural.py
def query_symbols(db_path: Path, filters) -> list[Symbol]: ...

# coordinator.py
def search(repo: Repo, query: str, mode: str) -> list[Result]:
    hits = search_lexical(repo.index_path, query)
    ...
```

**Pros:** Simple, no state management
**Cons:** Repeated path passing, no caching

---

## Recommended Approach

**Option B (Separate engines with coordinator)** — clean separation between Tantivy, SQLite, and graph; coordinator handles cross-cutting queries.

---

## File Plan

```
index/
├── __init__.py
├── lexical.py       # Tantivy wrapper (tantivy-py)
├── structural.py    # SQLite metadata (symbols, relations, chunks)
├── graph.py         # Symbol graph traversal
├── reconcile.py     # Change detection, Git blob hash comparison
├── coordinator.py   # High-level search, reindex orchestration
└── schema.sql       # SQLite schema (or in structural.py)
```

## Dependencies

- `tantivy` — Tantivy Python bindings (tantivy-py)
- `tree-sitter` — Parsing for symbol extraction
- `tree-sitter-languages` — Grammar bundles
- Standard library `sqlite3`

## Key Interfaces

```python
# coordinator.py
class IndexCoordinator:
    async def search(self, query: str, mode: SearchMode, scope: Scope) -> list[SearchResult]: ...
    async def get_symbol(self, name: str, path: str | None) -> Symbol | None: ...
    async def get_references(self, symbol: Symbol) -> list[Reference]: ...
    async def get_map(self, include: list[str]) -> RepoMap: ...
    async def reindex_incremental(self, changed_paths: list[Path]) -> IndexStats: ...
    async def reindex_full(self) -> IndexStats: ...

# reconcile.py
class Reconciler:
    def detect_changes(self) -> ChangeSet: ...
    def get_repo_fingerprint(self) -> str: ...
```

## SQLite Schema (from SPEC.md §7.3)

```sql
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    blob_hash TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    language TEXT
);

CREATE TABLE symbols (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,  -- function, class, variable, etc.
    path TEXT NOT NULL,
    line INTEGER NOT NULL,
    column INTEGER,
    language TEXT,
    container_id INTEGER REFERENCES symbols(id)
);

CREATE TABLE relations (
    src_id INTEGER REFERENCES symbols(id),
    dst_id INTEGER REFERENCES symbols(id),
    kind TEXT NOT NULL,  -- calls, imports, inherits, contains
    PRIMARY KEY (src_id, dst_id, kind)
);
```

## Open Questions

1. Tantivy Python bindings maturity?
   - `tantivy-py` is maintained, but check version compatibility
2. Tree-sitter grammar loading strategy?
   - Bundle common grammars, lazy-load others from `~/.codeplane/grammars/`
3. Index location: `.codeplane/index/` vs separate paths for Tantivy and SQLite?
   - **Recommendation:** `.codeplane/index.tantivy/` and `.codeplane/index.sqlite`
