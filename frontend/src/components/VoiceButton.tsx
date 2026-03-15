import { useState, useRef, useCallback, useEffect } from "react";
import { Mic, Square } from "lucide-react";
import { toast } from "sonner";
import WaveSurfer from "wavesurfer.js";
import RecordPlugin from "wavesurfer.js/dist/plugins/record.esm.js";
import { transcribeAudio } from "../api/client";
import { Textarea } from "./ui/textarea";
import { Spinner } from "./ui/spinner";

type RecordingState = "idle" | "recording" | "transcribing";

interface PromptWithVoiceProps {
  value: string;
  onChange: (value: string) => void;
}

export function PromptWithVoice({ value, onChange }: PromptWithVoiceProps) {
  const [state, setState] = useState<RecordingState>("idle");
  const containerRef = useRef<HTMLDivElement>(null);
  const recordRef = useRef<ReturnType<typeof RecordPlugin.create> | null>(null);
  const wsRef = useRef<WaveSurfer | null>(null);
  const initedRef = useRef(false);
  const valueRef = useRef(value);
  useEffect(() => { valueRef.current = value; }, [value]);

  const ensureInit = useCallback(() => {
    if (initedRef.current || !containerRef.current) return;
    initedRef.current = true;

    const record = RecordPlugin.create({
      renderRecordedAudio: false,
      scrollingWaveform: true,
      scrollingWaveformWindow: 4,
    });

    const ws = WaveSurfer.create({
      container: containerRef.current,
      waveColor: "#5180c6",
      progressColor: "#7196cf",
      height: 40,
      barWidth: 3,
      barGap: 2,
      barRadius: 3,
      plugins: [record],
    });

    record.on("record-end", async (blob: Blob) => {
      if (blob.size > 10 * 1024 * 1024) {
        toast.error("Audio too large (max 10 MB)");
        setState("idle");
        return;
      }
      setState("transcribing");
      try {
        const text = await transcribeAudio(blob);
        if (text) {
          const cur = valueRef.current;
          onChange(cur ? cur + " " + text : text);
          toast.success("Transcribed");
        }
      } catch {
        toast.error("Transcription failed");
      } finally {
        setState("idle");
      }
    });

    wsRef.current = ws;
    recordRef.current = record;
  }, [onChange]);

  useEffect(() => {
    return () => {
      recordRef.current?.destroy();
      wsRef.current?.destroy();
    };
  }, []);

  const handleToggle = useCallback(async () => {
    ensureInit();
    const record = recordRef.current;
    if (!record) return;

    if (state === "recording") {
      record.stopRecording();
      return;
    }

    try {
      await record.startRecording();
      setState("recording");
    } catch {
      toast.error("Microphone access denied");
    }
  }, [state, ensureInit]);

  return (
    <div className="relative">
      {/* Normal textarea — visible when NOT recording */}
      <div style={{ display: state === "recording" ? "none" : "block" }}>
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium text-foreground">Prompt</label>
          <Textarea
            value={value}
            onChange={(e) => onChange(e.currentTarget.value)}
            placeholder="Describe the task you want the agent to perform…"
            rows={6}
            className="pr-12"
          />
        </div>

        <div className="absolute bottom-2 right-2" style={{ zIndex: 10 }}>
          {state === "transcribing" ? (
            <Spinner size="sm" />
          ) : (
            <button
              type="button"
              onClick={handleToggle}
              title="Voice input"
              className="h-9 w-9 rounded-full bg-primary/20 text-primary flex items-center justify-center hover:bg-primary/30 transition-colors"
            >
              <Mic size={18} />
            </button>
          )}
        </div>
      </div>

      {/* Recording mode — waveform + stop button */}
      {state === "recording" && (
        <div
          className="rounded-lg border border-blue-600 p-4 flex flex-col items-center gap-3 bg-card"
          style={{ minHeight: 120 }}
        >
          <button
            type="button"
            onClick={handleToggle}
            title="Stop recording"
            className="h-9 w-9 rounded-full bg-destructive text-destructive-foreground flex items-center justify-center hover:bg-destructive/80 transition-colors"
          >
            <Square size={18} />
          </button>
        </div>
      )}

      {/* Single waveform container — always mounted, positioned based on state.
          WaveSurfer needs a stable DOM element that doesn't unmount. */}
      <div
        ref={containerRef}
        style={
          state === "recording"
            ? { marginTop: -60, marginBottom: 8, padding: "0 16px" }
            : { width: 0, height: 0, overflow: "hidden", position: "absolute" }
        }
      />
    </div>
  );
}

/**
 * Simple inline mic button for text inputs (e.g. transcript panel).
 * Records, transcribes, calls onTranscript with the text.
 */
export function MicButton({ onTranscript }: { onTranscript: (text: string) => void }) {
  const [busy, setBusy] = useState(false);
  const mediaRef = useRef<MediaRecorder | null>(null);

  const handleClick = useCallback(async () => {
    if (busy) {
      mediaRef.current?.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
      const chunks: Blob[] = [];
      recorder.ondataavailable = (e) => chunks.push(e.data);
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: "audio/webm" });
        try {
          const text = await transcribeAudio(blob);
          if (text) onTranscript(text);
        } catch {
          toast.error("Transcription failed");
        }
        setBusy(false);
      };
      recorder.start();
      mediaRef.current = recorder;
      setBusy(true);
    } catch {
      toast.error("Microphone access denied");
    }
  }, [busy, onTranscript]);

  return (
    <button
      type="button"
      onClick={handleClick}
      title={busy ? "Stop" : "Voice input"}
      className={`h-6 w-6 rounded-full flex items-center justify-center transition-colors ${
        busy
          ? "bg-destructive text-destructive-foreground hover:bg-destructive/80"
          : "text-muted-foreground hover:text-foreground hover:bg-accent"
      }`}
    >
      {busy ? <Square size={12} /> : <Mic size={14} />}
    </button>
  );
}
