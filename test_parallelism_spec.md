# CodePlane Test Execution Parallelism: Cross-Language Spec

## Goal
Enable fast, deterministic test execution across large suites by parallelizing at the test **target** level (e.g., files, packages, classes), with full local runner isolation. Must support any language CodePlane indexes (i.e., any language with a supported parser and test runner), including but not limited to Python, Go, JS/TS, Java, and .NET.

## Definitions
- **Test Target**: Smallest runnable unit CodePlane manages. Example: a single `test_*.py` file or a Go package.
- **Worker**: A CodePlane-managed subprocess that executes one or more test targets.
- **Batch**: A set of targets assigned to a worker.
- **Estimated Cost**: A scalar weight used to balance batches (default = 1).

## Target Model
Each discovered target includes:
```json
{
  "target_id": "tests/test_utils.py",
  "lang": "python",
  "kind": "unit",
  "cmd": ["pytest", "tests/test_utils.py"],
  "cwd": "repo_root",
  "estimated_cost": 1.2
}
```

## Execution Strategy
1. **Discover Targets**
   - Use per-language logic (see below).
   - Assign a stable `target_id` and default `estimated_cost`.

2. **Greedy Bin Packing**
   - Compute total estimated cost across all targets.
   - Greedily assign targets to `N` workers using cost-balanced bin packing.

3. **Parallel Execution**
   - Spawn `N` subprocesses.
   - Each subprocess runs its batch sequentially.
   - Apply per-target and global timeouts.

4. **Merge Results**
   - Parse outputs into structured schema.
   - Classify failures, detect retries, label flaky outcomes.

## Language-Specific Targeting Rules
Target rules depend on the language and available test runner. CodePlane supports any language with a:
- Recognized parser (Tree-sitter or LSP-backed)
- Declarative way to discover test files or commands
- CLI-compatible runner that can execute individual test units (e.g. files, packages, classes)

| Language       | Target Granularity      | Target ID Example              | Cmd Template                          |
|----------------|--------------------------|--------------------------------|---------------------------------------|
| Python         | File (`test_*.py`)       | `tests/test_utils.py`          | `pytest {path}`                       |
| Go             | Package (`./pkg/foo`)    | `pkg/foo`                      | `go test -json ./pkg/foo`            |
| JS/TS          | File (`*.test.ts`)       | `src/__tests__/foo.test.ts`    | `jest {path}`                        |
| Java           | Class or module          | `com.example.FooTest`          | `mvn -Dtest=FooTest test`            |
| .NET           | Project or class         | `MyProject.Tests.csproj`       | `dotnet test {path}`                 |
| Rust, Ruby, etc. | File/Module/Project     | Language-dependent             | Custom adapter logic per language    |

## Defaults
- `N = min(#vCPUs, 8)`
- Target cost = 1 if unknown
- Fail-fast: stop if first failure batch completes (configurable)
- Timeout: 30s per target (configurable)

## Optional Enhancements
- Historical cost recording per target (rolling median)
- Resource class labels (`unit`, `integration`, etc.)
- Test suite fingerprints for delta debugging

## Out of Scope
- Per-test-case parallelism
- CI sharding or remote execution
- MCP execution interface (handled separately)

## Summary
Use a universal, cost-aware, process-level parallel executor over language-specific test targets. Language-specific logic handles target discovery and runner invocation, but all parallelism and merging is managed centrally and deterministically by CodePlane.

