"""Final-package ownership checks, not blanket removal of historical names or data."""

import ast
import tomllib
from pathlib import Path


def test_backend_has_no_executable_vendor_imports_or_retained_extra():
    root = Path(__file__).resolve().parents[3]
    retired = {"traceforge", "traceforge_title_model", "title_model"}
    found: list[tuple[str, str]] = []
    for path in (root / "backend").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            found.extend((str(path.relative_to(root)), name) for name in names if name.split(".")[0] in retired)
    assert found == []
    manifest = tomllib.loads((root / "pyproject.toml").read_text())
    assert "legacy-traceforge" not in manifest["project"]["optional-dependencies"]
    locked = tomllib.loads((root / "uv.lock").read_text())
    assert not {"traceforge-toolkit", "traceforge-title-model"}.intersection(
        package["name"] for package in locked["package"]
    )


def test_application_owned_formats_and_provenance_remain_without_vendor_runtime():
    from backend.services.adapters.profile_contracts import PROFILES
    from backend.services.ingest.mapping import NATIVE_FORMATS, resolve_mapping_path

    assert len(NATIVE_FORMATS) == 24 and len({profile.id for profile in PROFILES}) == 32
    assert all(resolve_mapping_path(name).is_file() for name in NATIVE_FORMATS)
    provenance = Path(__file__).resolve().parents[2] / "compat/traceforge.LICENSE"
    assert provenance.is_file()
