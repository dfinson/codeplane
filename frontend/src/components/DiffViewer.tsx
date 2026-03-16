import { useState, useEffect, useCallback } from "react";
import { type LucideIcon, FileCode, FilePlus, FileMinus, FileEdit, MessageSquare, Send, Lock } from "lucide-react";
import { DiffEditor } from "@monaco-editor/react";
import { toast } from "sonner";
import { useStore, selectJobDiffs } from "../store";
import { fetchJobDiff, sendOperatorMessage, resumeJob } from "../api/client";
import { useIsMobile } from "../hooks/useIsMobile";
import { Spinner } from "./ui/spinner";
import { Button } from "./ui/button";
import { cn } from "../lib/utils";
import type { DiffFileModel, DiffHunkModel } from "../api/types";

interface DiffViewerProps {
  jobId: string;
  jobState?: string;
  resolution?: string | null;
  archivedAt?: string | null;
}

const STATUS_ICON: Record<string, LucideIcon> = {
  added: FilePlus,
  deleted: FileMinus,
  modified: FileEdit,
  renamed: FileEdit,
};

const STATUS_BADGE: Record<string, string> = {
  added: "text-green-400 border-green-800",
  deleted: "text-red-400 border-red-800",
  modified: "text-blue-400 border-blue-800",
  renamed: "text-yellow-400 border-yellow-800",
};

const STATUS_ICON_CLASS: Record<string, string> = {
  added: "text-green-400",
  deleted: "text-red-400",
  modified: "text-blue-400",
  renamed: "text-yellow-400",
};

function guessLanguage(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  const map: Record<string, string> = {
    ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
    py: "python", rs: "rust", go: "go", java: "java", kt: "kotlin",
    rb: "ruby", php: "php", cs: "csharp", cpp: "cpp", c: "c", h: "c",
    json: "json", yaml: "yaml", yml: "yaml", toml: "toml",
    md: "markdown", html: "html", css: "css", scss: "scss",
    sql: "sql", sh: "shell", bash: "shell", dockerfile: "dockerfile",
  };
  return map[ext] ?? "plaintext";
}

/** Build a compact span reference for a file's hunks, e.g. "src/foo.ts:L10-L25,L40-L52" */
function fileSpanRef(file: DiffFileModel): string {
  const spans = file.hunks.map((h: DiffHunkModel) => {
    const start = h.newStart;
    const end = h.newStart + h.newLines - 1;
    return start === end ? `L${start}` : `L${start}-L${end}`;
  });
  return `${file.path}:${spans.join(",")}`;
}

/** Determine if the diff is "active" (agent can be asked) vs historical/resolved. */
function computeAskState(
  jobState?: string,
  resolution?: string | null,
  archivedAt?: string | null,
): { canAsk: boolean; reason: string | null } {
  if (archivedAt) return { canAsk: false, reason: "Archived" };
  if (jobState === "failed") return { canAsk: false, reason: "Job failed" };
  if (jobState === "canceled") return { canAsk: false, reason: "Job canceled" };
  if (resolution === "merged") return { canAsk: false, reason: "Already merged" };
  if (resolution === "discarded") return { canAsk: false, reason: "Changes discarded" };
  if (resolution === "pr_created") return { canAsk: false, reason: "PR created" };
  // Active: running, queued, succeeded+unresolved, succeeded+conflict
  return { canAsk: true, reason: null };
}

