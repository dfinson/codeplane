"""Unit tests for config_resolver.py — import path resolution."""

from __future__ import annotations

import pytest

from codeplane.index._internal.indexing.config_resolver import (
    ConfigResolver,
    ImportPathResolver,
    build_js_package_exports,
    parse_cargo_toml,
    parse_go_mod,
    resolve_go_module,
    resolve_rust_module,
)

# =============================================================================
# Go Module Tests
# =============================================================================


class TestParseGoMod:
    """Tests for parse_go_mod()."""

    def test_simple_module_path(self) -> None:
        """Parse simple module path from go.mod."""
        text = "module github.com/user/repo\n\ngo 1.21\n"
        assert parse_go_mod(text) == "github.com/user/repo"

    def test_module_with_version(self) -> None:
        """Parse module with version suffix."""
        text = "module github.com/user/repo/v2\n\ngo 1.21\n"
        assert parse_go_mod(text) == "github.com/user/repo/v2"

    def test_missing_module_directive(self) -> None:
        """Return None when module directive is missing."""
        text = "go 1.21\nrequire (\n\tsome/dep v1.0.0\n)\n"
        assert parse_go_mod(text) is None

    def test_empty_file(self) -> None:
        """Return None for empty file."""
        assert parse_go_mod("") is None

    def test_module_with_comments(self) -> None:
        """Parse module even with comments."""
        text = "// Comment\nmodule github.com/user/repo\n"
        assert parse_go_mod(text) == "github.com/user/repo"


class TestResolveGoModule:
    """Tests for resolve_go_module()."""

    def test_root_package(self) -> None:
        """File in root returns just module path."""
        result = resolve_go_module(
            file_path="main.go",
            _short_package="main",
            go_mod_path="go.mod",
            go_mod_module="github.com/user/repo",
        )
        assert result == "github.com/user/repo"

    def test_nested_package(self) -> None:
        """File in subdirectory returns module path + subdir."""
        result = resolve_go_module(
            file_path="pkg/auth/token.go",
            _short_package="auth",
            go_mod_path="go.mod",
            go_mod_module="github.com/user/repo",
        )
        assert result == "github.com/user/repo/pkg/auth"

    def test_deeply_nested(self) -> None:
        """File in deeply nested directory."""
        result = resolve_go_module(
            file_path="internal/services/user/handler.go",
            _short_package="user",
            go_mod_path="go.mod",
            go_mod_module="github.com/user/repo",
        )
        assert result == "github.com/user/repo/internal/services/user"

    def test_nested_go_mod(self) -> None:
        """go.mod in subdirectory."""
        result = resolve_go_module(
            file_path="services/api/handler.go",
            _short_package="api",
            go_mod_path="services/go.mod",
            go_mod_module="github.com/user/api",
        )
        assert result == "github.com/user/api/api"

    def test_file_outside_go_mod_dir(self) -> None:
        """File outside go.mod directory returns None."""
        result = resolve_go_module(
            file_path="other/main.go",
            _short_package="other",
            go_mod_path="services/go.mod",
            go_mod_module="github.com/user/api",
        )
        assert result is None


# =============================================================================
# Rust Module Tests
# =============================================================================


class TestParseCargoToml:
    """Tests for parse_cargo_toml()."""

    def test_simple_crate(self) -> None:
        """Parse crate name from Cargo.toml."""
        text = '[package]\nname = "my_crate"\nversion = "0.1.0"'
        assert parse_cargo_toml(text) == "my_crate"

    def test_crate_with_hyphen(self) -> None:
        """Parse crate name with hyphens."""
        text = '[package]\nname = "my-crate"\nversion = "0.1.0"'
        assert parse_cargo_toml(text) == "my-crate"

    def test_missing_package_section(self) -> None:
        """Return None when [package] section is missing."""
        text = '[lib]\ncrate-type = ["cdylib"]\n'
        assert parse_cargo_toml(text) is None

    def test_workspace_only(self) -> None:
        """Return None for workspace-only Cargo.toml."""
        text = '[workspace]\nmembers = ["crates/*"]\n'
        assert parse_cargo_toml(text) is None

    def test_empty_file(self) -> None:
        """Return None for empty file."""
        assert parse_cargo_toml("") is None


