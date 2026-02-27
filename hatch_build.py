"""Hatch build hook — compile cplcache binary at wheel/editable install time."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CplcacheBuildHook(BuildHookInterface):
    """Compile cplcache.c into a platform binary during wheel build."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:  # noqa: ARG002
        source = Path(self.root) / "src" / "codeplane" / "bin" / "cplcache.c"
        if not source.exists():
            return

        is_windows = platform.system() == "Windows"
        binary_name = "cplcache.exe" if is_windows else "cplcache"
        binary_path = source.parent / binary_name

        compiler = _find_compiler(is_windows)
        if not compiler:
            print("WARNING: no C compiler found — cplcache binary will not be available")
            return

        cmd = _build_command(compiler, source, binary_path, is_windows)
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            print(f"WARNING: cplcache compilation failed: {exc}", file=sys.stderr)
            return

        # The compiled binary sits in src/codeplane/bin/ which is already
        # included by the wheel target (packages = ["src/codeplane"]).
        # Mark the wheel as platform-specific since it ships a native binary.
        build_data["pure_python"] = False
        build_data["infer_tag"] = True


def _find_compiler(is_windows: bool) -> str | None:
    if is_windows:
        return shutil.which("cl") or shutil.which("gcc")
    return shutil.which("cc") or shutil.which("gcc")


def _build_command(compiler: str, source: Path, output: Path, is_windows: bool) -> list[str]:
    name = Path(compiler).stem.lower()
    if is_windows and name == "cl":
        return [compiler, "/O2", str(source), f"/Fe:{output}", "ws2_32.lib"]
    if is_windows:
        # MinGW gcc on Windows
        return [compiler, "-O2", "-o", str(output), str(source), "-lws2_32"]
    return [compiler, "-O2", "-o", str(output), str(source)]
