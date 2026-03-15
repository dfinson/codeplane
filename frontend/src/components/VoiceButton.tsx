/**
 * VoiceRecorder — polished prompt input with voice recording.
 *
 * Idle: textarea with mic button anchored bottom-right.
 * Recording: waveform + stop button replace the textarea content.
 * Transcribing: loading spinner, then text inserted.
 */
import { useState, useRef, useCallback, useEffect } from "react";
import { ActionIcon, Loader, Textarea } from "@mantine/core";
import { Mic, Square } from "lucide-react";
import WaveSurfer from "wavesurfer.js";
import RecordPlugin from "wavesurfer.js/dist/plugins/record.esm.js";
import { transcribeAudio } from "../api/client";
import { notifications } from "@mantine/notifications";

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
        notifications.show({ color: "red", message: "Audio too large (max 10 MB)" });
        setState("idle");
        return;
      }
      setState("transcribing");
      try {
        const text = await transcribeAudio(blob);
        if (text) {
          onChange(value ? value + " " + text : text);
          notifications.show({ color: "green", message: "Transcribed" });
        }
      } catch {
        notifications.show({ color: "red", message: "Transcription failed" });
      } finally {
        setState("idle");
      }
    });

    wsRef.current = ws;
    recordRef.current = record;
  }, [onChange, value]);

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
      notifications.show({ color: "red", message: "Microphone access denied" });
    }
  }, [state, ensureInit]);

  return (
    <div className="relative">
      {/* Normal textarea — visible when NOT recording */}
      <div style={{ display: state === "recording" ? "none" : "block" }}>
        <Textarea
          label="Prompt"
          value={value}
          onChange={(e) => onChange(e.currentTarget.value)}
          placeholder="Describe the task you want the agent to perform (e.g. Refactor the login flow to remove deprecated auth code)."
          minRows={4}
          autosize
          maxRows={12}
          size="sm"
          styles={{
            input: { paddingRight: 52 },
          }}
        />

        {/* Mic button — anchored bottom-right of textarea */}
        <div className="absolute bottom-2 right-2" style={{ zIndex: 10 }}>
          {state === "transcribing" ? (
            <Loader size={24} />
          ) : (
            <ActionIcon
              variant="light"
              color="blue"
              size="xl"
              radius="xl"
              onClick={handleToggle}
              title="Voice input"
            >
              <Mic size={22} />
            </ActionIcon>
          )}
        </div>
      </div>

      {/* Recording mode — replaces textarea content */}
      {state === "recording" && (
        <div
          className="rounded-lg border border-blue-600 p-4 flex flex-col items-center gap-3"
          style={{
            background: "var(--mantine-color-dark-7)",
            minHeight: 120,
          }}
        >
          <div className="text-xs font-medium text-blue-400 uppercase tracking-wider">
            Recording…
          </div>

          {/* Waveform — centered, full width */}
          <div
            ref={containerRef}
            className="w-full"
            style={{ minHeight: 40 }}
          />

          {/* Stop button — integrated with waveform */}
          <ActionIcon
            variant="filled"
            color="red"
            size="xl"
            radius="xl"
            onClick={handleToggle}
            title="Stop recording"
          >
            <Square size={18} />
          </ActionIcon>
        </div>
      )}

      {/* Hidden waveform container when not recording (for WaveSurfer init) */}
      {state !== "recording" && (
        <div ref={containerRef} style={{ width: 0, height: 0, overflow: "hidden" }} />
      )}
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
          notifications.show({ color: "red", message: "Transcription failed" });
        }
        setBusy(false);
      };
      recorder.start();
      mediaRef.current = recorder;
      setBusy(true);
    } catch {
      notifications.show({ color: "red", message: "Microphone access denied" });
    }
  }, [busy, onTranscript]);

  return (
    <ActionIcon
      variant={busy ? "filled" : "subtle"}
      color={busy ? "red" : "gray"}
      size="sm"
      radius="xl"
      onClick={handleClick}
      title={busy ? "Stop" : "Voice input"}
    >
      {busy ? <Square size={12} /> : <Mic size={14} />}
    </ActionIcon>
  );
}
