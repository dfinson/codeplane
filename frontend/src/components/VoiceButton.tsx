import { useState, useRef, useCallback, type ReactNode } from "react";
import { transcribeAudio } from "../api/client";

/** Maximum audio size in bytes (10 MB, matches backend default). */
const MAX_AUDIO_BYTES = 10 * 1024 * 1024;

interface VoiceButtonProps {
  /** Called when transcription completes with the resulting text. */
  onTranscript: (text: string) => void;
  disabled?: boolean;
}

/**
 * Press-and-hold microphone button for voice input.
 * Records audio via MediaRecorder when held, uploads on release,
 * and returns the transcribed text.
 */
export function VoiceButton({ onTranscript, disabled }: VoiceButtonProps): ReactNode {
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  const stopRecording = useCallback(() => {
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      recorderRef.current.stop();
    }
  }, []);

  const startRecording = useCallback(async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = async () => {
        // Stop all tracks
        stream.getTracks().forEach((t) => t.stop());
        streamRef.current = null;

        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        setRecording(false);

        if (blob.size === 0) return;

        if (blob.size > MAX_AUDIO_BYTES) {
          setError(`Recording exceeds ${MAX_AUDIO_BYTES / (1024 * 1024)} MB limit`);
          return;
        }

        setTranscribing(true);
        try {
          const text = await transcribeAudio(blob);
          if (text.trim()) onTranscript(text.trim());
        } catch {
          setError("Transcription failed");
        } finally {
          setTranscribing(false);
        }
      };

      recorder.start();
      setRecording(true);
    } catch {
      setError("Microphone access denied");
    }
  }, [onTranscript]);

  const isActive = recording || transcribing;

  return (
    <span className="voice-button-wrapper">
      <button
        type="button"
        className={`btn btn--sm voice-button${recording ? " voice-button--recording" : ""}`}
        onPointerDown={() => void startRecording()}
        onPointerUp={stopRecording}
        onPointerLeave={stopRecording}
        disabled={disabled || transcribing}
        title={recording ? "Release to stop" : "Hold to record"}
        aria-label={recording ? "Recording… release to stop" : "Hold to dictate"}
      >
        {transcribing ? "…" : "🎤"}
      </button>
      {isActive && (
        <span className="voice-indicator">
          {recording ? "Recording…" : "Transcribing…"}
        </span>
      )}
      {error && <span className="voice-error">{error}</span>}
      <span className="voice-local-badge" title="Audio is transcribed locally on your machine">
        Local transcription
      </span>
    </span>
  );
}
