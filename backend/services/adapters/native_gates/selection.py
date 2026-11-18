"""Select only built-in gates or the fixed application-owned bridge registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING

from backend.services.adapters.contracts import AdapterReadiness

from .bridge_contract import BridgeSpec
from .readiness import PACKAGES, UnsupportedNativeHookError

if TYPE_CHECKING:
    from backend.models.execution_contracts import ImplementationPin

_BRIDGES = "backend.services.adapters.profile_bridges"


@dataclass(frozen=True, slots=True)
class NativeInstallation:
    id: str
    module: str
    dependencies: tuple[tuple[str, str], ...]
    operations: frozenset[str]
    observation_mapping: bool = False

    def observation_source(self) -> tuple[ImplementationPin, Path] | None:
        if not self.observation_mapping:
            return None
        from backend.services.runtime.contract_catalog import _pin

        spec = find_spec(self.module)
        if spec is None or spec.origin is None:
            raise UnsupportedNativeHookError("Native observation source is unavailable")
        source = Path(spec.origin)
        mapping = source.with_suffix(".yaml")
        if not mapping.is_file():
            raise UnsupportedNativeHookError("Native observation mapping is unavailable")
        files = [(self.module, source), (self.id, mapping)]
        if self.id in {"openai-client", "anthropic-client", "gemini-client", "mistral-client", "bedrock-client"}:
            helper = source.with_name("_client_calls.py")
            if not helper.is_file():
                raise UnsupportedNativeHookError("Native client helper is unavailable")
            files.append(("native-client-calls", helper))
        return _pin(self.id, files), mapping

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
        if self.observation_mapping:
            try:
                self.observation_source()
            except UnsupportedNativeHookError as exc:
                return AdapterReadiness(False, str(exc), versions[0])
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
    return NativeInstallation(spec.id, spec.module, spec.dependencies, spec.operations, spec.observation_mapping)


def require_installation(profile_id: str) -> NativeInstallation:
    installation = select_installation(profile_id)
    readiness = installation.readiness()
    if not readiness.ready:
        raise UnsupportedNativeHookError(readiness.reason)
    return installation
