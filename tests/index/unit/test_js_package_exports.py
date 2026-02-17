"""Tests for JS/TS package.json exports resolution.

Covers:
- build_js_package_exports(): parsing package.json exports field
- _parse_export_target(): extracting source file from conditional exports
- _resolve_export_target(): .js -> .ts remapping and extension probing
- ImportPathResolver._resolve_js(): bare specifier lookup via exports map
"""

from __future__ import annotations

import json

from codeplane.index._internal.indexing.config_resolver import (
    ImportPathResolver,
    _parse_export_target,
    _resolve_export_target,
    build_js_package_exports,
)

# ---------------------------------------------------------------------------
# _parse_export_target
# ---------------------------------------------------------------------------


class TestParseExportTarget:
    """Tests for extracting source file path from exports value."""

    def test_string_value(self) -> None:
        assert _parse_export_target("./src/index.ts") == "./src/index.ts"

    def test_conditional_prefers_source(self) -> None:
        """Should prefer @*/source key over import/require."""
        value = {
            "@zod/source": "./src/v4/index.ts",
            "types": "./v4/index.d.cts",
            "import": "./v4/index.js",
            "require": "./v4/index.cjs",
        }
        assert _parse_export_target(value) == "./src/v4/index.ts"

    def test_conditional_falls_back_to_types(self) -> None:
        value = {
            "types": "./v4/index.d.ts",
            "import": "./v4/index.js",
        }
        assert _parse_export_target(value) == "./v4/index.d.ts"

    def test_conditional_falls_back_to_import(self) -> None:
        value = {
            "import": "./dist/index.mjs",
            "require": "./dist/index.cjs",
        }
        assert _parse_export_target(value) == "./dist/index.mjs"

    def test_none_for_non_string_non_dict(self) -> None:
        assert _parse_export_target(42) is None
        assert _parse_export_target(None) is None


# ---------------------------------------------------------------------------
# _resolve_export_target
# ---------------------------------------------------------------------------


class TestResolveExportTarget:
    """Tests for resolving export target to actual file path."""

    def test_exact_match(self) -> None:
        paths = {"packages/zod/src/v4/index.ts"}
        assert (
            _resolve_export_target("packages/zod/src/v4/index.ts", paths)
            == "packages/zod/src/v4/index.ts"
        )

    def test_js_to_ts_remap(self) -> None:
        paths = {"packages/zod/src/v4/index.ts"}
        assert (
            _resolve_export_target("packages/zod/src/v4/index.js", paths)
            == "packages/zod/src/v4/index.ts"
        )

    def test_jsx_to_tsx_remap(self) -> None:
        paths = {"src/App.tsx"}
        assert _resolve_export_target("src/App.jsx", paths) == "src/App.tsx"

    def test_extension_probing(self) -> None:
        paths = {"src/utils.ts"}
        assert _resolve_export_target("src/utils", paths) == "src/utils.ts"

    def test_index_probing(self) -> None:
        paths = {"src/core/index.ts"}
        assert _resolve_export_target("src/core", paths) == "src/core/index.ts"

    def test_returns_none_for_missing(self) -> None:
        paths = {"src/other.ts"}
        assert _resolve_export_target("src/missing", paths) is None


# ---------------------------------------------------------------------------
# build_js_package_exports
# ---------------------------------------------------------------------------


