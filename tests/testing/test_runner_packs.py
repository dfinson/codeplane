"""Tests for runner pack detection and registry."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from codeplane.testing.runner_pack import runner_registry


class TestRunnerRegistry:
    """Tests for runner pack registry."""

    def test_given_registry_when_all_then_returns_all_packs(self) -> None:
        packs = runner_registry.all()
        assert len(packs) >= 12  # At least tier-1 packs

    def test_given_registry_when_get_valid_id_then_returns_pack(self) -> None:
        pack = runner_registry.get("python.pytest")
        assert pack is not None
        assert pack.pack_id == "python.pytest"

    def test_given_registry_when_get_invalid_id_then_returns_none(self) -> None:
        pack = runner_registry.get("invalid.pack")
        assert pack is None

    def test_given_registry_when_for_language_then_returns_filtered(self) -> None:
        python_packs = runner_registry.for_language("python")
        assert len(python_packs) >= 1
        assert all(p.language == "python" for p in python_packs)

        js_packs = runner_registry.for_language("javascript")
        assert len(js_packs) >= 2  # jest, vitest
        assert all(p.language == "javascript" for p in js_packs)


class TestPackDetection:
    """Tests for runner pack detection."""

    def test_given_pytest_markers_when_detect_then_high_confidence(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("[pytest]\n")
            (root / "tests").mkdir()
            (root / "tests" / "test_example.py").write_text("def test_foo(): pass")

            results = runner_registry.detect_all(root)
            pack_ids = [p.pack_id for p, _ in results]

            assert "python.pytest" in pack_ids
            pytest_result = next((p, c) for p, c in results if p.pack_id == "python.pytest")
            assert pytest_result[1] == 1.0  # High confidence

    def test_given_package_json_with_jest_when_detect_then_finds_jest(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "package.json").write_text('{"jest": {}}')
            (root / "src").mkdir()
            (root / "src" / "example.test.js").write_text("test('foo', () => {})")

            results = runner_registry.detect_all(root)
            pack_ids = [p.pack_id for p, _ in results]

            assert "js.jest" in pack_ids

    def test_given_go_mod_when_detect_then_finds_go_test(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "go.mod").write_text("module example.com/test\n")
            (root / "example_test.go").write_text("package main")

            results = runner_registry.detect_all(root)
            pack_ids = [p.pack_id for p, _ in results]

            assert "go.gotest" in pack_ids

    def test_given_cargo_toml_when_detect_then_finds_rust_runner(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Cargo.toml").write_text('[package]\nname = "test"\n')

            results = runner_registry.detect_all(root)
            pack_ids = [p.pack_id for p, _ in results]

            # Should find either nextest or cargo_test
            assert "rust.nextest" in pack_ids or "rust.cargo_test" in pack_ids

    def test_given_no_markers_when_detect_then_empty_results(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Empty directory

            results = runner_registry.detect_all(root)
            assert len(results) == 0


class TestPackDiscovery:
    """Tests for runner pack target discovery."""

    @pytest.mark.asyncio
    async def test_given_pytest_project_when_discover_then_finds_test_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")
            (root / "tests").mkdir()
            (root / "tests" / "test_one.py").write_text("def test_a(): pass")
            (root / "tests" / "test_two.py").write_text("def test_b(): pass")
            (root / "tests" / "conftest.py").write_text("")  # Should be ignored

            pack_class = runner_registry.get("python.pytest")
            assert pack_class is not None
            pack = pack_class()

            targets = await pack.discover(root)

            selectors = [t.selector for t in targets]
            assert len(targets) == 2
            assert any("test_one.py" in s for s in selectors)
            assert any("test_two.py" in s for s in selectors)

    @pytest.mark.asyncio
    async def test_given_jest_project_when_discover_then_finds_test_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "package.json").write_text('{"jest": {}}')
            (root / "src").mkdir()
            (root / "src" / "app.test.js").write_text("")
            (root / "src" / "app.spec.ts").write_text("")
            (root / "node_modules").mkdir()  # Should be ignored
            (root / "node_modules" / "pkg.test.js").write_text("")

            pack_class = runner_registry.get("js.jest")
            assert pack_class is not None
            pack = pack_class()

            targets = await pack.discover(root)

            selectors = [t.selector for t in targets]
            assert len(targets) == 2
            assert not any("node_modules" in s for s in selectors)

    @pytest.mark.asyncio
    async def test_given_go_project_when_discover_then_finds_packages(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "go.mod").write_text("module example.com/test")
            (root / "pkg").mkdir()
            (root / "pkg" / "foo_test.go").write_text("package foo")
            (root / "internal").mkdir()
            (root / "internal" / "bar_test.go").write_text("package bar")

            pack_class = runner_registry.get("go.gotest")
            assert pack_class is not None
            pack = pack_class()

            targets = await pack.discover(root)

            assert len(targets) == 2
            assert all(t.kind == "package" for t in targets)
