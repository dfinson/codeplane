# E2E Test Proposals: Real-World Repository Indexing

This document proposes comprehensive end-to-end tests using real-world open-source repositories to validate the CodePlane indexing infrastructure at scale.

## Goals

1. **Validate scalability** — Index repositories with 10K–500K lines of code
2. **Test language coverage** — Verify Python, JavaScript/TypeScript, Go, Rust parsing
3. **Benchmark performance** — Establish baseline indexing speeds
4. **Detect regressions** — Catch edge cases in real-world code patterns
5. **Validate incremental updates** — Ensure file watcher and reconciliation work correctly

---

## Proposed Test Repositories

### Tier 1: Small Python Projects (1K–10K LOC)

| Repository | LOC | Features to Test |
|------------|-----|------------------|
| `psf/requests` | ~5K | Popular HTTP library, decorators, classes |
| `pallets/click` | ~8K | CLI framework, decorators, nested classes |
| `python-attrs/attrs` | ~6K | Dataclasses, slots, descriptors |
| `more-itertools/more-itertools` | ~5K | Generators, itertools patterns |

**Test Focus:**
- Complete indexing in < 5 seconds
- All function/class definitions extracted
- Import graph correctly built
- Reference resolution for local bindings

### Tier 2: Medium Python Projects (10K–50K LOC)

| Repository | LOC | Features to Test |
|------------|-----|------------------|
| `pallets/flask` | ~20K | Web framework, blueprints, decorators |
| `django/django` (core only) | ~50K | ORM, migrations, complex class hierarchies |
| `pydantic/pydantic` | ~30K | Validators, generic types, metaclasses |
| `tiangolo/fastapi` | ~15K | ASGI, type hints, dependency injection |

**Test Focus:**
- Indexing in < 30 seconds
- Complex inheritance hierarchies
- Decorator stacking
- Type hint extraction (Pydantic models, FastAPI routes)

### Tier 3: Large Multi-Language Projects (50K–200K LOC)

| Repository | Languages | Features to Test |
|------------|-----------|------------------|
| `microsoft/vscode` (subset) | TypeScript | Large TS codebase, decorators, interfaces |
| `hashicorp/terraform` (provider) | Go | Go interfaces, struct embedding |
| `rust-lang/rust-analyzer` | Rust | Rust traits, lifetimes, macros |

**Test Focus:**
- Multi-language context detection
- Probe accuracy for language family
- Cross-file reference patterns

---

## Proposed Test Scenarios

### Scenario 1: Full Index from Scratch

```python
@pytest.mark.slow
@pytest.mark.parametrize("repo", TIER_1_REPOS)
def test_full_index_tier1(repo: str, tmp_path: Path) -> None:
    """Index a small repo from scratch."""
    # Clone repo to tmp_path
    clone_repo(repo, tmp_path)
    
    # Run full discovery + indexing
    db = Database(tmp_path / ".codeplane" / "index.db")
    db.create_all()
    
    # Discover contexts
    contexts = discover_contexts(tmp_path)
    assert len(contexts) >= 1
    
    # Index all files
    for ctx in contexts:
        indexer = StructuralIndexer(db, tmp_path)
        result = indexer.index_files(ctx.files, context_id=ctx.id)
        
        assert result.errors == []
        assert result.defs_extracted > 0
        assert result.refs_extracted > 0
    
    # Verify queryability
    with db.session() as session:
        facts = FactQueries(session)
        
        # Should have significant number of defs
        all_defs = facts.list_all_defs(limit=10000)
        assert len(all_defs) > 100
```

### Scenario 2: Incremental Update Simulation

```python
@pytest.mark.slow
def test_incremental_update(indexed_repo: IndexedRepo) -> None:
    """Simulate editing a file and re-indexing."""
    db, repo_path = indexed_repo
    
    # Get a Python file
    py_files = list(repo_path.glob("**/*.py"))
    target = py_files[0]
    
    # Record current state
    with db.session() as session:
        before_count = session.exec(select(func.count()).select_from(DefFact)).one()
    
    # Modify file (add a function)
    original = target.read_text()
    target.write_text(original + "\n\ndef _test_injected(): pass\n")
    
    # Re-index just that file
    indexer = StructuralIndexer(db, repo_path)
    result = indexer.index_files([str(target.relative_to(repo_path))], context_id=1)
    
    assert result.errors == []
    
    # Verify new function was indexed
    with db.session() as session:
        defs = session.exec(
            select(DefFact).where(DefFact.name == "_test_injected")
        ).all()
        assert len(defs) == 1
    
    # Restore file
    target.write_text(original)
```

### Scenario 3: File Watcher Integration

```python
@pytest.mark.slow
@pytest.mark.asyncio
async def test_file_watcher_real_repo(indexed_repo: IndexedRepo) -> None:
    """Test file watcher detects changes in real repo."""
    db, repo_path = indexed_repo
    
    # Start background indexer
    config = WatcherConfig(root=repo_path, debounce_seconds=0.5)
    watcher = FileWatcher(config)
    
    changes_detected: list[FileChangeEvent] = []
    
    async def on_change(event: FileChangeEvent) -> None:
        changes_detected.append(event)
    
    indexer = BackgroundIndexer(
        watcher=watcher,
        indexer=StructuralIndexer(db, repo_path),
        context_id=1,
        on_change=on_change,
    )
    
    # Start watching
    task = asyncio.create_task(indexer.start())
    await asyncio.sleep(0.5)  # Let watcher initialize
    
    # Modify a file
    py_file = list(repo_path.glob("**/*.py"))[0]
    original = py_file.read_text()
    py_file.write_text(original + "\n# test comment\n")
    
    # Wait for detection
    await asyncio.sleep(2.0)
    
    # Stop watcher
    await indexer.stop()
    task.cancel()
    
    # Verify change was detected
    assert len(changes_detected) >= 1
    
    # Restore
    py_file.write_text(original)
```