class TestBuildJsPackageExports:
    """Tests for building bare specifier -> file path map."""

    @staticmethod
    def _make_reader(file_contents: dict[str, str]):
        def read_file(path: str) -> str | None:
            return file_contents.get(path)

        return read_file

    def test_simple_monorepo_exports(self) -> None:
        """Should map package exports to repo-relative paths."""
        pkg = {
            "name": "zod",
            "exports": {
                ".": {"@zod/source": "./src/index.ts", "import": "./index.js"},
                "./v4": {"@zod/source": "./src/v4/index.ts", "import": "./v4/index.js"},
                "./v3": {"@zod/source": "./src/v3/index.ts", "import": "./v3/index.js"},
                "./mini": {"@zod/source": "./src/mini/index.ts", "import": "./mini/index.js"},
            },
        }
        file_paths = [
            "packages/zod/package.json",
            "packages/zod/src/index.ts",
            "packages/zod/src/v4/index.ts",
            "packages/zod/src/v3/index.ts",
            "packages/zod/src/mini/index.ts",
        ]
        reader = self._make_reader(
            {
                "packages/zod/package.json": json.dumps(pkg),
            }
        )

        result = build_js_package_exports(file_paths, reader)

        assert result["zod"] == "packages/zod/src/index.ts"
        assert result["zod/v4"] == "packages/zod/src/v4/index.ts"
        assert result["zod/v3"] == "packages/zod/src/v3/index.ts"
        assert result["zod/mini"] == "packages/zod/src/mini/index.ts"

    def test_root_package_json(self) -> None:
        """Should handle package.json at repo root."""
        pkg = {
            "name": "my-lib",
            "exports": {
                ".": "./src/index.ts",
            },
        }
        file_paths = ["package.json", "src/index.ts"]
        reader = self._make_reader({"package.json": json.dumps(pkg)})

        result = build_js_package_exports(file_paths, reader)

        assert result["my-lib"] == "src/index.ts"

    def test_skips_wildcard_exports(self) -> None:
        """Should skip wildcard export entries."""
        pkg = {
            "name": "zod",
            "exports": {
                "./v4/locales/*": {"import": "./v4/locales/*"},
            },
        }
        file_paths = ["package.json"]
        reader = self._make_reader({"package.json": json.dumps(pkg)})

        result = build_js_package_exports(file_paths, reader)

        assert len(result) == 0

    def test_skips_packages_without_exports(self) -> None:
        """Should silently skip package.json without exports field."""
        pkg = {"name": "simple-pkg", "version": "1.0.0"}
        file_paths = ["package.json"]
        reader = self._make_reader({"package.json": json.dumps(pkg)})

        result = build_js_package_exports(file_paths, reader)

        assert len(result) == 0

    def test_js_to_ts_remapping_in_exports(self) -> None:
        """Should remap .js targets to .ts when .ts exists."""
        pkg = {
            "name": "my-lib",
            "exports": {
                ".": {"import": "./src/index.js"},
            },
        }
        # Only .ts exists, not .js
        file_paths = ["package.json", "src/index.ts"]
        reader = self._make_reader({"package.json": json.dumps(pkg)})

        result = build_js_package_exports(file_paths, reader)

        assert result["my-lib"] == "src/index.ts"

    def test_multiple_packages_in_monorepo(self) -> None:
        """Should handle multiple scoped packages."""
        pkg_a = {"name": "@scope/core", "exports": {".": "./src/index.ts"}}
        pkg_b = {"name": "@scope/utils", "exports": {".": "./src/index.ts"}}
        file_paths = [
            "packages/core/package.json",
            "packages/core/src/index.ts",
            "packages/utils/package.json",
            "packages/utils/src/index.ts",
        ]
        reader = self._make_reader(
            {
                "packages/core/package.json": json.dumps(pkg_a),
                "packages/utils/package.json": json.dumps(pkg_b),
            }
        )

        result = build_js_package_exports(file_paths, reader)

        assert result["@scope/core"] == "packages/core/src/index.ts"
        assert result["@scope/utils"] == "packages/utils/src/index.ts"


# ---------------------------------------------------------------------------
# ImportPathResolver._resolve_js with exports
# ---------------------------------------------------------------------------


class TestResolveJsBareSpecifier:
    """Tests for bare specifier resolution via package.json exports."""

    def test_bare_specifier_resolved_via_exports(self) -> None:
        """Should resolve bare specifier to file from exports map."""
        js_exports = {"zod/v4": "packages/zod/src/v4/index.ts"}
        resolver = ImportPathResolver(
            ["packages/zod/src/v4/index.ts", "src/app.ts"],
            {},
            js_exports,
        )

        result = resolver.resolve("zod/v4", "js_import", "src/app.ts")
        assert result == "packages/zod/src/v4/index.ts"

    def test_bare_specifier_external_returns_none(self) -> None:
        """External packages not in exports should return None."""
        resolver = ImportPathResolver(
            ["src/app.ts"],
            {},
            {"zod": "packages/zod/src/index.ts"},
        )

        result = resolver.resolve("react", "js_import", "src/app.ts")
        assert result is None

    def test_relative_import_still_works(self) -> None:
        """Relative imports should continue to work as before."""
        resolver = ImportPathResolver(
            ["src/utils.ts", "src/app.ts"],
            {},
            {},
        )

        result = resolver.resolve("./utils", "js_import", "src/app.ts")
        assert result == "src/utils.ts"

    def test_bare_specifier_with_no_exports_map(self) -> None:
        """Without exports map, bare specifiers return None."""
        resolver = ImportPathResolver(
            ["src/app.ts"],
            {},
        )

        result = resolver.resolve("zod", "js_import", "src/app.ts")
        assert result is None
