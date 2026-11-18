"""Metadata-only contract for fixed, application-owned runnable bridge entries."""

from dataclasses import dataclass

from .readiness import PACKAGES


@dataclass(frozen=True, slots=True)
class BridgeSpec:
    id: str
    module: str
    dependencies: tuple[tuple[str, str], ...]
    operations: frozenset[str]

    def __post_init__(self) -> None:
        if not self.id or self.id in PACKAGES:
            raise ValueError("A bridge must have a distinct selected profile identity")
        prefix = "backend.services.adapters.profile_bridges."
        if not self.module.startswith(prefix) or not all(part.isidentifier() for part in self.module.split(".")):
            raise ValueError("Bridge installers must be application-owned profile_bridges modules")
        if not self.operations or any(not operation for operation in self.operations):
            raise ValueError("Observation-only or identity-only profiles cannot register runnable bridges")
        if not self.dependencies or len({package for package, _ in self.dependencies}) != len(self.dependencies):
            raise ValueError("A bridge requires distinct, exact dependency pins")
        if any(
            not package or not version or any(char in version for char in "<>=*, ")
            for package, version in self.dependencies
        ):
            raise ValueError("A bridge requires exact installed package versions, not version ranges")
