# Testing Subsystem

CodePlane's testing subsystem provides unified test discovery, execution, and result parsing across multiple languages and frameworks.

## Overview

The testing subsystem is built around **Runner Packs** - first-class plugins that define how to detect, discover, run, and parse tests for specific language/framework combinations.

### Key Concepts

- **Runner Pack**: A plugin that handles a specific test framework (e.g., `python.pytest`, `js.jest`)
- **Test Target**: A discoverable unit of tests with a specific `kind` (file, package, or project)
- **Workspace Root**: The directory context where tests are executed (supports monorepos)

## Supported Languages

### Tier 1 (Full Support)

| Language | Runner Pack | Target Kind | Output Format | Notes |
|----------|-------------|-------------|---------------|-------|
| Python | `python.pytest` | file | JUnit XML | Via `--junitxml` |
| JavaScript | `js.jest` | file | JSON | Via `--json --outputFile` |
| JavaScript | `js.vitest` | file | JUnit XML | Via `--reporter=junit` |
| Go | `go.gotest` | package | NDJSON | Via `-json` flag |
| Rust | `rust.nextest` | package | JUnit XML | Preferred over cargo test |
| Rust | `rust.cargo_test` | package | Coarse | Limited output format |
| Java | `java.maven` | project | JUnit XML | From `target/surefire-reports/` |
| Java | `java.gradle` | project | JUnit XML | From `build/test-results/` |
| C# | `csharp.dotnet` | project | JUnit XML | Via JunitXml.TestLogger |
| C/C++ | `cpp.ctest` | project | Coarse | Limited output format |
| Ruby | `ruby.rspec` | file | JUnit XML | Via RspecJunitFormatter |
| PHP | `php.phpunit` | file | JUnit XML | Via `--log-junit` |

### Tier 2 (Standard Support)

| Language | Runner Pack | Target Kind | Output Format | Notes |
|----------|-------------|-------------|---------------|-------|
| Kotlin | `kotlin.gradle` | project | JUnit XML | Uses Gradle test task |
| Swift | `swift.swiftpm` | package | Coarse | Limited output format |
| Scala | `scala.sbt` | project | JUnit XML | Via junit reporter |
| Dart | `dart.dart_test` | file | JSON | Via `--reporter json` |
| Dart | `dart.flutter_test` | file | JSON | Via `--machine` flag |
| Bash | `bash.bats` | file | JUnit XML | Via `--formatter junit` |
| PowerShell | `powershell.pester` | file | JUnit XML | Via Pester config |
| Lua | `lua.busted` | file | JUnit XML | Via `-o junit` |

## Target Kinds

Test targets have a `kind` that determines how they map to CLI arguments:

- **file**: A single test file. The selector is a relative file path.
- **package**: A module/package (Go packages, Rust crates). The selector is a package identifier.
- **project**: A project root (Maven module, Gradle project, .NET solution). The selector is typically `.` or a subproject path.

## Detection

Runner packs are detected automatically based on marker files:

```
pytest.ini, conftest.py, pyproject.toml[tool.pytest] → python.pytest
jest.config.js, package.json[jest] → js.jest
vitest.config.ts → js.vitest
go.mod → go.gotest
Cargo.toml → rust.nextest / rust.cargo_test
pom.xml → java.maven
build.gradle → java.gradle
*.csproj, *.sln → csharp.dotnet
CMakeLists.txt[enable_testing] → cpp.ctest
.rspec, spec/spec_helper.rb → ruby.rspec
phpunit.xml → php.phpunit
```

## Configuration Overrides

You can override detected runners in `.codeplane/config.yaml`:

```yaml
tests:
  runners:
    python: pytest
    javascript: vitest  # Override jest detection
    rust: cargo_test    # Use cargo test instead of nextest
```

## Monorepo Support

The testing subsystem supports monorepos by detecting nested workspaces:

- **JavaScript**: Detects `packages/*/package.json`, pnpm workspaces, nx/turborepo
- **Java**: Detects multi-module Maven/Gradle projects
- **.NET**: Detects solutions and project files

Each discovered target includes a `workspace_root` field indicating where to run the tests.

## Output Artifacts

Test results are written to `.codeplane/artifacts/tests/<run_id>/`:

```
.codeplane/artifacts/tests/abc12345/
├── test_tests_test_example.py.xml    # JUnit XML output
├── test_tests_test_example.py.stdout.txt  # Raw stdout
└── ...
```

## MCP Tools

### test_discover

Discover test targets in the repository.

**Parameters:**
- `paths` (optional): Scope discovery to specific paths

**Returns:**
```json
{
  "action": "discover",
  "targets": [
    {
      "target_id": "test:tests/test_example.py",
      "selector": "tests/test_example.py",
      "kind": "file",
      "language": "python",
      "runner_pack_id": "python.pytest",
      "workspace_root": "/path/to/repo"
    }
  ]
}
```

### test_run

Run tests.

**Parameters:**
- `targets` (optional): Specific target IDs to run
- `pattern` (optional): Test name pattern filter
- `tags` (optional): Test tags/markers filter
- `parallelism` (optional): Number of parallel workers (default: 4)
- `timeout_sec` (optional): Per-target timeout (default: 300)
- `fail_fast` (optional): Stop on first failure

**Returns:**
```json
{
  "action": "run",
  "run_status": {
    "run_id": "abc12345",
    "status": "running",
    "progress": {
      "targets": { "total": 10, "completed": 3, "running": 2, "failed": 0 },
      "cases": { "total": 50, "passed": 30, "failed": 0, "skipped": 2, "errors": 0 }
    },
    "artifact_dir": ".codeplane/artifacts/tests/abc12345"
  }
}
```

### test_status

Get status of a running test.

**Parameters:**
- `run_id`: Run ID from test_run

### test_cancel

Cancel a running test.

**Parameters:**
- `run_id`: Run ID to cancel

## Output Format Fidelity

### Full Fidelity (JUnit XML)

Most runners produce JUnit XML which provides:
- Individual test names and classnames
- Pass/fail/skip/error status per test
- Duration per test
- Failure messages and stack traces
- stdout/stderr capture

### Reduced Fidelity (Coarse Mode)

Some runners (`rust.cargo_test`, `swift.swiftpm`, `cpp.ctest`) cannot produce machine-readable output. In coarse mode:
- Only aggregate pass/fail counts are available
- Individual test details are not captured
- Failure messages may be incomplete

To get full fidelity for Rust, use `cargo-nextest` instead of `cargo test`.

## Extending with New Runner Packs

To add a new runner pack:

1. Create a class extending `RunnerPack`
2. Define `pack_id`, `language`, `markers`, `output_strategy`, `capabilities`
3. Implement `detect()`, `discover()`, `build_command()`, `parse_output()`
4. Register with `@runner_registry.register`

Example:

```python
@runner_registry.register
class MyRunnerPack(RunnerPack):
    pack_id = "lang.myrunner"
    language = "lang"
    runner_name = "myrunner"
    markers = [MarkerRule("myrunner.config", confidence="high")]
    output_strategy = OutputStrategy(format="junit_xml", file_based=True)
    capabilities = RunnerCapabilities(supported_kinds=["file"])

    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / "myrunner.config").exists():
            return 1.0
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        # Return list of TestTarget objects
        ...

    def build_command(self, target, *, output_path, pattern, tags) -> list[str]:
        return ["myrunner", "test", target.selector, f"--output={output_path}"]

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:
        return parse_junit_xml(output_path.read_text())
```
