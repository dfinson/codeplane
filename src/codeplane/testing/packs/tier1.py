"""Runner packs for Tier-1 languages.

This module registers runner packs for:
- Python (pytest)
- JavaScript/TypeScript (jest, vitest)
- Go (go test)
- Rust (cargo-nextest, cargo test)
- Java (Maven Surefire, Gradle)
- C# (.NET test)
- C/C++ (CTest)
- Ruby (RSpec)
- PHP (PHPUnit)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from codeplane.index._internal.ignore import PRUNABLE_DIRS
from codeplane.testing.models import ParsedTestCase, ParsedTestSuite, TestTarget
from codeplane.testing.runner_pack import (
    MarkerRule,
    OutputStrategy,
    RunnerCapabilities,
    RunnerPack,
    runner_registry,
)

if TYPE_CHECKING:
    from codeplane.testing.runtime import RuntimeExecutionContext


def _is_prunable_path(
    path: Path,
    workspace_root: Path,
    *,
    allowed_dirs: frozenset[str] | None = None,
) -> bool:
    """Check if path contains any prunable directory components.

    Args:
        path: Path to check
        workspace_root: Root directory for relative path calculation
        allowed_dirs: Optional set of directories that should be allowed
            even if they appear in PRUNABLE_DIRS (e.g., 'pkg' for Go)
    """
    try:
        rel = path.relative_to(workspace_root)
        for part in rel.parts:
            if part in PRUNABLE_DIRS:
                if allowed_dirs and part in allowed_dirs:
                    continue
                return True
        return False
    except ValueError:
        return True


# =============================================================================
# Python - pytest
# =============================================================================


@runner_registry.register
class PytestPack(RunnerPack):
    """Python pytest runner."""

    pack_id = "python.pytest"
    language = "python"
    runner_name = "pytest"
    markers = [
        MarkerRule("pytest.ini", confidence="high"),
        MarkerRule("conftest.py", confidence="medium"),
        MarkerRule("pyproject.toml", content_match="[tool.pytest", confidence="high"),
        MarkerRule("setup.cfg", content_match="[tool:pytest]", confidence="medium"),
    ]
    output_strategy = OutputStrategy(format="junit_xml", file_based=True, file_pattern="junit.xml")
    capabilities = RunnerCapabilities(
        supported_kinds=["file", "package"],
        supports_pattern_filter=True,
        supports_tag_filter=True,
        supports_parallel=True,
        supports_junit_output=True,
    )

    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / "pytest.ini").exists():
            return 1.0
        if (workspace_root / "conftest.py").exists():
            return 0.8
        pyproject = workspace_root / "pyproject.toml"
        if pyproject.exists():
            try:
                if "[tool.pytest" in pyproject.read_text():
                    return 1.0
            except Exception:
                pass
        setup_cfg = workspace_root / "setup.cfg"
        if setup_cfg.exists():
            try:
                if "[tool:pytest]" in setup_cfg.read_text():
                    return 0.9
            except Exception:
                pass
        # Check for any test files
        if list(workspace_root.glob("**/test_*.py")) or list(workspace_root.glob("**/*_test.py")):
            return 0.5
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        patterns = ["**/test_*.py", "**/*_test.py"]
        seen: set[str] = set()

        for pattern in patterns:
            for path in workspace_root.glob(pattern):
                # Skip prunable directories (.venv, __pycache__, etc.)
                if _is_prunable_path(path, workspace_root):
                    continue
                if path.name.startswith("_") or path.name == "conftest.py":
                    continue
                rel = str(path.relative_to(workspace_root))
                if rel in seen:
                    continue
                seen.add(rel)
                targets.append(
                    TestTarget(
                        target_id=f"test:{rel}",
                        selector=rel,
                        kind="file",
                        language="python",
                        runner_pack_id=self.pack_id,
                        workspace_root=str(workspace_root),
                    )
                )
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        # Use execution context if available, otherwise fall back to bare pytest
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                # Use configured executable (e.g., /repo/.venv/bin/python -m pytest)
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                # ExecutionContext exists but pytest not available via it
                cmd = ["pytest"]
        else:
            # No execution context, use bare pytest (legacy behavior)
            cmd = ["pytest"]

        cmd.extend([target.selector, f"--junitxml={output_path}", "--tb=short", "-q"])
        if pattern:
            cmd.extend(["-k", pattern])
        if tags:
            cmd.extend(["-m", " or ".join(tags)])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from codeplane.testing.parsers import parse_junit_xml

        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="pytest", errors=1)


# =============================================================================
# JavaScript/TypeScript - Jest
# =============================================================================


@runner_registry.register
class JestPack(RunnerPack):
    """JavaScript/TypeScript Jest runner."""

    pack_id = "js.jest"
    language = "javascript"
    runner_name = "jest"
    markers = [
        MarkerRule("jest.config.js", confidence="high"),
        MarkerRule("jest.config.ts", confidence="high"),
        MarkerRule("jest.config.json", confidence="high"),
        MarkerRule("jest.config.mjs", confidence="high"),
        MarkerRule("package.json", content_match='"jest"', confidence="medium"),
    ]
    output_strategy = OutputStrategy(format="json", file_based=True, file_pattern="results.json")
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=True,  # Via jest-junit reporter
    )

    def detect(self, workspace_root: Path) -> float:
        for config in ["jest.config.js", "jest.config.ts", "jest.config.json", "jest.config.mjs"]:
            if (workspace_root / config).exists():
                return 1.0
        pkg = workspace_root / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
                if "jest" in data:
                    return 0.9
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                if "jest" in deps:
                    return 0.8
            except Exception:
                pass
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        patterns = [
            "**/*.test.js",
            "**/*.test.ts",
            "**/*.test.jsx",
            "**/*.test.tsx",
            "**/*.spec.js",
            "**/*.spec.ts",
        ]
        seen: set[str] = set()

        for pattern in patterns:
            for path in workspace_root.glob(pattern):
                if _is_prunable_path(path, workspace_root):
                    continue
                rel = str(path.relative_to(workspace_root))
                if rel in seen:
                    continue
                seen.add(rel)
                lang = "typescript" if path.suffix in (".ts", ".tsx") else "javascript"
                targets.append(
                    TestTarget(
                        target_id=f"test:{rel}",
                        selector=rel,
                        kind="file",
                        language=lang,
                        runner_pack_id=self.pack_id,
                        workspace_root=str(workspace_root),
                    )
                )
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,  # noqa: ARG002
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                pm = self._detect_package_manager(Path(target.workspace_root))
                cmd = [pm, "jest"] if pm == "npx" else [pm, "run", "jest", "--"]
        else:
            pm = self._detect_package_manager(Path(target.workspace_root))
            cmd = [pm, "jest"] if pm == "npx" else [pm, "run", "jest", "--"]
        cmd.extend([target.selector, "--json", f"--outputFile={output_path}"])
        if pattern:
            cmd.extend(["--testNamePattern", pattern])
        return cmd

    def _detect_package_manager(self, root: Path) -> str:
        if (root / "pnpm-lock.yaml").exists():
            return "pnpm"
        if (root / "yarn.lock").exists():
            return "yarn"
        if (root / "bun.lockb").exists():
            return "bun"
        return "npx"

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        if output_path.exists():
            return self._parse_jest_json(output_path.read_text())
        return ParsedTestSuite(name="jest", errors=1)

    def _parse_jest_json(self, content: str) -> ParsedTestSuite:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return ParsedTestSuite(name="jest", errors=1)

        tests: list[ParsedTestCase] = []
        for result in data.get("testResults", []):
            for assertion in result.get("assertionResults", []):
                status_map: dict[str, Literal["passed", "failed", "skipped", "error"]] = {
                    "passed": "passed",
                    "failed": "failed",
                    "pending": "skipped",
                    "skipped": "skipped",
                }
                status = status_map.get(assertion.get("status", ""), "error")
                tests.append(
                    ParsedTestCase(
                        name=assertion.get("title", "unknown"),
                        classname=" > ".join(assertion.get("ancestorTitles", [])),
                        status=status,
                        duration_seconds=assertion.get("duration", 0) / 1000,
                        message="\n".join(assertion.get("failureMessages", [])) or None,
                    )
                )

        return ParsedTestSuite(
            name="jest",
            tests=tests,
            total=len(tests),
            passed=sum(1 for t in tests if t.status == "passed"),
            failed=sum(1 for t in tests if t.status == "failed"),
            skipped=sum(1 for t in tests if t.status == "skipped"),
            errors=sum(1 for t in tests if t.status == "error"),
            duration_seconds=data.get("testResults", [{}])[0].get("endTime", 0) / 1000
            if data.get("testResults")
            else 0,
        )


# =============================================================================
# JavaScript/TypeScript - Vitest
# =============================================================================


@runner_registry.register
class VitestPack(RunnerPack):
    """JavaScript/TypeScript Vitest runner."""

    pack_id = "js.vitest"
    language = "javascript"
    runner_name = "vitest"
    markers = [
        MarkerRule("vitest.config.js", confidence="high"),
        MarkerRule("vitest.config.ts", confidence="high"),
        MarkerRule("vite.config.js", content_match="vitest", confidence="medium"),
        MarkerRule("vite.config.ts", content_match="vitest", confidence="medium"),
    ]
    output_strategy = OutputStrategy(format="junit_xml", file_based=True, file_pattern="junit.xml")
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=True,
    )

    def detect(self, workspace_root: Path) -> float:
        for config in ["vitest.config.js", "vitest.config.ts"]:
            if (workspace_root / config).exists():
                return 1.0
        for vite in ["vite.config.js", "vite.config.ts"]:
            if (workspace_root / vite).exists():
                try:
                    if "vitest" in (workspace_root / vite).read_text():
                        return 0.9
                except Exception:
                    pass
        pkg = workspace_root / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                if "vitest" in deps:
                    return 0.7
            except Exception:
                pass
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        # Same patterns as Jest
        targets: list[TestTarget] = []
        patterns = ["**/*.test.js", "**/*.test.ts", "**/*.spec.js", "**/*.spec.ts"]
        seen: set[str] = set()

        for pattern in patterns:
            for path in workspace_root.glob(pattern):
                if _is_prunable_path(path, workspace_root):
                    continue
                rel = str(path.relative_to(workspace_root))
                if rel in seen:
                    continue
                seen.add(rel)
                lang = "typescript" if path.suffix == ".ts" else "javascript"
                targets.append(
                    TestTarget(
                        target_id=f"test:{rel}",
                        selector=rel,
                        kind="file",
                        language=lang,
                        runner_pack_id=self.pack_id,
                        workspace_root=str(workspace_root),
                    )
                )
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,  # noqa: ARG002
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                cmd = ["npx", "vitest", "run"]
        else:
            cmd = ["npx", "vitest", "run"]
        cmd.extend(
            [
                target.selector,
                "--reporter=junit",
                f"--outputFile={output_path}",
            ]
        )
        if pattern:
            cmd.extend(["--testNamePattern", pattern])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from codeplane.testing.parsers import parse_junit_xml

        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="vitest", errors=1)


# =============================================================================
# Go - go test
# =============================================================================


@runner_registry.register
class GoTestPack(RunnerPack):
    """Go test runner."""

    pack_id = "go.gotest"
    language = "go"
    runner_name = "go test"
    markers = [
        MarkerRule("go.mod", confidence="high"),
        MarkerRule("go.sum", confidence="high"),
    ]
    output_strategy = OutputStrategy(
        format="ndjson", file_based=False
    )  # go test -json writes to stdout
    capabilities = RunnerCapabilities(
        supported_kinds=["package"],  # Go tests run at package level
        supports_pattern_filter=True,
        supports_tag_filter=True,  # Build tags
        supports_parallel=True,
        supports_junit_output=False,  # Requires external tool like gotestsum
    )

    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / "go.mod").exists():
            return 1.0
        if (workspace_root / "go.sum").exists():
            return 0.9
        if list(workspace_root.glob("**/*.go")):
            return 0.3
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        seen_packages: set[str] = set()
        # Go standard directories that may be in PRUNABLE_DIRS (e.g., 'pkg' for Ruby)
        go_allowed = frozenset({"pkg", "internal", "cmd"})

        for path in workspace_root.glob("**/*_test.go"):
            if _is_prunable_path(path, workspace_root, allowed_dirs=go_allowed):
                continue
            pkg_dir = path.parent
            rel_pkg = str(pkg_dir.relative_to(workspace_root))
            if rel_pkg in seen_packages:
                continue
            seen_packages.add(rel_pkg)

            selector = f"./{rel_pkg}" if rel_pkg != "." else "./..."
            targets.append(
                TestTarget(
                    target_id=f"test:{selector}",
                    selector=selector,
                    kind="package",
                    language="go",
                    runner_pack_id=self.pack_id,
                    workspace_root=str(workspace_root),
                )
            )
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,  # noqa: ARG002
        pattern: str | None = None,
        tags: list[str] | None = None,
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                cmd = ["go", "test", "-json"]
        else:
            cmd = ["go", "test", "-json"]
        cmd.append(target.selector)
        if pattern:
            cmd.extend(["-run", pattern])
        if tags:
            cmd.extend(["-tags", ",".join(tags)])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from codeplane.testing.parsers import parse_go_test_json

        return parse_go_test_json(stdout)


# =============================================================================
# Rust - cargo-nextest (preferred) or cargo test
# =============================================================================


@runner_registry.register
class CargoNextestPack(RunnerPack):
    """Rust cargo-nextest runner (preferred for JUnit output)."""

    pack_id = "rust.nextest"
    language = "rust"
    runner_name = "cargo-nextest"
    markers = [
        MarkerRule("Cargo.toml", confidence="high"),
        MarkerRule(".config/nextest.toml", confidence="high"),
    ]
    output_strategy = OutputStrategy(format="junit_xml", file_based=True, file_pattern="junit.xml")
    capabilities = RunnerCapabilities(
        supported_kinds=["package"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=True,
    )

    def detect(self, workspace_root: Path) -> float:
        if not (workspace_root / "Cargo.toml").exists():
            return 0.0
        # Prefer nextest if available
        if shutil.which("cargo-nextest"):
            return 0.95
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        # Find workspace members or single package
        cargo_toml = workspace_root / "Cargo.toml"
        if not cargo_toml.exists():
            return targets

        try:
            content = cargo_toml.read_text()
            if "[workspace]" in content:
                # Workspace - find all members
                for member_toml in workspace_root.glob("*/Cargo.toml"):
                    member_dir = member_toml.parent
                    if list(member_dir.glob("**/*_test.rs")) or (member_dir / "tests").exists():
                        rel = str(member_dir.relative_to(workspace_root))
                        targets.append(
                            TestTarget(
                                target_id=f"test:{rel}",
                                selector=rel,
                                kind="package",
                                language="rust",
                                runner_pack_id=self.pack_id,
                                workspace_root=str(workspace_root),
                            )
                        )
                # Root is also a package (has both [workspace] and [package])
                if "[package]" in content and (
                    list(workspace_root.glob("src/**/*_test.rs"))
                    or (workspace_root / "tests").exists()
                ):
                    targets.append(
                        TestTarget(
                            target_id="test:.",
                            selector=".",
                            kind="package",
                            language="rust",
                            runner_pack_id=self.pack_id,
                            workspace_root=str(workspace_root),
                        )
                    )
            else:
                # Single package
                targets.append(
                    TestTarget(
                        target_id="test:.",
                        selector=".",
                        kind="package",
                        language="rust",
                        runner_pack_id=self.pack_id,
                        workspace_root=str(workspace_root),
                    )
                )
        except Exception:
            pass
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,  # noqa: ARG002
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                cmd = ["cargo", "nextest", "run"]
        else:
            cmd = ["cargo", "nextest", "run"]
        cmd.extend(
            [
                "--message-format",
                "junit",
                f"--junit-path={output_path}",
            ]
        )
        if target.selector != ".":
            cmd.extend(["-p", target.selector])
        if pattern:
            cmd.extend(["--", pattern])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from codeplane.testing.parsers import parse_junit_xml

        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="cargo-nextest", errors=1)


@runner_registry.register
class CargoTestPack(RunnerPack):
    """Rust cargo test runner (fallback, limited output format)."""

    pack_id = "rust.cargo_test"
    language = "rust"
    runner_name = "cargo test"
    markers = [
        MarkerRule("Cargo.toml", confidence="high"),
    ]
    output_strategy = OutputStrategy(
        format="coarse", file_based=False
    )  # cargo test has no configurable output
    capabilities = RunnerCapabilities(
        supported_kinds=["package"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=False,  # This is why nextest is preferred
    )

    def detect(self, workspace_root: Path) -> float:
        if not (workspace_root / "Cargo.toml").exists():
            return 0.0
        # Lower priority than nextest
        if shutil.which("cargo-nextest"):
            return 0.5
        return 0.9

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        # Same as nextest
        nextest = CargoNextestPack()
        targets = await nextest.discover(workspace_root)
        # Update pack_id
        for t in targets:
            t.runner_pack_id = self.pack_id
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,  # noqa: ARG002
        pattern: str | None = None,
        tags: list[str] | None = None,  # noqa: ARG002
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                cmd = ["cargo", "test"]
        else:
            cmd = ["cargo", "test"]
        if target.selector != ".":
            cmd.extend(["-p", target.selector])
        if pattern:
            cmd.extend(["--", pattern])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        # Parse coarse output - just count pass/fail from stdout
        lines = stdout.split("\n")
        passed = 0
        failed = 0

        for line in lines:
            if "test result:" in line:
                # e.g., "test result: ok. 10 passed; 0 failed;"
                parts = line.split()
                for i, part in enumerate(parts):
                    if "passed" in part and i > 0 and parts[i - 1].isdigit():
                        passed = int(parts[i - 1])
                    if "failed" in part and i > 0 and parts[i - 1].isdigit():
                        failed = int(parts[i - 1])

        return ParsedTestSuite(
            name="cargo test",
            total=passed + failed,
            passed=passed,
            failed=failed,
        )


# =============================================================================
# Java - Maven Surefire
# =============================================================================


@runner_registry.register
class MavenSurefirePack(RunnerPack):
    """Java Maven Surefire runner."""

    pack_id = "java.maven"
    language = "java"
    runner_name = "mvn test"
    markers = [
        MarkerRule("pom.xml", confidence="high"),
        MarkerRule("mvnw", confidence="high"),
    ]
    output_strategy = OutputStrategy(
        format="junit_xml", file_based=True, file_pattern="target/surefire-reports/*.xml"
    )
    capabilities = RunnerCapabilities(
        supported_kinds=["project", "file"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=True,  # Native surefire output
    )

    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / "pom.xml").exists():
            return 1.0
        if (workspace_root / "mvnw").exists():
            return 0.95
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        # Check for multi-module project
        pom = workspace_root / "pom.xml"
        if not pom.exists():
            return targets

        try:
            content = pom.read_text()
            if "<modules>" in content:
                # Multi-module - find module poms
                for module_pom in workspace_root.glob("*/pom.xml"):
                    module_dir = module_pom.parent
                    if (module_dir / "src" / "test").exists():
                        rel = str(module_dir.relative_to(workspace_root))
                        targets.append(
                            TestTarget(
                                target_id=f"test:{rel}",
                                selector=rel,
                                kind="project",
                                language="java",
                                runner_pack_id=self.pack_id,
                                workspace_root=str(workspace_root),
                            )
                        )
            else:
                # Single project
                if (workspace_root / "src" / "test").exists():
                    targets.append(
                        TestTarget(
                            target_id="test:.",
                            selector=".",
                            kind="project",
                            language="java",
                            runner_pack_id=self.pack_id,
                            workspace_root=str(workspace_root),
                        )
                    )
        except Exception:
            pass
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,  # noqa: ARG002 - Maven writes to target/surefire-reports
        pattern: str | None = None,
        tags: list[str] | None = None,  # noqa: ARG002
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                mvn = "./mvnw" if (Path(target.workspace_root) / "mvnw").exists() else "mvn"
                cmd = [mvn, "test"]
        else:
            mvn = "./mvnw" if (Path(target.workspace_root) / "mvnw").exists() else "mvn"
            cmd = [mvn, "test"]
        if target.selector != ".":
            cmd.extend(["-pl", target.selector])
        if pattern:
            cmd.extend(["-Dtest=" + pattern])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from codeplane.testing.parsers import parse_junit_xml

        # Surefire writes to target/surefire-reports/*.xml
        reports_dir = output_path.parent / "target" / "surefire-reports"
        if not reports_dir.exists():
            return ParsedTestSuite(name="maven", errors=1)

        all_tests: list[ParsedTestCase] = []
        total_duration = 0.0

        for xml_file in reports_dir.glob("TEST-*.xml"):
            suite = parse_junit_xml(xml_file.read_text())
            all_tests.extend(suite.tests)
            total_duration += suite.duration_seconds

        return ParsedTestSuite(
            name="maven",
            tests=all_tests,
            total=len(all_tests),
            passed=sum(1 for t in all_tests if t.status == "passed"),
            failed=sum(1 for t in all_tests if t.status == "failed"),
            skipped=sum(1 for t in all_tests if t.status == "skipped"),
            errors=sum(1 for t in all_tests if t.status == "error"),
            duration_seconds=total_duration,
        )


# =============================================================================
# Java - Gradle
# =============================================================================


@runner_registry.register
class GradlePack(RunnerPack):
    """Java Gradle runner."""

    pack_id = "java.gradle"
    language = "java"
    runner_name = "gradle test"
    markers = [
        MarkerRule("build.gradle", confidence="high"),
        MarkerRule("build.gradle.kts", confidence="high"),
        MarkerRule("gradlew", confidence="high"),
    ]
    output_strategy = OutputStrategy(
        format="junit_xml", file_based=True, file_pattern="build/test-results/test/*.xml"
    )
    capabilities = RunnerCapabilities(
        supported_kinds=["project", "file"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=True,
    )

    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / "build.gradle").exists():
            return 1.0
        if (workspace_root / "build.gradle.kts").exists():
            return 1.0
        if (workspace_root / "gradlew").exists():
            return 0.95
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        # Check for multi-project build
        settings = workspace_root / "settings.gradle"
        settings_kts = workspace_root / "settings.gradle.kts"

        subprojects: list[Path] = []
        for settings_file in [settings, settings_kts]:
            if settings_file.exists():
                try:
                    content = settings_file.read_text()
                    if "include" in content:
                        # Has subprojects - look for build files
                        for build in workspace_root.glob("*/build.gradle*"):
                            subprojects.append(build.parent)
                except Exception:
                    pass

        if subprojects:
            for project_dir in subprojects:
                if (project_dir / "src" / "test").exists():
                    rel = str(project_dir.relative_to(workspace_root))
                    targets.append(
                        TestTarget(
                            target_id=f"test:{rel}",
                            selector=rel,
                            kind="project",
                            language="java",
                            runner_pack_id=self.pack_id,
                            workspace_root=str(workspace_root),
                        )
                    )
        else:
            # Single project
            if (workspace_root / "src" / "test").exists():
                targets.append(
                    TestTarget(
                        target_id="test:.",
                        selector=".",
                        kind="project",
                        language="java",
                        runner_pack_id=self.pack_id,
                        workspace_root=str(workspace_root),
                    )
                )
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,  # noqa: ARG002
        pattern: str | None = None,
        tags: list[str] | None = None,  # noqa: ARG002
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
                if target.selector != "." and "test" in tool_config.base_args:
                    # Replace 'test' with ':selector:test'
                    cmd = [tool_config.executable, f":{target.selector}:test"]
            else:
                gradle = (
                    "./gradlew" if (Path(target.workspace_root) / "gradlew").exists() else "gradle"
                )
                cmd = (
                    [gradle, "test"]
                    if target.selector == "."
                    else [gradle, f":{target.selector}:test"]
                )
        else:
            gradle = "./gradlew" if (Path(target.workspace_root) / "gradlew").exists() else "gradle"
            cmd = [gradle, "test"]
            if target.selector != ".":
                cmd = [gradle, f":{target.selector}:test"]
        if pattern:
            cmd.extend(["--tests", pattern])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from codeplane.testing.parsers import parse_junit_xml

        # Gradle writes to build/test-results/test/*.xml
        reports_dir = output_path.parent / "build" / "test-results" / "test"
        if not reports_dir.exists():
            return ParsedTestSuite(name="gradle", errors=1)

        all_tests: list[ParsedTestCase] = []
        total_duration = 0.0

        for xml_file in reports_dir.glob("TEST-*.xml"):
            suite = parse_junit_xml(xml_file.read_text())
            all_tests.extend(suite.tests)
            total_duration += suite.duration_seconds

        return ParsedTestSuite(
            name="gradle",
            tests=all_tests,
            total=len(all_tests),
            passed=sum(1 for t in all_tests if t.status == "passed"),
            failed=sum(1 for t in all_tests if t.status == "failed"),
            skipped=sum(1 for t in all_tests if t.status == "skipped"),
            errors=sum(1 for t in all_tests if t.status == "error"),
            duration_seconds=total_duration,
        )


# =============================================================================
# C# - dotnet test
# =============================================================================


@runner_registry.register
class DotnetTestPack(RunnerPack):
    """C# dotnet test runner."""

    pack_id = "csharp.dotnet"
    language = "csharp"
    runner_name = "dotnet test"
    markers = [
        MarkerRule("*.csproj", confidence="high"),
        MarkerRule("*.sln", confidence="high"),
        MarkerRule("global.json", confidence="medium"),
    ]
    output_strategy = OutputStrategy(
        format="junit_xml", file_based=True, file_pattern="TestResults/*.xml"
    )
    capabilities = RunnerCapabilities(
        supported_kinds=["project"],
        supports_pattern_filter=True,
        supports_tag_filter=True,  # Via --filter
        supports_parallel=True,
        supports_junit_output=True,  # Via JunitXml.TestLogger nuget
    )

    def detect(self, workspace_root: Path) -> float:
        if list(workspace_root.glob("*.sln")):
            return 1.0
        if list(workspace_root.glob("*.csproj")):
            return 0.9
        if (workspace_root / "global.json").exists():
            return 0.7
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        # Find test projects
        for csproj in workspace_root.glob("**/*.csproj"):
            if _is_prunable_path(csproj, workspace_root):
                continue
            try:
                content = csproj.read_text()
                # Check if it's a test project
                if (
                    "Microsoft.NET.Test.Sdk" in content
                    or "xunit" in content.lower()
                    or "nunit" in content.lower()
                ):
                    rel = str(csproj.relative_to(workspace_root))
                    targets.append(
                        TestTarget(
                            target_id=f"test:{rel}",
                            selector=rel,
                            kind="project",
                            language="csharp",
                            runner_pack_id=self.pack_id,
                            workspace_root=str(workspace_root),
                        )
                    )
            except Exception:
                pass
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                cmd = ["dotnet", "test"]
        else:
            cmd = ["dotnet", "test"]
        cmd.extend(
            [
                target.selector,
                f"--logger:junit;LogFilePath={output_path}",
                "--no-build",  # Assume already built
            ]
        )
        if pattern:
            cmd.extend(["--filter", f"FullyQualifiedName~{pattern}"])
        if tags:
            cmd.extend(["--filter", " | ".join(f"Category={t}" for t in tags)])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from codeplane.testing.parsers import parse_junit_xml

        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="dotnet", errors=1)