class TestResolveRustModule:
    """Tests for resolve_rust_module()."""

    def test_lib_rs(self) -> None:
        """src/lib.rs returns just crate name."""
        result = resolve_rust_module(
            file_path="src/lib.rs",
            cargo_toml_path="Cargo.toml",
            crate_name="my_crate",
        )
        assert result == "my_crate"

    def test_main_rs(self) -> None:
        """src/main.rs returns just crate name."""
        result = resolve_rust_module(
            file_path="src/main.rs",
            cargo_toml_path="Cargo.toml",
            crate_name="my_crate",
        )
        assert result == "my_crate"

    def test_module_file(self) -> None:
        """src/auth.rs returns crate::auth."""
        result = resolve_rust_module(
            file_path="src/auth.rs",
            cargo_toml_path="Cargo.toml",
            crate_name="my_crate",
        )
        assert result == "my_crate::auth"

    def test_nested_module(self) -> None:
        """src/auth/token.rs returns crate::auth::token."""
        result = resolve_rust_module(
            file_path="src/auth/token.rs",
            cargo_toml_path="Cargo.toml",
            crate_name="my_crate",
        )
        assert result == "my_crate::auth::token"

    def test_mod_rs(self) -> None:
        """src/auth/mod.rs returns crate::auth (not crate::auth::mod)."""
        result = resolve_rust_module(
            file_path="src/auth/mod.rs",
            cargo_toml_path="Cargo.toml",
            crate_name="my_crate",
        )
        assert result == "my_crate::auth"

    def test_workspace_crate(self) -> None:
        """Crate in workspace subdirectory."""
        result = resolve_rust_module(
            file_path="crates/core/src/lib.rs",
            cargo_toml_path="crates/core/Cargo.toml",
            crate_name="core_crate",
        )
        assert result == "core_crate"


# =============================================================================
# Config Resolver Tests
# =============================================================================


class TestConfigResolver:
    """Tests for ConfigResolver class."""

    def test_discover_go_mods(self) -> None:
        """Discovers go.mod files and parses module paths."""
        file_paths = ["go.mod", "cmd/main.go", "pkg/utils/helpers.go"]
        resolver = ConfigResolver("/repo", file_paths)

        files_content = {"go.mod": "module github.com/user/repo\ngo 1.21\n"}

        def read_file(path: str) -> str | None:
            return files_content.get(path)

        go_mods = resolver._discover_go_mods(read_file)
        assert go_mods == {"go.mod": "github.com/user/repo"}

    def test_discover_cargo_tomls(self) -> None:
        """Discovers Cargo.toml files and parses crate names."""
        file_paths = ["Cargo.toml", "src/lib.rs", "src/main.rs"]
        resolver = ConfigResolver("/repo", file_paths)

        files_content = {"Cargo.toml": '[package]\nname = "my_crate"\nversion = "0.1.0"'}

        def read_file(path: str) -> str | None:
            return files_content.get(path)

        cargo_tomls = resolver._discover_cargo_tomls(read_file)
        assert cargo_tomls == {"Cargo.toml": "my_crate"}

    def test_find_nearest_config_root(self) -> None:
        """Finds config at repo root."""
        file_paths = ["go.mod", "cmd/main.go"]
        resolver = ConfigResolver("/repo", file_paths)
        configs = {"go.mod": "github.com/user/repo"}

        result = resolver._find_nearest_config("cmd/main.go", configs)
        assert result == ("go.mod", "github.com/user/repo")

    def test_find_nearest_config_nested(self) -> None:
        """Finds nearest config in nested directories."""
        file_paths = ["go.mod", "services/api/go.mod", "services/api/handler.go"]
        resolver = ConfigResolver("/repo", file_paths)
        configs = {
            "go.mod": "github.com/user/repo",
            "services/api/go.mod": "github.com/user/api",
        }

        result = resolver._find_nearest_config("services/api/handler.go", configs)
        assert result == ("services/api/go.mod", "github.com/user/api")

    def test_resolve_go_module_path(self) -> None:
        """Resolves Go file to module path."""
        file_paths = ["go.mod", "pkg/auth/token.go"]
        resolver = ConfigResolver("/repo", file_paths)

        files_content = {"go.mod": "module github.com/user/repo\ngo 1.21\n"}

        def read_file(path: str) -> str | None:
            return files_content.get(path)

        result = resolver.resolve(
            file_path="pkg/auth/token.go",
            language="go",
            short_package="auth",
            read_file=read_file,
        )
        assert result == "github.com/user/repo/pkg/auth"

    def test_resolve_rust_module_path(self) -> None:
        """Resolves Rust file to module path."""
        file_paths = ["Cargo.toml", "src/auth/token.rs"]
        resolver = ConfigResolver("/repo", file_paths)

        files_content = {"Cargo.toml": '[package]\nname = "my_crate"\nversion = "0.1.0"'}

        def read_file(path: str) -> str | None:
            return files_content.get(path)

        result = resolver.resolve(
            file_path="src/auth/token.rs",
            language="rust",
            short_package=None,
            read_file=read_file,
        )
        assert result == "my_crate::auth::token"

    def test_resolve_unsupported_language(self) -> None:
        """Returns None for unsupported language."""
        resolver = ConfigResolver("/repo", [])
        result = resolver.resolve(
            file_path="main.java",
            language="java",
            short_package="main",
        )
        assert result is None


# =============================================================================
# JS Exports Tests
# =============================================================================