### Scenario 4: Lexical Search Quality

```python
@pytest.mark.slow
def test_lexical_search_quality(indexed_repo_with_lexical: IndexedRepo) -> None:
    """Verify lexical search returns relevant results."""
    db, repo_path, lexical = indexed_repo_with_lexical
    
    # Search for common patterns
    test_queries = [
        ("def ", 100),  # Should find many function defs
        ("class ", 50),  # Should find class definitions
        ("import ", 100),  # Should find imports
        ("return ", 100),  # Should find return statements
    ]
    
    for query, min_expected in test_queries:
        results = lexical.search(query, limit=200)
        assert len(results.results) >= min_expected, f"Query '{query}' returned too few results"
```

### Scenario 5: Query Performance Benchmarks

```python
@pytest.mark.slow
@pytest.mark.benchmark
def test_def_lookup_performance(indexed_large_repo: IndexedRepo, benchmark) -> None:
    """Benchmark definition lookup speed."""
    db, _ = indexed_large_repo
    
    def lookup_defs():
        with db.session() as session:
            facts = FactQueries(session)
            # Look up 100 random definitions by name
            for name in COMMON_NAMES:
                facts.list_defs_by_name(unit_id=1, name=name, limit=10)
    
    result = benchmark(lookup_defs)
    
    # Should complete 100 lookups in < 1 second
    assert result.stats.mean < 1.0
```

---

## Test Infrastructure Requirements

### 1. Repository Cache

```python
# conftest.py
REPO_CACHE = Path.home() / ".codeplane-test-cache"

@pytest.fixture(scope="session")
def cached_repo(request) -> Path:
    """Clone and cache repo for test session."""
    repo_url = request.param
    repo_name = repo_url.split("/")[-1]
    cache_path = REPO_CACHE / repo_name
    
    if not cache_path.exists():
        subprocess.run(["git", "clone", "--depth=1", repo_url, str(cache_path)])
    
    return cache_path
```

### 2. Indexed Repo Fixtures

```python
@pytest.fixture
def indexed_repo(cached_repo: Path, tmp_path: Path) -> Generator[IndexedRepo, None, None]:
    """Provide a fully indexed repository."""
    # Copy to tmp to avoid modifying cache
    shutil.copytree(cached_repo, tmp_path / "repo")
    repo_path = tmp_path / "repo"
    
    # Index
    db = Database(tmp_path / "index.db")
    db.create_all()
    
    ctx = Context(name="test", language_family="python", root_path=str(repo_path))
    with db.session() as session:
        session.add(ctx)
        session.commit()
    
    indexer = StructuralIndexer(db, repo_path)
    py_files = [str(p.relative_to(repo_path)) for p in repo_path.glob("**/*.py")]
    indexer.index_files(py_files, context_id=1)
    
    yield db, repo_path
```

### 3. Benchmark Markers

```toml
# pyproject.toml additions
[tool.pytest.ini_options]
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "benchmark: marks tests as benchmarks",
    "e2e: marks tests as end-to-end",
]
```

---

## Expected Outcomes

| Repository | Index Time | Defs | Refs | Errors |
|------------|------------|------|------|--------|
| requests | < 3s | ~200 | ~2K | 0 |
| click | < 5s | ~300 | ~3K | 0 |
| flask | < 15s | ~1K | ~10K | 0 |
| pydantic | < 20s | ~2K | ~15K | 0 |

---

## Implementation Plan

### Phase 1: Infrastructure (Week 1)
- [ ] Create `tests/e2e/` directory structure
- [ ] Implement repository caching fixtures
- [ ] Add slow test markers to pytest config

### Phase 2: Tier 1 Tests (Week 2)
- [ ] Test `requests` indexing
- [ ] Test `click` indexing
- [ ] Test `attrs` indexing
- [ ] Establish baseline metrics

### Phase 3: Tier 2 Tests (Week 3)
- [ ] Test `flask` indexing
- [ ] Test `pydantic` indexing
- [ ] Test `fastapi` indexing
- [ ] Add incremental update scenarios

### Phase 4: Advanced Scenarios (Week 4)
- [ ] File watcher integration tests
- [ ] Lexical search quality tests
- [ ] Performance benchmarks
- [ ] Multi-language context tests

---

## CI/CD Integration

```yaml
# .github/workflows/e2e-tests.yml
name: E2E Tests

on:
  schedule:
    - cron: '0 2 * * *'  # Nightly
  workflow_dispatch:

jobs:
  e2e:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        repo: [requests, click, flask, pydantic]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install dependencies
        run: make dev
      - name: Run E2E tests
        run: pytest tests/e2e -m e2e -k ${{ matrix.repo }} --timeout=300
```

---

## Notes

- E2E tests should be excluded from regular CI (too slow)
- Run nightly or on-demand
- Cache cloned repos between runs to speed up test setup
- Consider using shallow clones (`--depth=1`) for faster cloning
- Track metrics over time to detect performance regressions
