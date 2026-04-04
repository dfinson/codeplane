import { useState, useEffect, useCallback, useRef, useLayoutEffect } from "react";
import { useNavigate } from "react-router-dom";
import { type LucideIcon, FileCode, FilePlus, FileMinus, FileEdit, MessageSquare, Send, Lock, Check, Minus } from "lucide-react";
import { DiffEditor } from "@monaco-editor/react";
import { toast } from "sonner";
import { useStore, selectJobDiffs } from "../store";
import { sendOperatorMessage, resumeJob, continueJob } from "../api/client";
import { useIsMobile } from "../hooks/useIsMobile";
import { Spinner } from "./ui/spinner";
import { Button } from "./ui/button";
import { cn } from "../lib/utils";
import { MicButton } from "./VoiceButton";
import { Tooltip } from "./ui/tooltip";
import { useDrag } from "../hooks/useDrag";
import type { DiffFileModel, DiffHunkModel } from "../api/types";

interface DiffViewerProps {
  jobId: string;
  jobState?: string;
  resolution?: string | null;
  archivedAt?: string | null;
  onAskSent?: () => void;
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

/**
 * Displays a file path truncated from the left by path segment when it overflows.
 * Always shows the full path if it fits; otherwise drops leading segments and
 * prepends "…/" until it fits (or only the filename remains).
 */
function TruncatedPath({ path }: { path: string }) {
  const containerRef = useRef<HTMLSpanElement>(null);
  const [displayPath, setDisplayPath] = useState(path);

  const computeTruncation = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;

    const segments = path.split("/");

    for (let start = 0; start < segments.length; start++) {
      const candidate =
        start === 0 ? path : "\u2026/" + segments.slice(start).join("/");
      // Probe the width by temporarily setting textContent
      el.textContent = candidate;
      if (el.scrollWidth <= el.offsetWidth + 1 || start === segments.length - 1) {
        setDisplayPath(candidate);
        return;
      }
    }
  }, [path]);

  useLayoutEffect(() => {
    computeTruncation();
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(computeTruncation);
    ro.observe(el);
    return () => ro.disconnect();
  }, [computeTruncation]);

  return (
    <span
      ref={containerRef}
      className="text-xs flex-1 min-w-0 overflow-hidden whitespace-nowrap text-foreground"
      title={path}
    >
      {displayPath}
    </span>
  );
}

/** Determine if the diff is askable; historical jobs create follow-up jobs. */
function computeAskState(): { canAsk: boolean; reason: string | null } {
  // Active jobs accept an operator message. Historical terminal jobs create
  // a follow-up job instead of mutating the original job in place.
  return { canAsk: true, reason: null };
}

