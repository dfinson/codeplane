"""Comprehensive tests for RunnerPack base class and registry."""

from pathlib import Path
from tempfile import TemporaryDirectory

from codeplane.testing.models import ParsedTestSuite, TestTarget
from codeplane.testing.runner_pack import (
    MarkerRule,
    OutputStrategy,
    RunnerCapabilities,
    RunnerPack,
    RunnerPackRegistry,
    runner_registry,
)
from codeplane.testing.runtime import RuntimeExecutionContext

# =============================================================================
# MarkerRule
# =============================================================================


class TestMarkerRule:
    """Tests for MarkerRule dataclass."""

    def test_create_file_marker(self) -> None:
        rule = MarkerRule(pattern="pytest.ini", confidence="high")
        assert rule.pattern == "pytest.ini"
        assert rule.confidence == "high"
        assert rule.content_match is None

    def test_create_with_content_match(self) -> None:
        rule = MarkerRule(
            pattern="pyproject.toml", confidence="medium", content_match="[tool.pytest"
        )
        assert rule.content_match == "[tool.pytest"

    def test_confidence_levels(self) -> None:
        rule_high = MarkerRule(pattern="x", confidence="high")
        rule_medium = MarkerRule(pattern="x", confidence="medium")
        rule_low = MarkerRule(pattern="x", confidence="low")
        assert rule_high.confidence == "high"
        assert rule_medium.confidence == "medium"
        assert rule_low.confidence == "low"


# =============================================================================
# OutputStrategy
# =============================================================================


class TestOutputStrategy:
    """Tests for OutputStrategy dataclass."""

    def test_create_junit(self) -> None:
        strategy = OutputStrategy(format="junit_xml")
        assert strategy.format == "junit_xml"
        assert strategy.file_based is True

    def test_create_json_stdout(self) -> None:
        strategy = OutputStrategy(format="json", file_based=False)
        assert strategy.format == "json"
        assert strategy.file_based is False

    def test_with_file_pattern(self) -> None:
        strategy = OutputStrategy(format="junit_xml", file_pattern="TEST-*.xml")
        assert strategy.file_pattern == "TEST-*.xml"


# =============================================================================
# RunnerCapabilities
# =============================================================================


class TestRunnerCapabilities:
    """Tests for RunnerCapabilities dataclass."""

    def test_default_capabilities(self) -> None:
        caps = RunnerCapabilities()
        assert caps.supported_kinds == ["file"]
        assert caps.supports_pattern_filter is True
        assert caps.supports_tag_filter is False
        assert caps.supports_parallel is True

    def test_custom_capabilities(self) -> None:
        caps = RunnerCapabilities(
            supported_kinds=["file", "package"],
            supports_pattern_filter=True,
            supports_tag_filter=True,
            supports_parallel=False,
        )
        assert "package" in caps.supported_kinds
        assert caps.supports_tag_filter is True
        assert caps.supports_parallel is False


# =============================================================================
# RunnerPackRegistry
# =============================================================================


