"""Voice transcription endpoint."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, UploadFile

from backend.models.api_schemas import TranscribeResponse

if TYPE_CHECKING:
    from backend.services.voice_service import VoiceService

router = APIRouter(tags=["voice"])

ALLOWED_AUDIO_TYPES = frozenset({"audio/webm", "audio/ogg", "audio/wav", "audio/mpeg", "audio/mp4", "audio/x-wav"})

_transcribe_semaphore = asyncio.Semaphore(2)


@router.post("/voice/transcribe", response_model=TranscribeResponse)
async def transcribe(request: Request, audio: UploadFile) -> TranscribeResponse:
    """Upload audio, receive transcript."""
    voice_service: VoiceService = request.app.state.voice_service

    if voice_service is None:
        raise HTTPException(status_code=501, detail="Voice transcription is disabled")

    # Validate content type (allow codec params like audio/webm;codecs=opus)
    if audio.content_type:
        base_type = audio.content_type.split(";")[0].strip()
        if base_type not in ALLOWED_AUDIO_TYPES:
            raise HTTPException(status_code=415, detail=f"Unsupported audio format: {audio.content_type}")

    # Stream-read with early abort on size limit
    max_bytes: int = request.app.state.voice_max_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await audio.read(64 * 1024)  # 64 KB chunks
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Audio exceeds {max_bytes // (1024 * 1024)} MB limit",
            )
        chunks.append(chunk)
    data = b"".join(chunks)

    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty audio file")

    # Concurrency-limited, off-event-loop transcription
    if _transcribe_semaphore._value == 0:  # noqa: SLF001
        raise HTTPException(status_code=429, detail="Transcription busy, try again later")

    async with _transcribe_semaphore:
        text = await asyncio.to_thread(voice_service.transcribe, data)

    return TranscribeResponse(text=text)
