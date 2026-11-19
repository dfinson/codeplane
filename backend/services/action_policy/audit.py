"""Bounded AGT audit delivery from the durable governance outbox."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import stat
import sys
from typing import TYPE_CHECKING

from backend.persistence.governance_repo import AuditDecision, AuditHead, GovernanceLedger

if TYPE_CHECKING:
    from pathlib import Path

    from agentmesh.governance.audit_backends import SignedAuditEntry

_SEGMENT_BYTES = 4 * 1024 * 1024
_RECORD_BYTES = 64 * 1024
_KEY_ID = re.compile(r"[0-9a-f]{32}")


class AuditIntegrityError(RuntimeError):
    """Delivery must stop rather than replace or silently repair audit evidence."""


class GovernanceAudit:
    def __init__(self, directory: Path, ledger: GovernanceLedger) -> None:
        self._directory = directory
        self._ledger = ledger

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self._directory.stat()
        if sys.platform == "win32":
            from backend.services.action_policy.windows_audit import private_directory, reject_reparse

            reject_reparse(self._directory)
            private_directory(self._directory)
        elif not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise AuditIntegrityError("Audit directory must be private to the service owner")
        if not (self._directory / "active-key").exists():
            key_id = self._new_key()
            # Concurrent initialization never replaces the selected key or either secret.
            with contextlib.suppress(FileExistsError):
                self._create(self._directory / "active-key", key_id.encode("ascii"))
        self._key(self._active_key())

    def _sync_directory(self) -> None:
        if sys.platform == "win32":
            # Each file is flushed with fsync; Windows does not expose POSIX directory fsync.
            return
        descriptor = os.open(self._directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _create(self, path: Path, data: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        self._sync_directory()

    @staticmethod
    def _read(path: Path, limit: int) -> bytes:
        if sys.platform == "win32":
            from backend.services.action_policy.windows_audit import reject_reparse

            reject_reparse(path)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or (
                sys.platform != "win32" and (info.st_uid != os.geteuid() or info.st_mode & 0o077)
            ):
                raise AuditIntegrityError("Audit files must be private regular files owned by the service")
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise AuditIntegrityError("Audit file exceeds its resource bound")
        return data

    def _new_key(self) -> str:
        key_id = secrets.token_hex(16)
        key = secrets.token_bytes(32)
        if sys.platform == "win32":
            from backend.services.action_policy.windows_audit import protect

            key = protect(key)
        self._create(self._directory / f"{key_id}.key", key)
        return key_id

    def _active_key(self) -> str:
        value = self._read(self._directory / "active-key", 32).decode("ascii")
        if _KEY_ID.fullmatch(value) is None:
            raise AuditIntegrityError("Invalid active audit key identifier")
        return value

    def _key(self, key_id: str) -> bytes:
        if _KEY_ID.fullmatch(key_id) is None:
            raise AuditIntegrityError("Invalid audit key identifier")
        key = self._read(self._directory / f"{key_id}.key", 4096 if sys.platform == "win32" else 32)
        if sys.platform == "win32":
            from backend.services.action_policy.windows_audit import protect

            key = protect(key, decrypt=True)
        if len(key) != 32:
            raise AuditIntegrityError("Audit signing requires a 256-bit key")
        return key

    async def rotate_key(self) -> str:
        """Select a new key without replacing old keys or signed segments."""
        return await asyncio.to_thread(self._rotate_key)

    def _rotate_key(self) -> str:
        self._active_key()
        key_id = self._new_key()
        temporary = self._directory / f"active-key-{key_id}.new"
        self._create(temporary, key_id.encode("ascii"))
        os.replace(temporary, self._directory / "active-key")
        self._sync_directory()
        return key_id

    def _path(self, segment: int) -> Path:
        if segment < 0:
            raise AuditIntegrityError("Invalid audit segment")
        return self._directory / f"audit-{segment:016d}.jsonl"

    def _entries(self, segment: int) -> list[SignedAuditEntry]:
        from agentmesh.governance.audit_backends import SignedAuditEntry

        data = self._read(self._path(segment), _SEGMENT_BYTES + _RECORD_BYTES)
        entries: list[SignedAuditEntry] = []
        previous = ""
        key_id: str | None = None
        for line in data.splitlines():
            if not line or len(line) > _RECORD_BYTES:
                raise AuditIntegrityError("Invalid audit record boundary")
            entry = SignedAuditEntry.model_validate_json(line)
            candidate = entry.data.get("key_id")
            if not isinstance(candidate, str) or (key_id is not None and candidate != key_id):
                raise AuditIntegrityError("Audit segment key identity changed")
            key_id = candidate
            if entry.previous_hash != previous or not entry.verify(self._key(key_id)):
                raise AuditIntegrityError("Audit signature or chain verification failed")
            entries.append(entry)
            previous = entry.content_hash
        if data and not data.endswith(b"\n"):
            raise AuditIntegrityError("Audit segment has an incomplete tail")
        return entries

    def _append(self, segment: int, key_id: str, event_type: str, data: dict[str, object]) -> SignedAuditEntry:
        from agentmesh.governance.audit import AuditLog
        from agentmesh.governance.audit_backends import FileAuditSink

        sink = FileAuditSink(self._path(segment), self._key(key_id), max_file_size=0)
        try:
            # AuditLog retains all entries in memory. One instance per record
            # keeps that bounded; the real FileAuditSink owns the durable chain.
            entry = AuditLog().log(
                event_type,
                "codeplane:policy",
                "record_decision",
                data=data,
                outcome="recorded",
                policy_decision=str(data.get("decision", "segment")),
            )
            entry = entry.model_copy(update={"sandbox_id": None, "environment": None, "compute_driver": None})
            if len(json.dumps(entry.model_dump(mode="json"), ensure_ascii=True).encode("utf-8")) > 60 * 1024:
                raise AuditIntegrityError("Audit envelope exceeds its record bound")
            sink.write(entry)
            descriptor = os.open(self._path(segment), os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            retained = self._entries(segment)[-1]
            if retained.entry_id != entry.entry_id:
                raise AuditIntegrityError("Audit append identity changed")
            return retained
        finally:
            sink.close()

    @staticmethod
    def _decision_data(decision: AuditDecision, key_id: str) -> dict[str, object]:
        return {
            "version": 1,
            "decision_id": decision.id,
            "job_id": decision.job_id,
            "epoch": decision.epoch,
            "action_digest": decision.action_digest,
            "decision": decision.decision,
            "metadata": decision.metadata,
            "key_id": key_id,
        }

    def _new_segment(self, segment: int, previous_hash: str) -> tuple[str, list[SignedAuditEntry]]:
        path = self._path(segment)
        if not path.exists():
            self._create(path, b"")
        entries = self._entries(segment)
        if not entries:
            key_id = self._active_key()
            self._append(
                segment,
                key_id,
                "audit.segment",
                {
                    "version": 1,
                    "segment": segment,
                    "previous_segment_hash": previous_hash,
                    "key_id": key_id,
                },
            )
            entries = self._entries(segment)
        header = entries[0]
        if (
            header.event_type != "audit.segment"
            or header.data.get("segment") != segment
            or header.data.get("previous_segment_hash") != previous_hash
        ):
            raise AuditIntegrityError("Audit segment continuity changed")
        return str(header.data["key_id"]), entries

    def _write(self, decision: AuditDecision, head: AuditHead | None) -> AuditHead:
        if head is None:
            segment = 0
            key_id, entries = self._new_segment(segment, "")
            pending = entries[1:]
        else:
            segment, key_id = head.segment, head.key_id
            entries = self._entries(segment)
            if (
                not entries
                or entries[0].event_type != "audit.segment"
                or entries[0].data.get("segment") != segment
                or entries[0].data.get("key_id") != key_id
            ):
                raise AuditIntegrityError("Durable audit segment identity changed")
            matches = [index for index, entry in enumerate(entries) if entry.content_hash == head.content_hash]
            if len(matches) != 1:
                raise AuditIntegrityError("Durable audit head is absent from its segment")
            pending = entries[matches[0] + 1 :]
            if not pending and (self._active_key() != key_id or self._path(segment).stat().st_size >= _SEGMENT_BYTES):
                segment += 1
                key_id, entries = self._new_segment(segment, head.content_hash)
                pending = entries[1:]
        data = self._decision_data(decision, key_id)
        if len(json.dumps(data, ensure_ascii=True).encode("utf-8")) > 48 * 1024:
            raise AuditIntegrityError("Signed decision exceeds its record bound")
        if pending:
            if len(pending) != 1 or pending[0].event_type != "policy.decision" or pending[0].data != data:
                raise AuditIntegrityError("Unacknowledged audit tail does not match the pending decision")
            return AuditHead(segment, key_id, pending[0].content_hash)
        retained = self._append(segment, key_id, "policy.decision", data)
        return AuditHead(segment, key_id, retained.content_hash)

    async def deliver_one(self) -> bool:
        """Replay only audit delivery, never the authorized business action."""
        return await self._ledger.deliver_audit(self._write)
