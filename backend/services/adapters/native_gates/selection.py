"""Select only built-in gates or the fixed application-owned bridge registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec

from backend.services.adapters.contracts import AdapterReadiness

from .bridge_contract import BridgeSpec
from .readiness import PACKAGES, UnsupportedNativeHookError

_BRIDGES = "backend.services.adapters.profile_bridges"


@dataclass(frozen=True, slots=True)
class NativeInstallation:
    id: str
    module: str
    dependencies: tuple[tuple[str, str], ...]
    operations: frozenset[str]

    def readiness(self) -> AdapterReadiness:
        versions = []
        for package, expected in self.dependencies:
            try:
                installed = version(package)
            except PackageNotFoundError:
                return AdapterReadiness(False, f"Missing optional dependency: {package}")
            versions.append(installed)
            if installed != expected:
                return AdapterReadiness(
                    False, f"Unsupported {package} version; qualified release is {expected}", installed
                )
        if find_spec(self.module) is None:
            return AdapterReadiness(False, f"App-owned native installer is unavailable: {self.id}", versions[0])
        return AdapterReadiness(True, installed_version=versions[0])


def select_installation(profile_id: str) -> NativeInstallation:
    if profile_id in PACKAGES:
        return NativeInstallation(
            profile_id, f"{__package__}.{profile_id}", PACKAGES[profile_id], frozenset({"tool_call"})
        )
    try:
        registry = import_module(_BRIDGES)
    except ModuleNotFoundError as exc:
        if exc.name != _BRIDGES:
            raise
        raise UnsupportedNativeHookError(f"No runnable native bridge is registered for {profile_id}") from exc
    entries = getattr(registry, "BRIDGES", None)
    if not isinstance(entries, Mapping):
        raise UnsupportedNativeHookError("The fixed native bridge registry is unavailable")
    spec = entries.get(profile_id)
    if not isinstance(spec, BridgeSpec) or spec.id != profile_id:
        raise UnsupportedNativeHookError(f"No runnable native bridge is registered for {profile_id}")
    return NativeInstallation(spec.id, spec.module, spec.dependencies, spec.operations)


def require_installation(profile_id: str) -> NativeInstallation:
    installation = select_installation(profile_id)
    readiness = installation.readiness()
    if not readiness.ready:
        raise UnsupportedNativeHookError(readiness.reason)
    return installation
