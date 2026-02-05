"""Session management for CodePlane MCP server.

Handles session lifecycle, state tracking, and task binding per Spec §23.3.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from codeplane.config.models import TimeoutsConfig


@dataclass
class SessionState:
    """State for a single session."""

    session_id: str
    created_at: float
    last_active: float
    task_id: str | None = None
    fingerprints: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    def touch(self) -> None:
        """Update last active timestamp."""
        self.last_active = time.time()


class SessionManager:
    """Manages active sessions."""

    def __init__(self, config: TimeoutsConfig | None = None) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._config = config or TimeoutsConfig()

    def get_or_create(self, session_id: str | None = None) -> SessionState:
        """Get existing session or create new one.

        Args:
            session_id: Optional session ID. If None, creates new session.

        Returns:
            SessionState object
        """
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            session.touch()
            return session

        # Create new session
        new_id = session_id or f"sess_{uuid.uuid4().hex[:12]}"
        session = SessionState(
            session_id=new_id,
            created_at=time.time(),
            last_active=time.time(),
        )
        self._sessions[new_id] = session
        return session

    def get(self, session_id: str) -> SessionState | None:
        """Get session if exists."""
        return self._sessions.get(session_id)

    def close(self, session_id: str) -> None:
        """Close a session."""
        if session_id in self._sessions:
            del self._sessions[session_id]

    def cleanup_stale(self) -> int:
        """Remove stale sessions.

        Returns:
            Number of sessions removed
        """
        now = time.time()
        to_remove = [
            sid
            for sid, s in self._sessions.items()
            if now - s.last_active > self._config.session_idle_sec
        ]
        for sid in to_remove:
            del self._sessions[sid]
        return len(to_remove)
