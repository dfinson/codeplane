"""Local path validation must not resolve rejected targets before containment."""

import os
import subprocess
from pathlib import Path

import pytest

from backend.services.adapters.capability_guard import enforce_tool_capabilities
from backend.services.adapters.contracts import CapabilityDeniedError
from backend.services.auth.workspace_paths import workspace_target


@pytest.mark.parametrize(
    "raw",
    [
        "../outside/file",
        "child/../../outside/file",
        r"\\path-probe.invalid\share\file.txt",
        "//path-probe.invalid/share/file.txt",
        r"\\?\UNC\path-probe.invalid\share\file.txt",
        r"\\?\C:\outside\file",
        r"\\.\NUL",
        r"C:relative",
    ],
)
async def test_rejected_targets_never_reach_resolve(tmp_path, monkeypatch, raw):
    root = tmp_path.resolve()
    original = Path.resolve
    seen = []

    def resolve(path, *args, **kwargs):
        seen.append(path)
        assert path == root, f"Untrusted target reached filesystem resolution: {path}"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError):
        workspace_target(str(root), raw)
    with pytest.raises(CapabilityDeniedError):
        await enforce_tool_capabilities(
            frozenset({"read"}), workspace=str(root), kind="read", tool_name="Read", paths=(raw,)
        )
    assert all(path == root for path in seen)


def test_absolute_sibling_is_rejected_before_target_resolution(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        assert path == root
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError):
        workspace_target(str(root), str(root.with_name(root.name + "-sibling") / "file"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX native filenames preserve backslashes")
async def test_posix_native_backslash_target_cannot_certify_rewritten_inside_path(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / r"a\b").mkdir()
    (root / "a" / "b").mkdir(parents=True)
    (tmp_path / "outside").write_text("outside bytes")
    (root / "outside").write_text("different inside bytes")
    raw = r"a\b/../../outside"
    assert (root / raw).resolve() == tmp_path / "outside"
    assert (root / raw).read_text() == "outside bytes"
    with pytest.raises(ValueError):
        workspace_target(str(root), raw)
    with pytest.raises(CapabilityDeniedError):
        await enforce_tool_capabilities(
            frozenset({"read"}), workspace=str(root), kind="read", tool_name="Read", paths=(raw,)
        )


@pytest.mark.parametrize("target", ["outside", "remote", "drive-relative"])
def test_symlink_target_is_checked_before_following(tmp_path, monkeypatch, target):
    root = tmp_path / "workspace"
    root.mkdir()
    destination = {
        "outside": str(tmp_path / "outside"),
        "remote": r"\\path-probe.invalid\share",
        "drive-relative": r"C:relative",
    }[target]
    link = root / "link"
    local = root / "local"
    local.mkdir()
    try:
        link.symlink_to(local, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege unavailable")
        raise
    readlink = os.readlink

    def declared_destination(path, *args, **kwargs):
        return destination if Path(path) == link else readlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "readlink", declared_destination)
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        assert path == root, "A rejected link reached the platform resolver"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError):
        workspace_target(str(root), "link/file")


def test_local_paths_and_inside_symlink_keep_physical_semantics(tmp_path):
    root = tmp_path / "workspace"
    (root / "real" / "child").mkdir(parents=True)
    (root / "real" / "file").write_text("inside bytes")
    try:
        (root / "alias").symlink_to("real/child", target_is_directory=True)
        (tmp_path / "workspace-alias").symlink_to(root, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege unavailable")
        raise
    assert workspace_target(str(root), "alias/../file") == root / "real" / "file"
    assert workspace_target(str(root), "real/new/file") == root / "real" / "new" / "file"
    assert workspace_target(str(root), str(root / "real" / "file")).read_text() == "inside bytes"
    alias = tmp_path / "workspace-alias"
    assert workspace_target(str(alias), str(alias / "real" / "file")) == root / "real" / "file"


@pytest.mark.skipif(os.name == "nt", reason="Owned POSIX symlink loop")
def test_symlink_loop_and_dotdot_escape_fail_closed(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "loop").symlink_to("loop")
    (root / "back").symlink_to(".")
    with pytest.raises((ValueError, RuntimeError, OSError)):
        workspace_target(str(root), "loop/file")
    with pytest.raises(ValueError):
        workspace_target(str(root), "back/../outside")


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows drive, device and junction semantics")
@pytest.mark.parametrize(
    "raw", [r"\outside", "/outside", "NUL", "CON/file", "file:stream", "dir/.. /file", "dir./file"]
)
def test_windows_aliases_are_rejected_before_resolution(tmp_path, monkeypatch, raw):
    root = tmp_path.resolve()
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        assert path == root
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError):
        workspace_target(str(root), raw)


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows drive-relative and other-drive semantics")
@pytest.mark.parametrize("relative", [True, False])
def test_windows_drive_siblings_are_rejected_before_resolution(tmp_path, monkeypatch, relative):
    root = tmp_path.resolve()
    raw = root.drive + "file" if relative else ("Y:" if root.drive.upper() == "Z:" else "Z:") + r"\outside\file"
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        assert path == root
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError):
        workspace_target(str(root), raw)


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows local junction semantics")
@pytest.mark.parametrize("inside", [False, True])
def test_windows_owned_local_junction(tmp_path, monkeypatch, inside):
    root = tmp_path / "workspace"
    root.mkdir()
    target = (root if inside else tmp_path) / "destination"
    target.mkdir()
    link = root / "junction"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    try:
        original = Path.resolve

        def resolve(path, *args, **kwargs):
            assert inside or path == root
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", resolve)
        if inside:
            assert workspace_target(str(root), "junction/new") == target / "new"
        else:
            with pytest.raises(ValueError):
                workspace_target(str(root), "junction/new")
    finally:
        link.rmdir()
