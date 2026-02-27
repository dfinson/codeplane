"""Hatch build hook — compile cplcache binary for all platforms at build time.

Compiles cplcache.c for the native platform (always) and cross-compiles for
other platforms when cross-compiler toolchains are available.  Binaries are
named ``cplcache-{os}-{arch}[.exe]`` and shipped inside the wheel.  At
runtime, ``_inject_cplcache_binary`` picks the one matching the current host.
"""

import platform
import shutil
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import (
    BuildHookInterface,
)


class _Target:
    """Cross-compilation target descriptor."""

    __slots__ = ("os", "arch", "ext", "compilers", "flags")

    def __init__(
        self,
        os: str,
        arch: str,
        ext: str,
        compilers: tuple[str, ...],
        flags: tuple[str, ...] = (),
    ) -> None:
        self.os = os
        self.arch = arch
        self.ext = ext
        self.compilers = compilers
        self.flags = flags

    @property
    def binary_name(self) -> str:
        return f"cplcache-{self.os}-{self.arch}{self.ext}"


_TARGETS: tuple[_Target, ...] = (
    _Target("linux", "x86_64", "", ("x86_64-linux-gnu-gcc", "gcc", "cc"), ()),
    _Target("linux", "aarch64", "", ("aarch64-linux-gnu-gcc",), ()),
    _Target("darwin", "x86_64", "", ("x86_64-apple-darwin-gcc", "o64-clang"), ()),
    _Target("darwin", "arm64", "", ("aarch64-apple-darwin-gcc", "oa64-clang"), ()),
    _Target("windows", "x86_64", ".exe", ("x86_64-w64-mingw32-gcc",), ("-lws2_32",)),
)

# Map platform.system() → target os, platform.machine() → target arch
_OS_MAP = {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}
_ARCH_MAP = {"x86_64": "x86_64", "AMD64": "x86_64", "aarch64": "aarch64", "arm64": "arm64"}


class CplcacheBuildHook(BuildHookInterface):
    """Compile cplcache.c for all reachable platforms during wheel build."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, object]) -> None:  # noqa: ARG002
        source = Path(self.root) / "src" / "codeplane" / "bin" / "cplcache.c"
        if not source.exists():
            return

        host_os = _OS_MAP.get(platform.system())
        host_arch = _ARCH_MAP.get(platform.machine())
        built_any = False

        for target in _TARGETS:
            binary_path = source.parent / target.binary_name
            is_native = target.os == host_os and target.arch == host_arch

            compiler = _find_compiler(target, is_native)
            if not compiler:
                continue

            cmd = _build_command(compiler, source, binary_path, target)
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=60)
                built_any = True
                print(f"cplcache: compiled {target.binary_name}")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
                if is_native:
                    print(
                        f"WARNING: native cplcache compilation failed: {exc}",
                        file=sys.stderr,
                    )

        if not built_any:
            print("WARNING: no cplcache binaries compiled — is a C compiler available?")


def _find_compiler(target: _Target, is_native: bool) -> str | None:
    """Find a working compiler for *target*, preferring native ``cc``."""
    if is_native:
        # Native build: also accept generic cc/gcc
        for name in ("cc", "gcc", *target.compilers):
            found: str | None = shutil.which(name)
            if found:
                return found
        return None
    # Cross-compile: only accept target-prefixed compilers
    for name in target.compilers:
        found = shutil.which(name)
        if found:
            return found
    return None


def _build_command(compiler: str, source: Path, output: Path, target: _Target) -> list[str]:
    cmd = [compiler, "-O2", "-o", str(output), str(source)]
    cmd.extend(target.flags)
    return cmd