# =============================================================================
# C/C++ - CTest
# =============================================================================


@runner_registry.register
class CTestPack(RunnerPack):
    """C/C++ CTest runner."""

    pack_id = "cpp.ctest"
    language = "cpp"
    runner_name = "ctest"
    markers = [
        MarkerRule("CMakeLists.txt", content_match="enable_testing", confidence="high"),
        MarkerRule("CMakeLists.txt", confidence="medium"),
    ]
    output_strategy = OutputStrategy(format="coarse", file_based=False)  # CTest output is limited
    capabilities = RunnerCapabilities(
        supported_kinds=["project"],
        supports_pattern_filter=True,
        supports_tag_filter=True,  # Via -L (labels)
        supports_parallel=True,
        supports_junit_output=False,  # Limited support
    )

    def detect(self, workspace_root: Path) -> float:
        cmake = workspace_root / "CMakeLists.txt"
        if cmake.exists():
            try:
                content = cmake.read_text()
                if "enable_testing" in content or "add_test" in content:
                    return 1.0
                return 0.5
            except Exception:
                pass
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        cmake = workspace_root / "CMakeLists.txt"
        if cmake.exists():
            targets.append(
                TestTarget(
                    target_id="test:.",
                    selector=".",
                    kind="project",
                    language="cpp",
                    runner_pack_id=self.pack_id,
                    workspace_root=str(workspace_root),
                )
            )
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,  # noqa: ARG002
        pattern: str | None = None,
        tags: list[str] | None = None,
        exec_ctx: RuntimeExecutionContext | None = None,  # noqa: ARG002 - CTest doesn't use execution context
    ) -> list[str]:
        build_dir = Path(target.workspace_root) / "build"
        cmd = ["ctest", "--test-dir", str(build_dir), "--output-on-failure"]
        if pattern:
            cmd.extend(["-R", pattern])
        if tags:
            cmd.extend(["-L", "|".join(tags)])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        # Parse coarse CTest output
        lines = stdout.split("\n")
        passed = 0
        failed = 0

        for line in lines:
            if "tests passed" in line.lower():
                try:
                    # e.g., "100% tests passed, 0 tests failed out of 10"
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part == "passed,":
                            passed = int(parts[i - 1])
                        if part == "failed":
                            failed = int(parts[i - 1])
                except (ValueError, IndexError):
                    pass

        return ParsedTestSuite(
            name="ctest",
            total=passed + failed,
            passed=passed,
            failed=failed,
        )


