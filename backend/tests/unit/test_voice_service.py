"""Tests for VoiceService — transcription and model loading."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.services.voice_service import VoiceService


class TestVoiceServiceInit:
    def test_default_model(self) -> None:
        with patch("backend.services.voice_service.WhisperModel"):
            svc = VoiceService()
            assert svc._model_name == "base.en"

    def test_uses_module_default_model(self) -> None:
        with patch("backend.services.voice_service.WhisperModel"):
            svc = VoiceService()
            assert svc._model_name == "base.en"


class TestModelLoading:
    @patch("backend.services.voice_service.WhisperModel")
    def test_loads_model_once(self, mock_whisper_cls: MagicMock) -> None:
        mock_model = MagicMock()
        mock_whisper_cls.return_value = mock_model
        svc = VoiceService()
        svc._ensure_model()
        svc._ensure_model()  # Should not create a second instance
        mock_whisper_cls.assert_called_once()


class TestTranscribe:
    @patch("backend.services.voice_service.WhisperModel")
    def test_transcribes_audio(self, mock_whisper_cls: MagicMock) -> None:
        mock_segment = MagicMock()
        mock_segment.text = "hello world"
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_segment], MagicMock())
        mock_whisper_cls.return_value = mock_model

        svc = VoiceService()
        result = svc.transcribe(b"fake-audio-data")
        assert "hello world" in result

    @patch("backend.services.voice_service.WhisperModel")
    def test_empty_segments_returns_empty(self, mock_whisper_cls: MagicMock) -> None:
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([], MagicMock())
        mock_whisper_cls.return_value = mock_model

        svc = VoiceService()
        result = svc.transcribe(b"")
        assert result == ""
