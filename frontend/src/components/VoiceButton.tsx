import { useState, useRef, useCallback, useEffect, type RefObject } from "react";
import { Mic, Square } from "lucide-react";
import { toast } from "sonner";
import WaveSurfer from "wavesurfer.js";
import RecordPlugin from "wavesurfer.js/dist/plugins/record.esm.js";
import { transcribeAudio } from "../api/client";
import { Textarea } from "./ui/textarea";
import { Spinner } from "./ui/spinner";
import { Tooltip } from "./ui/tooltip";
import { useIsMobile } from "../hooks/useIsMobile";

type RecordingState = "idle" | "recording" | "transcribing";

interface PromptWithVoiceProps {
  value: string;
  onChange: (value: string) => void;
  error?: string;
  onBlur?: React.FocusEventHandler<HTMLTextAreaElement>;
  onKeyDown?: React.KeyboardEventHandler<HTMLTextAreaElement>;
}

export function PromptWithVoice({ value, onChange, error, onBlur, onKeyDown }: PromptWithVoiceProps) {
  const [state, setState] = useState<RecordingState>("idle");
  const isMobile = useIsMobile();
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
          <label className="text-sm font-medium text-foreground">
            Prompt<span className="text-red-500 ml-0.5">*</span>
          </label>
          <div className="relative">
            <Textarea
              value={value}
              onChange={(e) => onChange(e.currentTarget.value)}
              onBlur={onBlur}
              onKeyDown={onKeyDown}
              error={error}
              placeholder="Describe the task you want the agent to perform…"
              rows={isMobile ? 4 : 6}
              className="pr-12"
            />
            <div className="absolute bottom-3 right-3">
              <Tooltip content={state === "transcribing" ? "Transcribing" : "Voice input"}>
                <button
                  type="button"
                  onClick={handleToggle}
                  disabled={state === "transcribing"}
                  aria-label={state === "transcribing" ? "Transcribing audio" : "Voice input"}
                  className="h-8 w-8 rounded-full flex items-center justify-center text-muted-foreground transition-colors hover:text-foreground hover:bg-accent disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {state === "transcribing" ? <Spinner size="sm" /> : <Mic size={16} />}
                </button>
              </Tooltip>
            </div>
          </div>
          <p className="hidden sm:block text-xs text-muted-foreground mt-1">Ctrl+Enter to submit</p>
        </div>
      </div>

      {/* Recording mode — waveform + stop button */}
      {state === "recording" && (
        <div
          className="rounded-lg border border-blue-600 p-4 flex flex-col items-center gap-3 bg-card"
          style={{ minHeight: 120 }}
        >
          <Tooltip content="Stop recording">
            <button
              type="button"
              onClick={handleToggle}
              className="h-9 w-9 rounded-full bg-destructive text-destructive-foreground flex items-center justify-center hover:bg-destructive/80 transition-colors"
            >
              <Square size={18} />
            </button>
          </Tooltip>
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

interface MicButtonProps {
  onTranscript: (text: string) => void;
  /** Called when recording state changes — lets the parent show waveform / transcribing UI. */
  onStateChange?: (state: RecordingState) => void;
  /** A stable ref to the DOM element where WaveSurfer should render its waveform. */
  waveformContainerRef?: RefObject<HTMLDivElement>;
}

/**
 * Inline mic button for text inputs (e.g. transcript panel).
 * Records with WaveSurfer waveform visualization, then transcribes via the API.
 * Calls onTranscript with the result and onStateChange to let the parent show
 * the waveform strip and transcribing indicator.
 */
export function MicButton({ onTranscript, onStateChange, waveformContainerRef }: MicButtonProps) {
  const [state, setState] = useState<RecordingState>("idle");
  const recordRef = useRef<ReturnType<typeof RecordPlugin.create> | null>(null);
  const wsRef = useRef<WaveSurfer | null>(null);
  const initedRef = useRef(false);

  const updateState = useCallback((s: RecordingState) => {
    setState(s);
    onStateChange?.(s);
  }, [onStateChange]);

  // Initialise WaveSurfer once — requires the waveform container to be mounted.
  const ensureInit = useCallback(() => {
    if (initedRef.current) return;
    const container = waveformContainerRef?.current;
    if (!container) return;
    initedRef.current = true;

    const record = RecordPlugin.create({
      renderRecordedAudio: false,
      scrollingWaveform: true,
      scrollingWaveformWindow: 4,
    });

    const ws = WaveSurfer.create({
      container,
      waveColor: "#5180c6",
      progressColor: "#7196cf",
      height: 32,
      barWidth: 2,
      barGap: 2,
      barRadius: 2,
      plugins: [record],
    });

    record.on("record-end", async (blob: Blob) => {
      if (blob.size > 10 * 1024 * 1024) {
        toast.error("Audio too large (max 10 MB)");
        updateState("idle");
        return;
      }
      updateState("transcribing");
      try {
        const text = await transcribeAudio(blob);
        if (text) onTranscript(text);
      } catch {
        toast.error("Transcription failed");
      } finally {
        updateState("idle");
      }
    });

    wsRef.current = ws;
    recordRef.current = record;
  }, [waveformContainerRef, onTranscript, updateState]);

  useEffect(() => {
    return () => {
      recordRef.current?.destroy();
      wsRef.current?.destroy();
    };
  }, []);

  const handleClick = useCallback(async () => {
    if (state === "recording") {
      recordRef.current?.stopRecording();
      return;
    }
    if (state === "transcribing") return;

    ensureInit();
    if (!recordRef.current) {
      toast.error("Audio not ready, please try again");
      return;
    }
    try {
      await recordRef.current.startRecording();
      updateState("recording");
    } catch {
      toast.error("Microphone access denied");
    }
  }, [state, ensureInit, updateState]);

  return (
    <Tooltip content={state === "recording" ? "Stop recording" : "Voice input"}>
      <button
        type="button"
        onClick={handleClick}
        disabled={state === "transcribing"}
        aria-label={state === "recording" ? "Stop recording" : state === "transcribing" ? "Transcribing audio" : "Voice input"}
        className={`h-6 w-6 rounded-full flex items-center justify-center transition-colors ${
          state === "recording"
            ? "bg-destructive text-destructive-foreground hover:bg-destructive/80"
            : "text-muted-foreground hover:text-foreground hover:bg-accent"
        }`}
      >
        {state === "recording" ? <Square size={12} /> : <Mic size={14} />}
      </button>
    </Tooltip>
  );
}