# =============================================================================
# Ruby - RSpec
# =============================================================================


@runner_registry.register
class RSpecPack(RunnerPack):
    """Ruby RSpec runner."""

    pack_id = "ruby.rspec"
    language = "ruby"
    runner_name = "rspec"
    markers = [
        MarkerRule(".rspec", confidence="high"),
        MarkerRule("spec/spec_helper.rb", confidence="high"),
        MarkerRule("Gemfile", content_match="rspec", confidence="medium"),
    ]
    output_strategy = OutputStrategy(format="junit_xml", file_based=True, file_pattern="rspec.xml")
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=True,
        supports_parallel=True,
        supports_junit_output=True,  # Via rspec_junit_formatter
    )

    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / ".rspec").exists():
            return 1.0
        if (workspace_root / "spec" / "spec_helper.rb").exists():
            return 0.9
        gemfile = workspace_root / "Gemfile"
        if gemfile.exists():
            try:
                if "rspec" in gemfile.read_text():
                    return 0.8
            except Exception:
                pass
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        for path in workspace_root.glob("spec/**/*_spec.rb"):
            if _is_prunable_path(path, workspace_root):
                continue
            rel = str(path.relative_to(workspace_root))
            targets.append(
                TestTarget(
                    target_id=f"test:{rel}",
                    selector=rel,
                    kind="file",
                    language="ruby",
                    runner_pack_id=self.pack_id,
                    workspace_root=str(workspace_root),
                )
            )
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        # Use execution context if available
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                cmd = ["bundle", "exec", "rspec"]
        else:
            cmd = ["bundle", "exec", "rspec"]
        cmd.extend(
            [
                target.selector,
                "--format",
                "RspecJunitFormatter",
                "--out",
                str(output_path),
            ]
        )
        if pattern:
            cmd.extend(["--example", pattern])
        if tags:
            for tag in tags:
                cmd.extend(["--tag", tag])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from codeplane.testing.parsers import parse_junit_xml

        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="rspec", errors=1)