export default function DiffViewer({ jobId, jobState, resolution, archivedAt }: DiffViewerProps) {
  const diffs = useStore(selectJobDiffs(jobId));
  const isMobile = useIsMobile();
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [original, setOriginal] = useState("");
  const [modified, setModified] = useState("");
  const [loading, setLoading] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(256);
  const minSidebarWidth = 150;
  const maxSidebarWidth = 400;

  const handleResizeStart = (e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = sidebarWidth;
    const onMouseMove = (ev: MouseEvent) => {
      setSidebarWidth(Math.min(maxSidebarWidth, Math.max(minSidebarWidth, startWidth + ev.clientX - startX)));
    };
    const onMouseUp = () => {
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  };

  // Ask-about-diff state
  const [checkedFiles, setCheckedFiles] = useState<Set<number>>(new Set());
  const [askMsg, setAskMsg] = useState("");
  const [askSending, setAskSending] = useState(false);
  const { canAsk, reason: disabledReason } = computeAskState(jobState, resolution, archivedAt);
  const isTerminal = ["succeeded", "failed", "canceled"].includes(jobState ?? "");

  // Fetch historical diff from API on mount (for completed jobs / page refresh)
  useEffect(() => {
    fetchJobDiff(jobId)
      .then((files) => {
        if (files.length > 0) {
          useStore.setState((s) => ({
            diffs: { ...s.diffs, [jobId]: files },
          }));
        }
      })
      .catch(() => {});
  }, [jobId]);

  const selectedFile = diffs[selectedIdx];

  useEffect(() => {
    if (!selectedFile) return;
    setLoading(true);

    const additions = selectedFile.hunks
      ?.flatMap((h: { lines?: { type: string; content: string }[] }) =>
        (h.lines ?? []).filter((l: { type: string }) => l.type !== "deletion").map((l: { content: string }) => l.content),
      )
      .join("\n") ?? "";

    const deletions = selectedFile.hunks
      ?.flatMap((h: { lines?: { type: string; content: string }[] }) =>
        (h.lines ?? []).filter((l: { type: string }) => l.type !== "addition").map((l: { content: string }) => l.content),
      )
      .join("\n") ?? "";

    setOriginal(deletions);
    setModified(additions);
    setLoading(false);
  }, [selectedFile]);

  const toggleFile = useCallback((idx: number) => {
    setCheckedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  }, []);

  const handleAskSend = useCallback(async () => {
    if (!askMsg.trim() || checkedFiles.size === 0) return;
    const refs = Array.from(checkedFiles)
      .sort()
      .map((idx) => diffs[idx])
      .filter((f): f is DiffFileModel => f != null)
      .map((f) => fileSpanRef(f));

    const contextPrefix = `[Re: changes in ${refs.join("; ")}]\n\n`;
    const fullMessage = contextPrefix + askMsg.trim();

    setAskSending(true);
    try {
      if (isTerminal) {
        await resumeJob(jobId, fullMessage);
      } else {
        await sendOperatorMessage(jobId, fullMessage);
      }
      toast.success("Question sent to agent");
      setAskMsg("");
      setCheckedFiles(new Set());
    } catch (e) {
      toast.error(String(e));
    } finally {
      setAskSending(false);
    }
  }, [jobId, askMsg, checkedFiles, diffs, isTerminal]);

  const totalAdditions = diffs.reduce((sum, f) => sum + (f.additions ?? 0), 0);
  const totalDeletions = diffs.reduce((sum, f) => sum + (f.deletions ?? 0), 0);

  if (diffs.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-8 text-center">
        <p className="text-sm text-muted-foreground">No changes detected</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col md:flex-row gap-3 md:gap-0 h-[60vh] min-h-[300px] max-h-[600px]">
        {/* File list sidebar */}
        <div
          className="shrink-0 flex flex-col overflow-hidden rounded-lg border border-border bg-card max-md:max-h-[30%]"
          style={isMobile ? undefined : { width: sidebarWidth }}
        >
          <div className="flex items-center justify-between px-3 py-2.5 border-b border-border">
            <span className="text-xs font-semibold text-muted-foreground">{diffs.length} files</span>
            <div className="flex items-center gap-2">
              <span className="text-xs text-green-400">+{totalAdditions}</span>
              <span className="text-xs text-red-400">-{totalDeletions}</span>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto">
            {diffs.map((file, i) => {
              const Icon = STATUS_ICON[file.status] ?? FileCode;
              const checked = checkedFiles.has(i);
              return (
                <div
                  key={i}
                  className={cn(
                    "flex items-center gap-1.5 px-2 py-2 text-sm transition-colors w-full",
                    i === selectedIdx ? "bg-accent" : "hover:bg-accent/50",
                  )}
                >
                  {/* Checkbox — visible when ask is active, disabled placeholder when not */}
                  {canAsk ? (
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleFile(i)}
                      className="shrink-0 accent-primary w-3.5 h-3.5 cursor-pointer"
                      title="Select to ask about this file's changes"
                    />
                  ) : (
                    <span className="shrink-0 w-3.5" />
                  )}
                  <button
                    type="button"
                    onClick={() => setSelectedIdx(i)}
                    className="flex items-center gap-2 flex-1 min-w-0 text-left"
                  >
                    <Icon size={14} className={cn("shrink-0", STATUS_ICON_CLASS[file.status])} />
                    <span className="text-xs truncate flex-1 text-foreground" title={file.path}>
                      {isMobile ? (file.path.split("/").pop() ?? file.path) : file.path}
                    </span>
                    <span className={cn("text-xs border rounded px-1 hidden sm:inline", STATUS_BADGE[file.status])}>
                      +{file.additions} -{file.deletions}
                    </span>
                  </button>
                </div>
              );
            })}
          </div>
        </div>

        {/* Resize handle — desktop only */}
        {!isMobile && (
          <div
            className="hidden md:flex items-center justify-center w-1.5 shrink-0 cursor-col-resize rounded-full bg-border hover:bg-primary/60 transition-colors active:bg-primary"
            onMouseDown={handleResizeStart}
          />
        )}

        {/* Monaco Diff Editor */}
        <div className="flex-1 min-h-0 overflow-hidden rounded-lg border border-border bg-card">
          {loading ? (
            <div className="flex items-center justify-center h-full">
              <Spinner />
            </div>
          ) : selectedFile ? (
            <DiffEditor
              original={original}
              modified={modified}
              language={guessLanguage(selectedFile.path)}
              theme="vs-dark"
              options={{
                readOnly: true,
                minimap: { enabled: false },
                renderSideBySide: !isMobile,
                scrollBeyondLastLine: false,
                fontSize: isMobile ? 12 : 13,
                lineNumbersMinChars: isMobile ? 2 : 3,
                glyphMargin: false,
                lineDecorationsWidth: isMobile ? 2 : 4,
                folding: !isMobile,
              }}
            />
          ) : null}
        </div>
      </div>

      {/* Ask-about-diff bar */}
      {canAsk && checkedFiles.size > 0 && (
        <div className="flex items-end gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 animate-in slide-in-from-bottom-2 duration-200">
          <MessageSquare size={16} className="text-primary shrink-0 mb-1" />
          <span className="text-xs text-muted-foreground shrink-0 mb-1">
            {checkedFiles.size} file{checkedFiles.size > 1 ? "s" : ""} selected
          </span>
          <textarea
            placeholder="Ask about these changes…"
            value={askMsg}
            onChange={(e) => {
              setAskMsg(e.currentTarget.value);
              e.currentTarget.style.height = "auto";
              e.currentTarget.style.height = Math.min(e.currentTarget.scrollHeight, 160) + "px";
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !isMobile && !e.shiftKey) {
                e.preventDefault();
                handleAskSend();
              }
            }}
            disabled={askSending}
            rows={1}
            className="flex-1 rounded-md border border-input bg-transparent px-3 py-1.5 text-sm text-foreground shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 resize-none overflow-y-auto"
            style={{ maxHeight: 160 }}
          />
          <Button
            size="sm"
            onClick={handleAskSend}
            disabled={askSending || !askMsg.trim()}
            loading={askSending}
            className="h-8 gap-1 shrink-0"
          >
            <Send size={14} />
            Ask
          </Button>
        </div>
      )}

      {/* Disabled state hint */}
      {!canAsk && (
        <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2">
          <Lock size={14} className="text-muted-foreground shrink-0" />
          <span className="text-xs text-muted-foreground">
            {disabledReason} — asking about changes is only available for pending diffs
          </span>
        </div>
      )}
    </div>
  );
}
