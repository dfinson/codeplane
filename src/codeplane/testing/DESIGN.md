# Testing Module — Design Spec

## Scope

The testing module discovers test targets and executes them in parallel. Supports multiple runners per repo.

### Responsibilities

- Test runner detection (marker files, config, language defaults)
- Test target discovery (files, packages, classes)
- Parallel execution with cost-balanced batching
- Result parsing and aggregation
- Failure fingerprinting for convergence detection
- Timeout and fail-fast handling

### From SPEC.md

- §11: Tests: planning, parallelism, execution
- §11.5: Test runner discovery
- §11.6: Language-specific targeting rules

---

## Design Options

### Option A: Functional pipeline

```python
def discover_targets(repo: Repo, config: TestConfig) -> list[TestTarget]:
    ...

def execute_targets(targets: list[TestTarget], parallelism: int) -> TestResults:
    ...

def parse_output(target: TestTarget, stdout: str, stderr: str, exit_code: int) -> TargetResult:
    ...
```

**Pros:** Simple, composable
**Cons:** No shared state for cost tracking

### Option B: TestRunner class

```python
class TestRunner:
    def __init__(self, repo: Repo, config: TestConfig):
        self.repo = repo
        self.config = config
        self.cost_history = CostHistory()
    
    async def discover(self, paths: list[Path] | None = None) -> list[TestTarget]: ...
    async def run(self, targets: list[TestTarget], options: RunOptions) -> TestResults: ...
    async def cancel(self, run_id: str) -> None: ...
```

**Pros:** Stateful (cost history), cleaner interface
**Cons:** More complex

### Option C: Actor per runner type

```python
class RunnerActor:
    runner_type: str  # pytest, jest, go, etc.
    
    async def discover(self, paths) -> list[TestTarget]: ...
    async def execute(self, target: TestTarget) -> TargetResult: ...

class TestCoordinator:
    runners: dict[str, RunnerActor]
    
    async def run_all(self, targets, parallelism) -> TestResults:
        # Dispatch to appropriate runner actors
```

**Pros:** Clean separation per runner
**Cons:** More moving parts

---

## Recommended Approach

**Option B (TestRunner class)** — single coordinator, discovers targets across all runners, executes with shared parallelism pool, tracks cost history.

---

## File Plan

```
testing/
├── __init__.py
├── runner.py        # TestRunner: discover, run, cancel
├── discovery.py     # Runner detection, target discovery per language
├── targets.py       # TestTarget model, cost estimation
├── executor.py      # Parallel subprocess execution, batching
├── parsers/         # Output parsers per runner
│   ├── __init__.py
│   ├── pytest.py
│   ├── jest.py
│   ├── go.py
│   └── generic.py   # Fallback (exit code only)
└── fingerprint.py   # Failure fingerprinting for convergence
```

## Dependencies

- Standard library `subprocess`, `asyncio`
- No runner-specific libraries (runners are external processes)

## Key Interfaces

```python
# runner.py
class TestRunner:
    async def discover(
        self,
        paths: list[Path] | None = None,
        filter: TestFilter | None = None
    ) -> list[TestTarget]: ...
    
    async def run(
        self,
        targets: list[TestTarget] | None = None,  # None = all discovered
        options: RunOptions | None = None
    ) -> TestRun: ...
    
    async def get_status(self, run_id: str) -> TestRunStatus: ...
    async def cancel(self, run_id: str) -> None: ...

# Types
@dataclass
class TestTarget:
    target_id: str           # Unique identifier
    path: Path               # File or package path
    language: str
    runner: str              # pytest, jest, go, etc.
    kind: str                # unit, integration, e2e
    cmd: list[str]           # Command to execute
    cwd: Path
    estimated_cost: float    # For batching

@dataclass
class RunOptions:
    parallelism: int = 0     # 0 = auto
    timeout_sec: int = 30
    fail_fast: bool = False
    filter: TestFilter | None = None

@dataclass
class TestRun:
    run_id: str
    status: Literal["running", "completed", "cancelled", "failed"]
    progress: Progress
    results: list[TargetResult]
    summary: TestSummary

@dataclass
class TargetResult:
    target_id: str
    status: Literal["passed", "failed", "skipped", "error", "timeout"]
    duration_ms: int
    failure: FailureInfo | None
    fingerprint: str | None  # For convergence detection
```

## Runner Detection (from SPEC.md §11.5)

Resolution order:
1. Explicit config (`.codeplane/config.yaml`)
2. Marker file detection
3. Language defaults

Marker files:
- `pytest.ini`, `pyproject.toml[tool.pytest]` → pytest
- `jest.config.*`, `package.json[jest]` → jest
- `vitest.config.*` → vitest
- `go.mod` → go test
- `Cargo.toml` → cargo test

## Open Questions

1. Streaming results during long test runs?
   - **Recommendation:** Use `codeplane_test_stream` SSE, emit per-target results
2. Cost history persistence?
   - **Recommendation:** Store in ledger SQLite, rolling median per target
3. Flaky detection?
   - **Recommendation:** Track consecutive flip-flops per target, flag as flaky after N
