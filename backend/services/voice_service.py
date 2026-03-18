"""Local voice transcription via faster-whisper."""

from __future__ import annotations

import io

import structlog
from faster_whisper import WhisperModel

logger = structlog.get_logger()

_MODEL_NAME = "base.en"


class VoiceService:
    """Transcribes audio using faster-whisper locally.

    The model is loaded once and reused across requests.
    """

    def __init__(self) -> None:
        self._model_name = _MODEL_NAME
        self._model: WhisperModel | None = None

    def _ensure_model(self) -> WhisperModel:
        if self._model is None:
            logger.debug("voice_model_loading", model=self._model_name)
            self._model = WhisperModel(self._model_name, device="cpu", compute_type="int8")
            logger.debug("voice_model_loaded", model=self._model_name)
        return self._model

    def transcribe(self, audio_bytes: bytes) -> str:
        """Transcribe raw audio bytes and return the text."""
        model = self._ensure_model()
        segments, _ = model.transcribe(io.BytesIO(audio_bytes))
        return " ".join(seg.text.strip() for seg in segments)
