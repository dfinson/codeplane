"""Local voice transcription via faster-whisper."""

from __future__ import annotations

import io

import structlog
from faster_whisper import WhisperModel

logger = structlog.get_logger()

ALLOWED_MODELS = frozenset(
    {
        "tiny",
        "tiny.en",
        "base",
        "base.en",
        "small",
        "small.en",
        "medium",
        "medium.en",
        "large-v2",
        "large-v3",
    }
)


class VoiceService:
    """Transcribes audio using faster-whisper locally.

    The model is loaded once and reused across requests.
    """

    def __init__(self, model_name: str = "base.en") -> None:
        if model_name not in ALLOWED_MODELS:
            raise ValueError(f"Invalid voice model '{model_name}'. Allowed: {sorted(ALLOWED_MODELS)}")
        self._model_name = model_name
        self._model: WhisperModel | None = None

    def _ensure_model(self) -> WhisperModel:
        if self._model is None:
            logger.info("voice_model_loading", model=self._model_name)
            self._model = WhisperModel(self._model_name, device="cpu", compute_type="int8")
            logger.info("voice_model_loaded", model=self._model_name)
        return self._model

    def transcribe(self, audio_bytes: bytes) -> str:
        """Transcribe raw audio bytes and return the text."""
        model = self._ensure_model()
        segments, _ = model.transcribe(io.BytesIO(audio_bytes))
        return " ".join(seg.text.strip() for seg in segments)
