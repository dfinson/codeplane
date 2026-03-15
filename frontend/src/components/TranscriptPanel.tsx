/**
 * Agent conversation / transcript panel.
 *
 * Custom component per spec — message list layout is product-specific.
 * Mantine used for shell (Paper, ScrollArea, TextInput, Button).
 */
import { useRef, useEffect, useState, useCallback } from "react";
import { Paper, Text, Group, TextInput, ActionIcon, ScrollArea } from "@mantine/core";
import { Send, Bot, User } from "lucide-react";
import { useTowerStore, selectJobTranscript } from "../store";
import { sendOperatorMessage } from "../api/client";
import { notifications } from "@mantine/notifications";
import { MicButton } from "./VoiceButton";

export function TranscriptPanel({ jobId, interactive }: { jobId: string; interactive?: boolean }) {
  const entries = useTowerStore(selectJobTranscript(jobId));
  const viewportRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const [msg, setMsg] = useState("");
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (stickRef.current && viewportRef.current) {
      viewportRef.current.scrollTo({ top: viewportRef.current.scrollHeight });
    }
  }, [entries.length]);

  const handleScroll = (pos: { x: number; y: number }) => {
    const el = viewportRef.current;
    if (el) stickRef.current = el.scrollHeight - pos.y - el.clientHeight < 40;
  };

  const handleSend = useCallback(async () => {
    if (!msg.trim()) return;
    setSending(true);
    try {
      await sendOperatorMessage(jobId, msg.trim());
      setMsg("");
    } catch (e) {
      notifications.show({ color: "red", title: "Send failed", message: String(e) });
    } finally {
      setSending(false);
    }
  }, [jobId, msg]);

  return (
    <Paper className="flex flex-col h-full overflow-hidden" radius="lg" p={0}>
      <Group
        justify="space-between"
        className="px-4 py-2.5 border-b border-[var(--mantine-color-dark-4)] shrink-0"
      >
        <Text size="sm" fw={600} c="dimmed">Transcript</Text>
        <Text size="xs" c="dimmed">{entries.length} messages</Text>
      </Group>

      <ScrollArea
        className="flex-1 min-h-0"
        viewportRef={viewportRef}
        onScrollPositionChange={handleScroll}
      >
        {entries.length === 0 ? (
          <Text size="sm" c="dimmed" ta="center" py="xl">No messages yet</Text>
        ) : (
          <div className="p-3 space-y-2">
            {entries.map((e, i) => {
              const isAgent = e.role === "agent";
              return (
                <div
                  key={i}
                  className={`flex gap-2 ${isAgent ? "" : "flex-row-reverse"}`}
                >
                  <div className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 mt-1 ${
                    isAgent ? "bg-blue-900/50" : "bg-green-900/50"
                  }`}>
                    {isAgent ? <Bot size={14} /> : <User size={14} />}
                  </div>
                  <div
                    className={`max-w-[80%] rounded-xl px-3 py-2 text-sm leading-relaxed ${
                      isAgent
                        ? "bg-[var(--mantine-color-dark-6)] rounded-tl-sm"
                        : "bg-blue-900/30 rounded-tr-sm"
                    }`}
                  >
                    <div className="whitespace-pre-wrap">{e.content}</div>
                    <Text size="xs" c="dimmed" mt={4}>
                      {new Date(e.timestamp).toLocaleTimeString()}
                    </Text>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </ScrollArea>

      {interactive && (
        <div className="p-2 border-t border-[var(--mantine-color-dark-4)] shrink-0">
          <Group gap="xs">
            <TextInput
              placeholder="Send instruction to agent…"
              value={msg}
              onChange={(e) => setMsg(e.currentTarget.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
              disabled={sending}
              className="flex-1"
              size="sm"
              rightSection={
                <MicButton
                  onTranscript={(t: string) => setMsg((prev) => (prev ? prev + " " : "") + t)}
                />
              }
              rightSectionWidth={40}
            />
            <ActionIcon
              variant="filled"
              size="lg"
              onClick={handleSend}
              disabled={sending || !msg.trim()}
              loading={sending}
            >
              <Send size={16} />
            </ActionIcon>
          </Group>
        </div>
      )}
    </Paper>
  );
}
