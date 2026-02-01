# E2E Test Proposals: Real-World Repository Indexing (Revised)

This document defines a truth-based, polyglot-capable end-to-end test suite
for validating the CodePlane indexing infrastructure. The goal is to verify
correctness, scalability, and incremental behavior of the Tier 0 + Tier 1
syntactic index, without asserting semantic or cross-language linkage.

This suite is designed to run reliably on local developer machines and standard
GitHub-hosted runners.

---

## Goals

1. Validate scalability
   Index repositories ranging from 10K–500K LOC without OOM or timeouts.

2. Validate polyglot support
   Correctly index multiple languages, contexts, and workspaces in a single repo.

3. Validate correctness
   Assert presence and metadata of specific anchor symbols, not just counts.

4. Validate incremental behavior
   Prove that single-file edits only reindex affected files and publish new epochs.

5. Validate background updates
   Ensure file watcher → background indexing → epoch publish works end to end.

6. Enforce performance budgets
   Fail tests when time or memory limits are exceeded.

---

## Repository Tiers

### Tier 1: Small Single-Language Repos (1K–10K LOC)

Purpose: correctness and fast feedback.

| Repository | Language | Why |
|-----------|----------|-----|
| psf/requests | Python | Functions, imports, decorators |
| pallets/click | Python | Nested scopes, decorators |
| python-attrs/attrs | Python | Class-heavy, slots |
| more-itertools/more-itertools | Python | Dense functional code |

### Tier 2: Medium Single-Language Repos (10K–50K LOC)

Purpose: stress syntax extraction and incremental updates.

| Repository | Language |
|-----------|----------|
| pallets/flask | Python |
| pydantic/pydantic | Python |
| tiangolo/fastapi | Python |

### Tier 3: Polyglot / Multi-Context Repos (Required)

Purpose: validate context discovery, routing, and multi-language indexing.
No cross-language semantic linkage is asserted.

Pick repos that are stable, popular, and not enormous. Each Tier 3 repo MUST:
- Contain at least 2 contexts/workspaces
- Contain at least 2 languages
- Have pinned commit SHAs for determinism

Example Tier 3 candidates (choose 2–3):
- A JS/TS monorepo with workspaces (package.json + workspace manifest)
- A Rust workspace repo with multiple crates (Cargo workspace)
- A mixed Go repo with scripts/config mixed in

For Tier 3, we test:
- Multiple contexts discovered
- Files routed to the correct context
- At least two language families indexed
- Anchor symbols present per-context (not repo-wide)

---

## Truth-Based Validation: Anchor Symbols

Stop asserting len(defs) > 0 and “defs_extracted > 0”.
Instead, validate a hardcoded list of anchor symbols per repo.

Create per-repo anchor definitions in tests/e2e/anchors/<repo>.yaml:

Example: tests/e2e/anchors/pallets_click.yaml

```yaml
repo: pallets/click
commit: <PINNED_SHA>
contexts:
  - root: .
    anchors:
      - name: Group
        kind: class
        file: click/core.py
        line_range: [1, 300]
      - name: echo
        kind: function
        file: click/utils.py
        line_range: [1, 300]
```

Anchor assertions:
- Exactly one matching DefFact exists in the expected unit/context
- file_id → File.path ends with expected file
- start_line is within line_range
- kind matches exactly

Optional: add “negative anchors” to ensure false positives don’t pass.

---

## Performance Budgets (Enforced)

Replace the “Expected Outcomes” table with a budgets file read by tests.

Create tests/e2e/budgets.json:

```json
{
  "psf/requests": {
    "full_index_seconds": 5,
    "incremental_seconds": 2,
    "max_rss_mb": 1500
  },
  "pallets/click": {
    "full_index_seconds": 7,
    "incremental_seconds": 2,
    "max_rss_mb": 1500
  },
  "polyglot_repo_1": {
    "full_index_seconds": 60,
    "incremental_seconds": 5,
    "max_rss_mb": 2500
  }
}
```

Budgets must be generous enough for GitHub runners but strict enough to catch regressions.

Measure:
- wall clock time for initialize/index phases
- peak RSS during indexing using psutil

Fail if any budget is exceeded.

---

## Proposed Test Scenarios

### Scenario 1: Full Index from Scratch (Truth-Based)

Applies to Tier 1, Tier 2, Tier 3.

Behavior:
1. Clone repo at pinned SHA (shallow clone + checkout)
2. Run full initialize: discovery + index all + publish initial epoch
3. Validate:
   - contexts discovered match expectation (Tier 3 must be >1)
   - anchor symbols validate in the correct context
   - epoch published and await_epoch works
   - performance budgets respected

Sketch:

```python
@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.parametrize("repo_case", REPO_CASES)
def test_full_index_truth_based(repo_case: RepoCase, tmp_path: Path) -> None:
    repo_path = materialize_repo(repo_case, tmp_path)
    budgets = load_budgets()
    anchors = load_anchors(repo_case)

    with rss_monitor() as rss:
        t0 = time.perf_counter()
        coord = IndexCoordinator(repo_path)
        result = asyncio.run(coord.initialize())
        elapsed = time.perf_counter() - t0

    assert result.errors == []
    assert elapsed <= budgets[repo_case.key]["full_index_seconds"]
    assert rss.peak_mb <= budgets[repo_case.key]["max_rss_mb"]

    # Context assertions
    assert_contexts(coord, anchors)

    # Anchor symbol assertions
    assert_anchor_symbols(coord, anchors)
```

