"""Fingerprint the selected local implementations without constructing any SDK."""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import distribution
from pathlib import Path

from backend.models.execution_contracts import ExecutionContract, ImplementationPin
from backend.services.action_policy.agt_policy import POLICY_VERSION
from backend.services.events.activity_projection import ActivityProjection
from backend.services.ingest.mapping import NATIVE_FORMATS, PARSER_REVISION, resolve_mapping_path

_BACKEND = Path(__file__).resolve().parents[2]
CONTRACT_ID = "cp-standalone-v1"


def _pin(identity: str, files: list[tuple[str, Path]]) -> ImplementationPin:
    digest = hashlib.sha256()
    for label, path in sorted(files):
        digest.update(label.encode("utf-8") + b"\0")
        with path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return ImplementationPin(id=identity, sha256=digest.hexdigest())


def _sources(identity: str, *paths: str) -> ImplementationPin:
    return _pin(identity, [(path, _BACKEND / path) for path in paths])


def _distribution(name: str, expected: str) -> ImplementationPin:
    package = distribution(name)
    if package.version != expected or package.files is None:
        raise ValueError(f"Execution contract requires {name}=={expected}")
    files = [
        (str(item), Path(str(package.locate_file(item))))
        for item in package.files
        if ".." not in item.parts
        and item.suffix not in {".pyc", ".pyo"}
        and not any(part.endswith(".dist-info") for part in item.parts)
    ]
    if not files:
        raise ValueError(f"No installed implementation files for {name}")
    return _pin(f"{name}/{expected}", files)


def standalone_contract() -> ExecutionContract:
    """Only these actual installed sources are eligible for a new admission."""
    governance = _distribution("agent-governance-toolkit-core", "5.0.0")
    policy = _sources(
        POLICY_VERSION,
        "services/action_policy/agt_policy.py",
        "services/action_policy/authority.py",
        "services/action_policy/standalone_router.py",
        "services/action_policy/session_policy.py",
        "services/action_policy/assessment.py",
        "models/action_assessment.py",
        "services/action_policy/credential_filter.py",
        "persistence/governance_repo.py",
    )
    policy = policy.model_copy(
        update={
            "sha256": hashlib.sha256(
                json.dumps([policy.sha256, governance.model_dump(mode="json")], sort_keys=True).encode(),
            ).hexdigest()
        }
    )
    mappings = []
    for name in sorted(NATIVE_FORMATS):
        path = resolve_mapping_path(name)
        if path is None:
            raise ValueError(f"Required native mapping is unavailable: {name}")
        mappings.append(_pin(name, [(name, path)]))
    return ExecutionContract(
        id=CONTRACT_ID,
        policy=policy,
        parser=_sources(
            PARSER_REVISION,
            "services/ingest/mapping.py",
            "services/ingest/otel_decoder.py",
            "services/action_policy/syntax.py",
            "services/events/tool_pairing.py",
        ),
        grouping=_sources(ActivityProjection.version, "services/events/activity_projection.py"),
        title=_sources(
            "cp-deterministic-title-v1",
            "services/events/activity_projection.py",
            "services/action_policy/credential_filter.py",
        ),
        grammars=tuple(
            _distribution(name, version)
            for name, version in (
                ("tree-sitter", "0.25.2"),
                ("tree-sitter-bash", "0.25.1"),
                ("tree-sitter-powershell", "0.26.4"),
                ("tree-sitter-markdown", "0.5.1"),
            )
        ),
        mappings=tuple(mappings),
    )
