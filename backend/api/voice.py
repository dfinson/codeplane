"""Voice transcription endpoint."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["voice"])


# POST /api/voice/transcribe — Upload audio, receive transcript