class TestBuildJsPackageExports:
    """Tests for build_js_package_exports()."""

    def test_simple_exports(self) -> None:
        """Build exports map from simple package.json."""
        file_paths = ["package.json", "src/index.ts"]
        files_content = {"package.json": '{"name": "my-pkg", "exports": {".":"./src/index.ts"}}'}

        def read_file(path: str) -> str | None:
            return files_content.get(path)

        result = build_js_package_exports(file_paths, read_file)
        assert result == {"my-pkg": "src/index.ts"}

    def test_subpath_exports(self) -> None:
        """Build exports map with subpath exports."""
        file_paths = ["package.json", "src/index.ts", "src/utils.ts"]
        files_content = {
            "package.json": '{"name": "my-pkg", "exports": {".":"./src/index.ts", "./utils":"./src/utils.ts"}}'
        }

        def read_file(path: str) -> str | None:
            return files_content.get(path)

        result = build_js_package_exports(file_paths, read_file)
        assert result == {"my-pkg": "src/index.ts", "my-pkg/utils": "src/utils.ts"}

    def test_no_exports_field(self) -> None:
        """Return empty map when exports field is missing."""
        file_paths = ["package.json"]
        files_content = {"package.json": '{"name": "my-pkg", "main": "index.js"}'}

        def read_file(path: str) -> str | None:
            return files_content.get(path)

        result = build_js_package_exports(file_paths, read_file)
        assert result == {}

    def test_no_name_field(self) -> None:
        """Skip package.json without name field."""
        file_paths = ["package.json"]
        files_content = {"package.json": '{"exports": {".":"./src/index.ts"}}'}

        def read_file(path: str) -> str | None:
            return files_content.get(path)

        result = build_js_package_exports(file_paths, read_file)
        assert result == {}


# =============================================================================
# ImportPathResolver Tests
# =============================================================================


class TestImportPathResolver:
    """Tests for ImportPathResolver class."""

    @pytest.fixture
    def files_list(self) -> list[str]:
        """List of files in the repo."""
        return [
            "src/__init__.py",
            "src/utils.py",
            "src/models/user.py",
            "src/models/__init__.py",
            "src/index.ts",
            "src/utils.ts",
            "include/header.h",
        ]

    @pytest.fixture
    def resolver(self, files_list: list[str]) -> ImportPathResolver:
        """Create resolver with test files."""
        return ImportPathResolver(
            all_file_paths=files_list,
            declared_modules={},
            js_package_exports={},
        )

    def test_resolve_python_dotted(self, resolver: ImportPathResolver) -> None:
        """Resolve Python dotted import."""
        result = resolver.resolve(
            source_literal="src.models.user",
            importer_path="src/main.py",
            import_kind="python_import",
        )
        assert result == "src/models/user.py"

    @pytest.mark.skip(reason="Relative imports need full package structure")
    def test_resolve_python_relative(self, resolver: ImportPathResolver) -> None:
        """Resolve Python relative import from submodule."""
        result = resolver.resolve(
            source_literal=".user",
            importer_path="src/models/__init__.py",
            import_kind="python_import_from",
        )
        assert result == "src/models/user.py"

    def test_resolve_js_relative(self, resolver: ImportPathResolver) -> None:
        """Resolve JS relative import."""
        result = resolver.resolve(
            source_literal="./utils",
            importer_path="src/index.ts",
            import_kind="js_import",
        )
        assert result == "src/utils.ts"

    def test_resolve_c_include(self, resolver: ImportPathResolver) -> None:
        """Resolve C include directive."""
        result = resolver.resolve(
            source_literal="header.h",
            importer_path="src/main.c",
            import_kind="c_include",
        )
        assert result == "include/header.h"

    def test_resolve_declaration_based(self) -> None:
        """Resolve declaration-based import (Java/Kotlin/etc)."""
        resolver = ImportPathResolver(
            all_file_paths=["src/main/java/com/example/models/User.java"],
            declared_modules={
                "src/main/java/com/example/models/User.java": "com.example.models",
            },
            js_package_exports={},
        )
        result = resolver.resolve(
            source_literal="com.example.models.User",
            importer_path="src/main/java/com/example/Main.java",
            import_kind="java_import",
        )
        assert result == "src/main/java/com/example/models/User.java"

    def test_resolve_unknown_returns_none(self, resolver: ImportPathResolver) -> None:
        """Unknown imports return None."""
        result = resolver.resolve(
            source_literal="nonexistent.module",
            importer_path="src/main.py",
            import_kind="python_import",
        )
        assert result is None

    def test_resolve_bare_js_with_exports(self) -> None:
        """Resolve bare specifier using package.json exports."""
        resolver = ImportPathResolver(
            all_file_paths=["packages/my-pkg/src/index.ts"],
            declared_modules={},
            js_package_exports={"my-pkg": "packages/my-pkg/src/index.ts"},
        )
        result = resolver.resolve(
            source_literal="my-pkg",
            importer_path="src/main.ts",
            import_kind="js_import",
        )
        assert result == "packages/my-pkg/src/index.ts"