### Scenario 2: Incremental Update Isolated (No Hidden Full Rescan)

Goal: prove only the changed file is reindexed.

Requirements:
- StructuralIndexer (or coordinator) must report touched_file_ids and touched_paths
- Alternatively, spy/monkeypatch the “index_files” call to verify input set

Test:
1. Start from indexed repo fixture
2. Edit one file (append a function/class)
3. Call reindex_incremental([changed_path]) and await_epoch
4. Assert:
   - only that file_id/path is touched
   - new def exists
   - no other file facts changed (optional: compare per-file content_hash or per-file def counts)

Sketch:

```python
@pytest.mark.e2e
@pytest.mark.slow
def test_incremental_update_touches_only_one_file(indexed_repo: IndexedRepo) -> None:
    coord, repo_path, case = indexed_repo
    target = pick_editable_file(repo_path, case)

    before_epoch = coord.get_current_epoch()
    before_touched = snapshot_file_facts(coord, case)

    apply_edit(target)

    stats = asyncio.run(coord.reindex_incremental([relpath(target, repo_path)]))
    assert stats.touched_paths == {relpath(target, repo_path)}

    ok = asyncio.run(coord.await_epoch(before_epoch + 1, timeout_seconds=10))
    assert ok

    assert_new_anchor_or_injected_def(coord, target)

    after_touched = snapshot_file_facts(coord, case)
    assert_only_target_changed(before_touched, after_touched, target)
```

### Scenario 3: File Watcher → Background Indexing → Epoch Publish

Goal: prove UX does not actively drive indexing; watcher does.

Test:
1. Initialize index
2. Start watcher + background index loop
3. Modify a file
4. Assert:
   - watcher reports an event (debounced)
   - background indexer reindexes file
   - epoch increments
   - anchor/injected def appears

Keep it a smoke test, not timing-fragile.

### Scenario 4: Lexical Search Quality (Minimal, Stable Assertions)

Avoid “return at least 100 results” style tests (too variable).
Instead, use anchor-based search assertions:

For each repo define 3–5 queries with expected hits containing known file paths.

Example:
- query: "class Group"
- expected_path_contains: "click/core.py"

Assert:
- at least one result
- top N contains expected path
- snippet contains token

### Scenario 5: Query Performance Micro-Budget (FactQueries)

Avoid pytest-benchmark variability in CI. Use a deterministic time budget:
- run N fixed queries
- require completion under X ms on runner

Example:
- list_defs_by_name for 20 known symbols
- list_refs_by_token for a few common tokens with limit=100
- require total under 1s for Tier 1 repos

---

## Test Infrastructure Requirements

### 1. Repo Cache (Shallow + Dirty Check)

Use a local cache directory and shallow clones.
Prevent corrupted caches from breaking CI runs.

Rules:
- Always clone with --depth=1
- Always checkout pinned SHA
- If cache exists but HEAD/sha mismatches, wipe and re-clone
- If git fsck fails, wipe and re-clone

Sketch:

```python
REPO_CACHE = Path.home() / ".codeplane-test-cache"

def ensure_repo_cached(repo_url: str, sha: str) -> Path:
    cache_path = REPO_CACHE / repo_slug(repo_url)
    if cache_path.exists():
        if not is_git_repo_healthy(cache_path):
            shutil.rmtree(cache_path)
        else:
            current = git_head(cache_path)
            if current != sha:
                git_fetch_shallow(cache_path, sha)
                git_checkout(cache_path, sha)
    if not cache_path.exists():
        git_clone_depth1(repo_url, cache_path)
        git_checkout(cache_path, sha)
    return cache_path
```

### 2. Materialize to tmp for mutation tests

Copy from cache to tmp_path to avoid modifying cache:
- shutil.copytree (fast enough for depth=1 repos)
- or use git worktree if you want faster isolation (optional)

### 3. Standard pytest markers

Add markers:
- e2e: real-world repos
- slow: long-running
- nightly: huge repos / budgets relaxed

### 4. CI strategy

- Regular PR CI: run Tier 1 only (fast, deterministic)
- Nightly / workflow_dispatch: run Tier 2 + Tier 3
- Never run the largest polyglot repos on every PR

---

## CI/CD Integration (Suggested)

- PR workflow:
  - run e2e on Tier 1 only
  - enforce budgets (tight)

- Nightly:
  - run Tier 2 + Tier 3
  - enforce budgets (slightly looser)

---

## Notes / Non-Goals

- These E2E tests do NOT validate cross-language linkage or semantic resolution.
- Polyglot repos are tested for correct context discovery, routing, and multi-language indexing only.
- Avoid flaky “count-based” assertions; prefer anchor symbols and stable expectations.
- Avoid pytest-benchmark in CI unless you have stable runners; prefer explicit time budgets.
