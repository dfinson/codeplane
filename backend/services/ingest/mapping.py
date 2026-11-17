"""Standalone, versioned decoding of the released native trace format mappings."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from backend.compat.traceformats.preprocessors import get_preprocessor
from backend.models.session_event import EventMetadata, SessionEvent, ToolMotivation

_BUNDLED = Path(__file__).parents[2] / "compat" / "traceformats" / "mappings"
NATIVE_FORMATS = frozenset(
    [
        "aider",
        "aider_markdown",
        "amazonq",
        "antigravity",
        "claude",
        "cline",
        "codex",
        "continue_dev",
        "copilot",
        "copilot_markdown",
        "copilot_vscode",
        "crewai",
        "goose",
        "langchain",
        "langgraph",
        "maf",
        "maf_transcript",
        "openai_agents",
        "opencode",
        "openhands",
        "pydantic_ai",
        "semantic_kernel",
        "smolagents",
        "sweagent",
    ]
)
PARSER_REVISION = "cp-mappings-v2"


@dataclass(frozen=True, slots=True)
class DecoderState:
    intent: str | None
    reasoning: str | None
    references: tuple[str, ...]
    turn: int
    active_turn: str | None = None


class _MappingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventMapping(_MappingModel):
    kind: str
    payload: dict[str, str] = Field(default_factory=dict)


class SpanMapping(_MappingModel):
    kind: str
    attributes: dict[str, str] = Field(default_factory=dict)


class MotivationSource(_MappingModel):
    events: list[str]
    field: str = "content"
    role: Literal["intent", "reasoning"] = "intent"


class MotivationConfig(_MappingModel):
    sources: list[MotivationSource] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=lambda: ["tool.call.started", "tool.call.completed"])
    source_window: int = Field(default=10, ge=1, le=1000)


class FrameworkMapping(_MappingModel):
    framework: str
    framework_version: str
    ingestion_mode: Literal["stream", "file_watch", "poll", "replay", "sqlite"]
    type_field: str = "type"
    repo_field: str | None = None
    timestamp_field: str | None = None
    default_kind: str = "raw"
    preprocessor: str | None = None
    events: dict[str, EventMapping] = Field(default_factory=dict)
    spans: dict[str, SpanMapping] = Field(default_factory=dict)
    motivation: MotivationConfig | None = None

    def get_motivation_config(self) -> MotivationConfig:
        return self.motivation or MotivationConfig()


def resolve_mapping_path(
    name: str, extra_dirs: list[Path] | None = None, *, migrated_user_dir: Path | None = None
) -> Path | None:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
        raise ValueError("Invalid trace mapping identifier")
    directories = [
        *(extra_dirs or ()),
        migrated_user_dir or Path.home() / ".codeplane" / "mappings",
        Path.home() / ".traceforge" / "mappings",
        _BUNDLED,
    ]
    return next(
        (path for directory in directories if (path := directory.expanduser() / f"{name}.yaml").is_file()),
        None,
    )


def canonical_record(raw: dict[str, Any]) -> bytes:
    return json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _path(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, (tuple, list)):
            try:
                current = current[int(part)]
            except (IndexError, ValueError):
                return None
        else:
            return None
    return current


def _timestamp(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError("Non-finite source timestamp")
        divisor = 1_000_000_000 if value > 1e15 else 1000 if value > 1e12 else 1
        try:
            return datetime.fromtimestamp(value / divisor, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("Source timestamp is outside the supported range") from exc
    if isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Malformed source timestamp requires reconciliation") from exc
        return timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp
    if value is not None:
        raise ValueError("Unsupported source timestamp")
    return fallback


class NativeMappingDecoder:
    def __init__(
        self,
        mapping_path: Path,
        *,
        job_id: str,
        external_session_id: str,
        anchor: datetime,
        framework_id: str | None = None,
        binding_id: str | None = None,
        legacy_identity: bool = False,
        source_key: str | None = None,
    ) -> None:
        if not job_id.strip() or not external_session_id.strip():
            raise ValueError("Native decoding requires distinct explicit Job and external source identities")
        source = mapping_path.read_bytes()
        if len(source) > 1024 * 1024:
            raise ValueError("Mapping exceeds 1 MiB")
        self.mapping = FrameworkMapping.model_validate(yaml.safe_load(source))
        self.framework = framework_id or mapping_path.stem
        self.job_id = job_id
        self.external_id = external_session_id
        self.binding_id = binding_id
        if legacy_identity and binding_id is not None:
            raise ValueError("Legacy identity is only valid for explicitly unbound historical sources")
        self.legacy_identity = legacy_identity
        self._scope = {"job_id": job_id, "binding_id": binding_id, "scope": "binding" if binding_id else "job"}
        if source_key is not None:
            self._scope["source_key"] = source_key
        self.anchor = anchor.replace(tzinfo=UTC) if anchor.tzinfo is None else anchor
        self._preprocessor = get_preprocessor(self.mapping.preprocessor) if self.mapping.preprocessor else None
        if self.mapping.preprocessor and self._preprocessor is None:
            raise ValueError(f"Unregistered trace preprocessor: {self.mapping.preprocessor}")
        preprocessor_file = inspect.getsourcefile(self._preprocessor) if self._preprocessor is not None else None
        self.mapping_digest = hashlib.sha256(
            canonical_record(
                {
                    "revision": PARSER_REVISION,
                    "legacy_identity": legacy_identity,
                    "mapping_sha256": hashlib.sha256(source).hexdigest(),
                    "preprocessor_sha256": hashlib.sha256(Path(preprocessor_file).read_bytes()).hexdigest()
                    if preprocessor_file
                    else None,
                    "parser_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                }
            )
        ).hexdigest()
        self._config = self.mapping.get_motivation_config()
        self._sources: dict[str, list[tuple[str, str]]] = {
            kind: [] for source in self._config.sources for kind in source.events
        }
        for source_config in self._config.sources:
            for kind in source_config.events:
                self._sources[kind].append((source_config.field, source_config.role))
        self._references: deque[str] = deque(maxlen=self._config.source_window)
        self._intent: str | None = None
        self._reasoning: str | None = None
        self._turn = 0
        self._active_turn: str | None = None

    def _event_id(self, record_index: int, output_index: int, digest: str) -> str:
        values: list[object] = [
            "codeplane-native-v1" if self.legacy_identity else "codeplane-native-v2",
            self.framework,
            self.external_id,
            record_index,
            output_index,
            digest,
        ]
        if not self.legacy_identity:
            values.insert(1, self._scope)
        return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(values, sort_keys=True, separators=(",", ":"))))

    def _turn_id(self) -> str:
        material = (
            f"{self.job_id}:native-turn:{self._turn}"
            if self.legacy_identity
            else json.dumps(
                ["codeplane-turns-v2", self._session_scope(), self.framework, self.external_id, self._turn],
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return str(uuid.uuid5(uuid.NAMESPACE_URL, material))

    def _session_scope(self) -> dict[str, str | None]:
        return {key: value for key, value in self._scope.items() if key != "source_key"}

    def _group_payload(self, payload: dict[str, Any], kind: str, raw: dict[str, Any]) -> None:
        if self.legacy_identity:
            if not payload.get("turn_id"):
                payload["turn_id"] = self._turn_id()
            if kind == "message.assistant":
                self._turn += 1
            return
        real_operator = kind == "message.user" and not (
            payload.get("admission_status") or payload.get("execution_status") == "unconfirmed"
        )
        if real_operator:
            self._turn += 1
            self._active_turn = None
        explicit = payload.get("turn_id")
        if not isinstance(explicit, str) or not explicit.strip():
            explicit = next(
                (
                    value
                    for path in ("turn_id", "turnId", "data.turnId")
                    if isinstance(value := _path(raw, path), str) and value.strip()
                ),
                None,
            )
        if explicit is not None:
            payload["turn_id"] = explicit
            self._active_turn = None if kind == "turn.ended" else explicit
            payload["_codeplane_grouping"] = {"derived": False, "basis": "producer_turn"}
            return
        if self._active_turn is not None:
            payload["turn_id"] = self._active_turn
            payload["_codeplane_grouping"] = {"derived": True, "basis": "producer_turn"}
            return
        native = next(
            (
                (field, value)
                for field in ("request_id", "operation_id", "run_id")
                if isinstance(value := payload.get(field, raw.get(field)), str) and value.strip()
            ),
            None,
        )
        if native is not None:
            payload["turn_id"] = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    json.dumps(
                        [
                            "codeplane-source-operation-v1",
                            self._session_scope(),
                            self.framework,
                            self.external_id,
                            *native,
                        ],
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            )
            basis = native[0]
        else:
            payload["turn_id"] = self._turn_id()
            basis = "operator_message" if self._turn else "source_session"
        payload["_codeplane_grouping"] = {"derived": True, "basis": basis}

    def _source(
        self,
        raw: dict[str, Any],
        record_index: int,
        output_index: int,
        digest: str,
        *,
        timestamp_fallback: bool = False,
    ) -> dict[str, Any]:
        source = {
            "contract": "codeplane-native-v1" if self.legacy_identity else "codeplane-native-v2",
            "framework": self.framework,
            "session_id": self.external_id,
            "record_index": record_index,
            "output_index": output_index,
            "record_sha256": digest,
            "vendor_event_id": raw.get("id"),
        }
        if not self.legacy_identity:
            source.update(self._scope)
            source["timestamp_fallback"] = timestamp_fallback
        return source

    def decode(self, raw: dict[str, Any], record_index: int) -> tuple[SessionEvent, ...]:
        previous = self.checkpoint()
        try:
            return self._decode(raw, record_index)
        except (ValueError, TypeError, KeyError, RecursionError):
            self.restore(previous)
            raise

    def checkpoint(self) -> DecoderState:
        return DecoderState(self._intent, self._reasoning, tuple(self._references), self._turn, self._active_turn)

    def restore(self, state: DecoderState) -> None:
        if state.turn < 0 or len(state.references) > self._config.source_window:
            raise ValueError("Invalid decoder checkpoint")
        self._intent = state.intent
        self._reasoning = state.reasoning
        self._references = deque(state.references, maxlen=self._config.source_window)
        self._turn = state.turn
        self._active_turn = state.active_turn

    def _decode(self, raw: dict[str, Any], record_index: int) -> tuple[SessionEvent, ...]:
        if record_index < 0:
            raise ValueError("Physical source record index cannot be negative")
        canonical = canonical_record(raw)
        if len(canonical) > 1024 * 1024:
            raise ValueError("Source record exceeds 1 MiB")
        digest = hashlib.sha256(canonical).hexdigest()
        if self.mapping.spans and "name" in raw:
            from backend.services.ingest.otel_decoder import span_event

            event = span_event(
                raw,
                job_id=self.job_id,
                binding_id=self.binding_id or f"{self.framework}:{self.external_id}",
                source=self.framework,
                event_id=self._event_id(record_index, 0, digest),
                fallback_timestamp=self.anchor + timedelta(microseconds=record_index),
                mapping=self.mapping,
            )
            payload = dict(event.payload)
            payload["_codeplane_source"] = self._source(
                raw,
                record_index,
                0,
                digest,
                timestamp_fallback=not any(
                    raw.get(key) is not None
                    for key in (
                        "startTimeUnixNano",
                        "start_time_unix_nano",
                        "start_time",
                        "endTimeUnixNano",
                        "end_time_unix_nano",
                        "end_time",
                    )
                ),
            )
            if self.legacy_identity:
                payload.setdefault("turn_id", self._turn_id())
                if event.kind == "message.assistant":
                    self._turn += 1
            else:
                self._group_payload(payload, event.kind, raw)
            return (
                event.model_copy(
                    update={
                        "payload": payload,
                        "metadata": event.metadata.model_copy(
                            update={
                                "run_id": self.external_id,
                                "integration_binding_id": self.binding_id,
                            }
                        ),
                    }
                ),
            )
        normalized = self._preprocessor(json.loads(canonical)) if self._preprocessor is not None else [raw]
        if len(normalized) > 1000:
            raise ValueError("Source record expands beyond 1000 canonical events")
        output: list[SessionEvent] = []
        output_bytes = 0
        for obj in normalized:
            if not isinstance(obj, dict):
                raise ValueError("Preprocessor must return object records")
            raw_type = _path(obj, self.mapping.type_field)
            raw_kind = str(raw_type if raw_type is not None else "unknown")
            mapping = self.mapping.events.get(raw_kind)
            kind = mapping.kind if mapping is not None else self.mapping.default_kind
            if not kind and mapping is None:
                continue
            payload = {}
            if mapping is not None:
                for field, path in mapping.payload.items():
                    value = path[8:] if path.startswith("literal:") else _path(obj, path)
                    if value is not None:
                        payload[field] = value
            if kind == "raw" or mapping is None:
                payload["original_type"] = raw_kind
            index = len(output)
            stable_id = self._event_id(record_index, index, digest)
            for field, role in self._sources.get(raw_kind, ()):
                value = payload.get(field)
                text = (
                    "\n".join(str(item) for item in value if item)
                    if isinstance(value, list)
                    else (str(value) if value is not None else "")
                )
                if role == "intent":
                    self._intent = text if text.strip() else None
                else:
                    self._reasoning = text if text.strip() else None
            if raw_kind in self._sources:
                self._references.append(stable_id)
            motivation = (
                ToolMotivation(intent=self._intent, reasoning=self._reasoning, source_event_ids=tuple(self._references))
                if kind in self._config.targets and (self._intent or self._reasoning)
                else None
            )
            repo = _path(obj, self.mapping.repo_field) if self.mapping.repo_field else None
            raw_timestamp = _path(obj, self.mapping.timestamp_field) if self.mapping.timestamp_field else None
            timestamp = _timestamp(
                raw_timestamp,
                self.anchor + timedelta(microseconds=record_index),
            )
            payload["_codeplane_source"] = self._source(
                raw,
                record_index,
                index,
                digest,
                timestamp_fallback=raw_timestamp is None,
            )
            self._group_payload(payload, kind, obj)
            event = SessionEvent(
                id=stable_id,
                session_id=self.job_id,
                timestamp=timestamp,
                kind=kind,
                payload=payload,
                metadata=EventMetadata(
                    source_framework=self.mapping.framework,
                    ingestion_mode=self.mapping.ingestion_mode,
                    raw_kind=raw_kind,
                    run_id=self.external_id,
                    motivation=motivation,
                    integration_binding_id=self.binding_id,
                    repo=str(repo) if repo is not None else None,
                ),
            )
            output_bytes += len(event.model_dump_json().encode("utf-8"))
            if output_bytes > 8 * 1024 * 1024:
                raise ValueError("Source record exceeds the normalized event resource budget")
            output.append(event)
        return tuple(output)