@runner_registry.register
class MinitestPack(RunnerPack):
    """Ruby Minitest runner."""

    pack_id = "ruby.minitest"
    language = "ruby"
    runner_name = "minitest"
    markers = [
        MarkerRule("Rakefile", content_match="Rake::TestTask", confidence="high"),
        MarkerRule("Gemfile", content_match="minitest", confidence="medium"),
        MarkerRule("test/test_helper.rb", confidence="high"),
    ]
    output_strategy = OutputStrategy(
        format="junit_xml", file_based=True, file_pattern="minitest.xml"
    )
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=False,
        supports_junit_output=True,  # Via minitest-reporters
    )

    def detect(self, workspace_root: Path) -> float:
        # High confidence: Rakefile with Rake::TestTask
        rakefile = workspace_root / "Rakefile"
        if rakefile.exists():
            try:
                content = rakefile.read_text()
                if "Rake::TestTask" in content:
                    return 0.9
            except Exception:
                pass
        # Medium confidence: test_helper.rb exists
        if (workspace_root / "test" / "test_helper.rb").exists():
            return 0.7
        # Medium: Gemfile mentions minitest
        gemfile = workspace_root / "Gemfile"
        if gemfile.exists():
            try:
                if "minitest" in gemfile.read_text():
                    return 0.6
            except Exception:
                pass
        # Low: test/ directory with *_test.rb or spec_*.rb files
        test_dir = workspace_root / "test"
        if test_dir.is_dir():
            for p in test_dir.iterdir():
                if p.suffix == ".rb" and (p.stem.endswith("_test") or p.stem.startswith("spec_")):
                    return 0.5
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        test_dir = workspace_root / "test"
        if not test_dir.is_dir():
            return targets

        for path in test_dir.rglob("*.rb"):
            if _is_prunable_path(path, workspace_root):
                continue
            stem = path.stem
            if stem.endswith("_test") or stem.startswith("spec_"):
                rel = str(path.relative_to(workspace_root))
                targets.append(
                    TestTarget(
                        target_id=f"test:{rel}",
                        selector=rel,
                        kind="file",
                        language="ruby",
                        runner_pack_id=self.pack_id,
                        workspace_root=str(workspace_root),
                    )
                )
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,  # noqa: ARG002
        pattern: str | None = None,
        tags: list[str] | None = None,  # noqa: ARG002
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        if exec_ctx:
            tool_config = exec_ctx.get_test_runner(self.pack_id)
            if tool_config and tool_config.available:
                cmd = [tool_config.executable] + list(tool_config.base_args)
            else:
                cmd = ["bundle", "exec", "ruby"]
        else:
            cmd = ["bundle", "exec", "ruby"]
        cmd.append(target.selector)
        if pattern:
            cmd.extend(["-n", pattern])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from codeplane.testing.parsers import parse_junit_xml

        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="minitest", errors=1)