class TestRunnerPackRegistry:
    """Tests for RunnerPackRegistry."""

    def test_register_decorator(self) -> None:
        registry = RunnerPackRegistry()

        @registry.register
        class TestPack(RunnerPack):
            pack_id = "test.pack"
            language = "test"
            runner_name = "test"
            markers = [MarkerRule(pattern="test.marker", confidence="high")]
            output_strategy = OutputStrategy(format="coarse")
            capabilities = RunnerCapabilities()

            def detect(self, workspace_root: Path) -> float:  # noqa: ARG002
                return 0.0

            async def discover(self, workspace_root: Path) -> list[TestTarget]:  # noqa: ARG002
                return []

            def build_command(
                self,
                target: TestTarget,  # noqa: ARG002
                *,
                output_path: Path,  # noqa: ARG002
                pattern: str | None = None,  # noqa: ARG002
                tags: list[str] | None = None,  # noqa: ARG002
                exec_ctx: RuntimeExecutionContext | None = None,  # noqa: ARG002
            ) -> list[str]:
                return []

            def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
                return ParsedTestSuite(name="test", total=0, passed=0, failed=0)

        assert "test.pack" in registry._packs
        assert registry.get("test.pack") is not None

    def test_get_nonexistent(self) -> None:
        registry = RunnerPackRegistry()
        result = registry.get("nonexistent.pack")
        assert result is None

    def test_all_packs(self) -> None:
        # Use global registry which has all packs
        packs = runner_registry.all()

        # Should have Tier-1 and Tier-2 packs
        assert len(packs) >= 10

        # Check for specific known packs
        pack_ids = {p.pack_id for p in packs}
        assert "python.pytest" in pack_ids
        assert "js.jest" in pack_ids
        assert "go.gotest" in pack_ids

    def test_for_language(self) -> None:
        python_packs = runner_registry.for_language("python")
        assert len(python_packs) >= 1
        for p in python_packs:
            assert p.language == "python"

    def test_detect_all(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")

            matches = runner_registry.detect_all(root)

            assert len(matches) >= 1
            pack_classes = {p.pack_id for p, _ in matches}
            assert "python.pytest" in pack_classes

    def test_detect_all_returns_sorted_by_confidence(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")  # high confidence

            matches = runner_registry.detect_all(root)

            if len(matches) >= 2:
                # Should be sorted descending by confidence
                confidences = [conf for _, conf in matches]
                assert confidences == sorted(confidences, reverse=True)


# =============================================================================
# RunnerPack base class
# =============================================================================


class TestRunnerPackBase:
    """Tests for RunnerPack base class behavior."""

    def test_pack_has_required_attributes(self) -> None:
        """Pack classes should have required attributes."""
        pytest_pack_class = runner_registry.get("python.pytest")
        assert pytest_pack_class is not None

        assert hasattr(pytest_pack_class, "pack_id")
        assert hasattr(pytest_pack_class, "language")
        assert hasattr(pytest_pack_class, "runner_name")
        assert hasattr(pytest_pack_class, "markers")
        assert hasattr(pytest_pack_class, "output_strategy")
        assert hasattr(pytest_pack_class, "capabilities")

    def test_pack_instance_methods(self) -> None:
        """Pack instances should have required methods."""
        pytest_pack_class = runner_registry.get("python.pytest")
        assert pytest_pack_class is not None

        pack = pytest_pack_class()
        assert callable(getattr(pack, "detect", None))
        assert callable(getattr(pack, "discover", None))
        assert callable(getattr(pack, "build_command", None))
        assert callable(getattr(pack, "parse_output", None))

    def test_pack_id_format(self) -> None:
        """Pack IDs should follow language.runner format."""
        for pack_class in runner_registry.all():
            assert "." in pack_class.pack_id
            parts = pack_class.pack_id.split(".")
            assert len(parts) == 2
            assert len(parts[0]) > 0
            assert len(parts[1]) > 0

    def test_get_cwd_default(self) -> None:
        """Default get_cwd should return workspace root."""
        pytest_pack_class = runner_registry.get("python.pytest")
        assert pytest_pack_class is not None
        pack = pytest_pack_class()

        target = TestTarget(
            target_id="test:foo",
            selector="foo",
            kind="file",
            language="python",
            runner_pack_id="python.pytest",
            workspace_root="/my/repo",
        )

        cwd = pack.get_cwd(target)
        assert cwd == Path("/my/repo")


# =============================================================================
# Global registry tests
# =============================================================================


class TestGlobalRegistry:
    """Tests for the global runner_registry."""

    def test_tier1_packs_registered(self) -> None:
        """All Tier-1 packs should be registered."""
        expected = [
            "python.pytest",
            "js.jest",
            "js.vitest",
            "go.gotest",
            "rust.nextest",
            "rust.cargo_test",
            "java.maven",
            "java.gradle",
            "csharp.dotnet",
            "cpp.ctest",
            "ruby.rspec",
            "php.phpunit",
        ]
        for pack_id in expected:
            pack = runner_registry.get(pack_id)
            assert pack is not None, f"Missing Tier-1 pack: {pack_id}"

    def test_tier2_packs_registered(self) -> None:
        """All Tier-2 packs should be registered."""
        expected = [
            "kotlin.gradle",
            "swift.swiftpm",
            "scala.sbt",
            "dart.dart_test",
            "dart.flutter_test",
            "bash.bats",
            "powershell.pester",
            "lua.busted",
        ]
        for pack_id in expected:
            pack = runner_registry.get(pack_id)
            assert pack is not None, f"Missing Tier-2 pack: {pack_id}"

    def test_all_packs_have_markers(self) -> None:
        """Every pack should have at least one marker rule."""
        for pack_class in runner_registry.all():
            assert len(pack_class.markers) >= 1, f"Pack {pack_class.pack_id} has no markers"

    def test_all_packs_have_output_strategy(self) -> None:
        """Every pack should have an output strategy."""
        for pack_class in runner_registry.all():
            assert pack_class.output_strategy is not None
            assert pack_class.output_strategy.format in [
                "junit_xml",
                "json",
                "ndjson",
                "tap",
                "coarse",
            ]

    def test_all_packs_instantiable(self) -> None:
        """Every pack class should be instantiable."""
        for pack_class in runner_registry.all():
            pack = pack_class()
            assert pack.pack_id == pack_class.pack_id

    def test_all_packs_detect_method(self) -> None:
        """Every pack should have working detect method."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for pack_class in runner_registry.all():
                pack = pack_class()
                confidence = pack.detect(root)
                # Should return 0.0 for empty directory
                assert 0.0 <= confidence <= 1.0
                assert confidence == 0.0  # Empty dir = no detection
