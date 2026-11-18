"""Resolve, validate and pin existing declarative mappings for registered history.

Selection retains the existing resolver order. Only a selected legacy definition
is migrated, at most 1 MiB per registration, with no overwrite or directory crawl.
The decoder reads a private bounded snapshot, so validation and its fingerprint
cannot observe different versions of an operator-edited source file.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.config import NativeMappingsConfig
from backend.models.domain import StateConflictError
from backend.services.ingest.mapping import PARSER_REVISION, NativeMappingDecoder, resolve_mapping_path

_MAX_DEFINITION_BYTES = 1024 * 1024


class MappingPin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    format_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    source_path: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    framework: str
    framework_version: str
    parser_revision: str
    decoder_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class MappingRegistry:
    def __init__(self, config: NativeMappingsConfig | None = None) -> None:
        settings = config or NativeMappingsConfig()
        self._extra_dirs = [Path(path).expanduser() for path in settings.extra_dirs]
        self._migrated_dir = (
            Path(settings.migrated_user_dir).expanduser()
            if settings.migrated_user_dir is not None
            else Path.home() / ".codeplane" / "mappings"
        )

    def _resolve(self, name: str) -> Path:
        path = resolve_mapping_path(name, self._extra_dirs, migrated_user_dir=self._migrated_dir)
        if path is None:
            raise ValueError(f"Native mapping definition is unavailable: {name}")
        return path

    @staticmethod
    def _read(path: Path) -> bytes:
        with path.open("rb") as stream:
            source = stream.read(_MAX_DEFINITION_BYTES + 1)
        if len(source) > _MAX_DEFINITION_BYTES:
            raise ValueError("Native mapping definition exceeds 1 MiB")
        return source

    @staticmethod
    def _decoder(
        name: str,
        source: bytes,
        *,
        job_id: str,
        external_session_id: str,
        anchor: datetime,
        binding_id: str | None = None,
        source_key: str | None = None,
    ) -> NativeMappingDecoder:
        with tempfile.TemporaryDirectory(prefix="codeplane-mapping-") as directory:
            snapshot = Path(directory) / f"{name}.yaml"
            snapshot.write_bytes(source)
            try:
                return NativeMappingDecoder(
                    snapshot,
                    job_id=job_id,
                    external_session_id=external_session_id,
                    anchor=anchor,
                    framework_id=name,
                    binding_id=binding_id,
                    source_key=source_key,
                )
            except (yaml.YAMLError, ValidationError, UnicodeError) as exc:
                raise ValueError(f"Malformed native mapping definition: {name}") from exc

    def _migrate(self, name: str, source: bytes) -> None:
        self._migrated_dir.mkdir(parents=True, exist_ok=True)
        descriptor, candidate = tempfile.mkstemp(prefix=".mapping-", suffix=".tmp", dir=self._migrated_dir)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(source)
                stream.flush()
                os.fsync(stream.fileno())
            # A concurrent destination wins and is revalidated by pin().
            with suppress(FileExistsError):
                os.link(candidate, self._migrated_dir / f"{name}.yaml")
        finally:
            os.unlink(candidate)

    def pin(self, name: str) -> dict[str, Any]:
        path = self._resolve(name)
        source = self._read(path)
        decoder = self._decoder(
            name,
            source,
            job_id="mapping-validation",
            external_session_id="mapping-validation",
            anchor=datetime(1970, 1, 1, tzinfo=UTC),
        )
        legacy_dir = Path.home() / ".traceforge" / "mappings"
        if path.parent.resolve() == legacy_dir.resolve():
            self._migrate(name, source)
            path = self._resolve(name)
            source = self._read(path)
            decoder = self._decoder(
                name,
                source,
                job_id="mapping-validation",
                external_session_id="mapping-validation",
                anchor=datetime(1970, 1, 1, tzinfo=UTC),
            )
        return MappingPin(
            format_id=name,
            source_path=str(path.resolve()),
            content_sha256=hashlib.sha256(source).hexdigest(),
            framework=decoder.mapping.framework,
            framework_version=decoder.mapping.framework_version,
            parser_revision=PARSER_REVISION,
            decoder_sha256=decoder.mapping_digest,
        ).model_dump()

    def decoder(
        self,
        name: str,
        *,
        pin: dict[str, Any] | None = None,
        job_id: str,
        external_session_id: str,
        anchor: datetime,
        binding_id: str | None = None,
        source_key: str | None = None,
    ) -> NativeMappingDecoder:
        try:
            path = self._resolve(name)
            source = self._read(path)
            decoder = self._decoder(
                name,
                source,
                job_id=job_id,
                external_session_id=external_session_id,
                anchor=anchor,
                binding_id=binding_id,
                source_key=source_key,
            )
            if pin is not None:
                selected = MappingPin.model_validate(pin)
                if (
                    selected.format_id != name
                    or selected.content_sha256 != hashlib.sha256(source).hexdigest()
                    or selected.framework != decoder.mapping.framework
                    or selected.framework_version != decoder.mapping.framework_version
                    or selected.parser_revision != PARSER_REVISION
                    or selected.decoder_sha256 != decoder.mapping_digest
                ):
                    raise ValueError("Registered mapping content or decoder version changed")
            return decoder
        except (OSError, ValueError) as exc:
            raise StateConflictError(
                "Native mapping is unavailable or changed; explicit reconciliation required"
            ) from exc