# =============================================================================
# PHP - PHPUnit
# =============================================================================


@runner_registry.register
class PHPUnitPack(RunnerPack):
    """PHP PHPUnit runner."""

    pack_id = "php.phpunit"
    language = "php"
    runner_name = "phpunit"
    markers = [
        MarkerRule("phpunit.xml", confidence="high"),
        MarkerRule("phpunit.xml.dist", confidence="high"),
        MarkerRule("composer.json", content_match="phpunit", confidence="medium"),
    ]
    output_strategy = OutputStrategy(format="junit_xml", file_based=True, file_pattern="junit.xml")
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=True,  # Via --group
        supports_parallel=True,
        supports_junit_output=True,
    )

    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / "phpunit.xml").exists():
            return 1.0
        if (workspace_root / "phpunit.xml.dist").exists():
            return 0.95
        composer = workspace_root / "composer.json"
        if composer.exists():
            try:
                data = json.loads(composer.read_text())
                deps = {**data.get("require", {}), **data.get("require-dev", {})}
                if "phpunit/phpunit" in deps:
                    return 0.8
            except Exception:
                pass
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        for path in workspace_root.glob("tests/**/*Test.php"):
            if _is_prunable_path(path, workspace_root):
                continue
            rel = str(path.relative_to(workspace_root))
            targets.append(
                TestTarget(
                    target_id=f"test:{rel}",
                    selector=rel,
                    kind="file",
                    language="php",
                    runner_pack_id=self.pack_id,
                    workspace_root=str(workspace_root),
                )
            )
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,
        exec_ctx: RuntimeExecutionContext | None = None,  # noqa: ARG002 - PHPUnit doesn't use execution context currently
    ) -> list[str]:
        cmd = ["./vendor/bin/phpunit", target.selector, f"--log-junit={output_path}"]
        if pattern:
            cmd.extend(["--filter", pattern])
        if tags:
            cmd.extend(["--group", ",".join(tags)])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from codeplane.testing.parsers import parse_junit_xml

        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="phpunit", errors=1)