export default function DiffViewer({ jobId, jobState, onAskSent }: DiffViewerProps) {
  const navigate = useNavigate();
  const diffs = useStore(selectJobDiffs(jobId));
  const isMobile = useIsMobile();
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [original, setOriginal] = useState("");
  const [modified, setModified] = useState("");
  const [loading, setLoading] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(256);
  const minSidebarWidth = 150;
  const maxSidebarWidth = 400;

  const dragHandlers = useDrag({
    axis: "x",
    onDrag: (delta) => {
      setSidebarWidth(Math.min(maxSidebarWidth, Math.max(minSidebarWidth, sidebarWidth - delta)));
    },
  });

  // Ask-about-diff state — tracked per hunk (key: "fileIdx:hunkIdx")
  const [checkedHunks, setCheckedHunks] = useState<Set<string>>(new Set());
  const [askMsg, setAskMsg] = useState("");
  const [askSending, setAskSending] = useState(false);
  const { canAsk, reason: disabledReason } = computeAskState();
  const isTerminal = ["review", "completed", "failed", "canceled"].includes(jobState ?? "");

  const hunkKey = (fi: number, hi: number) => `${fi}:${hi}`;

  const isFileFullyChecked = useCallback(
    (fi: number) => {
      const f = diffs[fi];
      return f != null && f.hunks.length > 0 && f.hunks.every((_, hi) => checkedHunks.has(hunkKey(fi, hi)));
    },
    [diffs, checkedHunks],
  );

  const isFilePartiallyChecked = useCallback(
    (fi: number) => {
      const f = diffs[fi];
      if (!f) return false;
      const n = f.hunks.filter((_, hi) => checkedHunks.has(hunkKey(fi, hi))).length;
      return n > 0 && n < f.hunks.length;
    },
    [diffs, checkedHunks],
  );

  // Voice input state
  const waveformContainerRef = useRef<HTMLDivElement>(null);
  const [micState, setMicState] = useState<"idle" | "recording" | "transcribing">("idle");

  // Monaco editor refs for glyph-margin hunk checkboxes
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const diffEditorRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const monacoRef = useRef<any>(null);
  const decorationIdsRef = useRef<string[]>([]);
  const [hunkLineRanges, setHunkLineRanges] = useState<{ startLine: number; endLine: number }[]>([]);

  // Refs so the glyph-margin click handler always reads current state
  const checkedHunksRef = useRef(checkedHunks);
  checkedHunksRef.current = checkedHunks;
  const selectedIdxRef = useRef(selectedIdx);
  selectedIdxRef.current = selectedIdx;
  const hunkLineRangesRef = useRef(hunkLineRanges);
  hunkLineRangesRef.current = hunkLineRanges;

  // NOTE: diff data is fetched by JobDetailScreen and stored in the Zustand
  // store — no need to duplicate the fetch here.

  const selectedFile = diffs[selectedIdx];

  useEffect(() => {
    if (!selectedFile) return;
    setLoading(true);

    const modifiedParts: string[] = [];
    const originalParts: string[] = [];
    const ranges: { startLine: number; endLine: number }[] = [];
    let lineOffset = 1;

    for (const h of selectedFile.hunks) {
      const lines = h.lines ?? [];
      const nonDel = lines.filter((l) => l.type !== "deletion");
      const nonAdd = lines.filter((l) => l.type !== "addition");
      ranges.push({ startLine: lineOffset, endLine: lineOffset + Math.max(nonDel.length - 1, 0) });
      lineOffset += nonDel.length;
      modifiedParts.push(...nonDel.map((l) => l.content));
      originalParts.push(...nonAdd.map((l) => l.content));
    }

    setOriginal(originalParts.join("\n"));
    setModified(modifiedParts.join("\n"));
    setHunkLineRanges(ranges);
    setLoading(false);
  }, [selectedFile]);

  const toggleFile = useCallback(
    (fi: number) => {
      setCheckedHunks((prev) => {
        const next = new Set(prev);
        const f = diffs[fi];
        if (!f) return next;
        const full = f.hunks.every((_, hi) => next.has(hunkKey(fi, hi)));
        f.hunks.forEach((_, hi) => {
          const k = hunkKey(fi, hi);
          if (full) next.delete(k);
          else next.add(k);
        });
        return next;
      });
    },
    [diffs],
  );

  const toggleHunk = useCallback((fi: number, hi: number) => {
    setCheckedHunks((prev) => {
      const next = new Set(prev);
      const k = hunkKey(fi, hi);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  }, []);

  // Inject CSS for glyph-margin checkbox icons (runs once)
  useEffect(() => {
    const id = "hunk-cb-styles";
    if (document.getElementById(id)) return;
    const style = document.createElement("style");
    style.id = id;
    style.textContent = [
      ".hunk-cb-unchecked, .hunk-cb-checked { cursor: pointer !important; }",
      ".hunk-cb-unchecked {",
      "  background: url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none'%3E%3Crect x='1.5' y='1.5' width='13' height='13' rx='2.5' stroke='rgba(180,180,200,0.7)' stroke-width='2'/%3E%3C/svg%3E\") center center / 16px no-repeat;",
      "}",
      ".hunk-cb-checked {",
      "  background: url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none'%3E%3Crect x='0.5' y='0.5' width='15' height='15' rx='2.5' fill='%230e639c'/%3E%3Cpath d='M4 8L7 11L12 5' stroke='white' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E\") center center / 16px no-repeat;",
      "}",
      ".hunk-selected-line { background: rgba(14,99,156,0.12) !important; }",
      ".hunk-selected-line-margin { border-left: 3px solid rgba(14,99,156,0.7) !important; }",
    ].join("\n");
    document.head.appendChild(style);
    return () => { style.remove(); };
  }, []);

  // Sync glyph-margin decorations whenever selection or file changes
  useEffect(() => {
    const editor = diffEditorRef.current;
    const m = monacoRef.current;
    if (!editor || !m) return;
    const modifiedEditor = editor.getModifiedEditor();
    if (!canAsk || hunkLineRanges.length === 0) {
      decorationIdsRef.current = modifiedEditor.deltaDecorations(decorationIdsRef.current, []);
      return;
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const newDecorations: any[] = [];
    hunkLineRanges.forEach((range, hi) => {
      const checked = checkedHunks.has(hunkKey(selectedIdx, hi));
      // Glyph checkbox on the first line of each hunk
      newDecorations.push({
        range: new m.Range(range.startLine, 1, range.startLine, 1),
        options: {
          glyphMarginClassName: checked ? "hunk-cb-checked" : "hunk-cb-unchecked",
          glyphMarginHoverMessage: { value: "Toggle hunk selection" },
        },
      });
      // Background tint + left-border accent across all lines of checked hunks
      if (checked) {
        newDecorations.push({
          range: new m.Range(range.startLine, 1, range.endLine, 1),
          options: {
            className: "hunk-selected-line",
            marginClassName: "hunk-selected-line-margin",
            isWholeLine: true,
          },
        });
      }
    });
    decorationIdsRef.current = modifiedEditor.deltaDecorations(decorationIdsRef.current, newDecorations);
  }, [selectedIdx, checkedHunks, hunkLineRanges, canAsk]);

  // DiffEditor mount handler — wires the glyph-margin click listener
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleEditorMount = useCallback((editor: any, monaco: any) => {
    diffEditorRef.current = editor;
    monacoRef.current = monaco;
    const modifiedEditor = editor.getModifiedEditor();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    modifiedEditor.onMouseDown((e: any) => {
      if (e.target.type !== monaco.editor.MouseTargetType.GUTTER_GLYPH_MARGIN) return;
      // Prevent Monaco from focusing/scrolling on glyph clicks
      e.event?.preventDefault?.();
      e.event?.stopPropagation?.();
      const lineNumber = e.target.position?.lineNumber;
      if (lineNumber == null) return;
      const ranges = hunkLineRangesRef.current;
      const fi = selectedIdxRef.current;
      for (let hi = 0; hi < ranges.length; hi++) {
        const r = ranges[hi];
        if (r && lineNumber >= r.startLine && lineNumber <= r.endLine) {
          toggleHunk(fi, hi);
          // Blur editor to prevent keyboard popup on mobile / scroll jump on desktop
          (document.activeElement as HTMLElement)?.blur?.();
          break;
        }
      }
    });
  }, [toggleHunk]);

  const handleAskSend = useCallback(async () => {
    if (!askMsg.trim() || checkedHunks.size === 0) return;

    // Group checked hunks by file index
    const fileHunks = new Map<number, number[]>();
    for (const key of checkedHunks) {
      const parts = key.split(":");
      const fi = Number(parts[0]);
      const hi = Number(parts[1]);
      const arr = fileHunks.get(fi) ?? [];
      arr.push(hi);
      fileHunks.set(fi, arr);
    }

    const refs: string[] = [];
    for (const [fi, his] of fileHunks) {
      const file = diffs[fi];
      if (!file) continue;
      // If all hunks selected, use the full file span shorthand
      if (his.length === file.hunks.length) {
        refs.push(fileSpanRef(file));
      } else {
        const spans = his
          .sort((a, b) => a - b)
          .map((hi) => {
            const h = file.hunks[hi];
            if (!h) return null;
            const start = h.newStart;
            const end = h.newStart + h.newLines - 1;
            return start === end ? `L${start}` : `L${start}-L${end}`;
          })
          .filter(Boolean);
        refs.push(`${file.path}:${spans.join(",")}`);
      }
    }

    const contextPrefix = `[Re: changes in ${refs.join("; ")}]\n\n`;
    const fullMessage = contextPrefix + askMsg.trim();

    setAskSending(true);
    try {
      if (isTerminal) {
        try {
          await resumeJob(jobId, fullMessage);
        } catch {
          // Worktree gone / unrecoverable — fall back to follow-up job
          const nextJob = await continueJob(jobId, fullMessage);
          toast.success("Follow-up job created");
          navigate(`/jobs/${nextJob.id}`);
        }
      } else {
        await sendOperatorMessage(jobId, fullMessage);
      }
      toast.success("Question sent to agent");
      setAskMsg("");
      setCheckedHunks(new Set());
      onAskSent?.();
    } catch (e) {
      toast.error(String(e));
    } finally {
      setAskSending(false);
    }
  }, [jobId, askMsg, checkedHunks, diffs, isTerminal, navigate, onAskSent]);

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
      <div className="flex flex-col md:flex-row gap-3 md:gap-0 h-[calc(100vh-14rem)] md:h-[60vh] min-h-[300px] max-h-[600px]">
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
              const fileChecked = isFileFullyChecked(i);
              const filePartial = isFilePartiallyChecked(i);
              return (
                <div key={i} className="flex flex-col">
                  <div
                    className={cn(
                      "flex items-center gap-1.5 px-2 py-2 text-sm transition-colors w-full",
                      i === selectedIdx ? "bg-accent" : "hover:bg-accent/50",
                    )}
                  >
                    {/* File checkbox — tri-state: unchecked / partial (minus) / fully checked */}
                    {canAsk ? (
                      <Tooltip content="Select to ask about this file's changes">
                        <button
                          type="button"
                          onClick={() => toggleFile(i)}
                          className={cn(
                            "shrink-0 w-5 h-5 md:w-3.5 md:h-3.5 rounded-[3px] border flex items-center justify-center transition-colors cursor-pointer",
                            fileChecked || filePartial
                              ? "bg-primary border-primary text-primary-foreground"
                              : "border-muted-foreground/40 hover:border-muted-foreground",
                          )}
                        >
                          {fileChecked && <Check size={12} strokeWidth={3} />}
                          {filePartial && <Minus size={12} strokeWidth={3} />}
                        </button>
                      </Tooltip>
                    ) : (
                      <span className="shrink-0 w-5 md:w-3.5" />
                    )}
                    <button
                      type="button"
                      onClick={() => setSelectedIdx(i)}
                      className="flex items-center gap-2 flex-1 min-w-0 text-left"
                    >
                      <Icon size={14} className={cn("shrink-0", STATUS_ICON_CLASS[file.status])} />
                      <TruncatedPath path={file.path} />
                      <span className={cn("text-xs border rounded px-1 hidden sm:inline", STATUS_BADGE[file.status])}>
                        +{file.additions} -{file.deletions}
                      </span>
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Resize handle — desktop only */}
        {!isMobile && (
          <div
            className="hidden md:flex items-center justify-center w-1.5 shrink-0 cursor-col-resize rounded-full bg-border hover:bg-primary/60 transition-colors active:bg-primary"
            {...dragHandlers}
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
              onMount={handleEditorMount}
              options={{
                readOnly: true,
                minimap: { enabled: false },
                renderSideBySide: !isMobile,
                scrollBeyondLastLine: false,
                fontSize: isMobile ? 12 : 13,
                lineNumbersMinChars: 3,
                glyphMargin: canAsk,
                lineDecorationsWidth: 4,
                folding: true,
              }}
            />
          ) : null}
        </div>
      </div>

      {/* Ask-about-diff bar */}
      {canAsk && checkedHunks.size > 0 && (
        <div className="flex flex-col gap-1.5 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 animate-in slide-in-from-bottom-2 duration-200">
          <div className="flex items-center gap-2">
            <MessageSquare size={16} className="text-primary shrink-0" />
            <span className="text-xs text-muted-foreground">
              {checkedHunks.size} hunk{checkedHunks.size !== 1 ? "s" : ""} selected
              {" across "}
              {new Set(Array.from(checkedHunks).map((k) => k.split(":")[0])).size}
              {" file"}
              {new Set(Array.from(checkedHunks).map((k) => k.split(":")[0])).size !== 1 ? "s" : ""}
            </span>
          </div>

          {/* Waveform strip — always mounted for WaveSurfer stability, shown only during recording */}
          <div className={cn(
            "rounded border border-blue-600/50 bg-card px-3 py-1",
            micState === "recording" ? "block" : "hidden",
          )}>
            <div ref={waveformContainerRef} />
          </div>

          {/* Transcribing indicator */}
          {micState === "transcribing" && (
            <div className="flex items-center gap-2 px-1 text-sm text-muted-foreground">
              <Spinner size="sm" />
              <span>Transcribing…</span>
            </div>
          )}

          <div className="flex items-end gap-2">
            <div className="relative flex-1">
              <textarea
                placeholder="Ask about these changes…"
                value={askMsg}
                onChange={(e) => {
                  setAskMsg(e.currentTarget.value);
                  e.currentTarget.style.height = "auto";
                  e.currentTarget.style.height = Math.min(e.currentTarget.scrollHeight, isMobile ? 240 : 160) + "px";
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !isMobile && !e.shiftKey) {
                    e.preventDefault();
                    handleAskSend();
                  }
                }}
                disabled={askSending || micState !== "idle"}
                rows={1}
                className="flex w-full rounded-md border border-input bg-transparent px-3 py-1.5 text-sm text-foreground shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 resize-none pr-8 overflow-y-auto"
                style={{ maxHeight: isMobile ? 240 : 160 }}
              />
              <div className="absolute right-2 bottom-1.5">
                <MicButton
                  onTranscript={(t) => setAskMsg((prev) => (prev ? prev + " " : "") + t)}
                  onStateChange={setMicState}
                  waveformContainerRef={waveformContainerRef}
                />
              </div>
            </div>
            <Button
              size="sm"
              onClick={handleAskSend}
              disabled={askSending || !askMsg.trim() || micState !== "idle"}
              loading={askSending}
              className="h-8 gap-1 shrink-0"
            >
              <Send size={14} />
              Ask
            </Button>
          </div>
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
