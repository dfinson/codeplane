import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { FileCode, MessageSquare, Send, Lock, Filter, X, Lightbulb, FolderOpen, Download } from "lucide-react";
import { DiffEditor } from "@monaco-editor/react";
import { toast } from "sonner";
import { useStore, selectJobDiffs } from "../store";
import { sendOperatorMessage, resumeJob, continueJob, fetchStepDiff, fetchJobTelemetry, fetchDiffFile } from "../api/client";
import { useIsMobile } from "../hooks/useIsMobile";
import { Spinner } from "./ui/spinner";
import { Tooltip } from "./ui/tooltip";
import { Button } from "./ui/button";
import { cn } from "../lib/utils";
import { MicButton } from "./VoiceButton";

import { useDrag } from "../hooks/useDrag";
import { useMotivationLayers } from "../hooks/useMotivationLayers";
import { useLayeredDiffStyles } from "../hooks/useLayeredDiffStyles";
import type { DiffFileModel, DiffHunkModel, FileMotivation, HunkMotivation, StepDiffResponse } from "../api/types";
import { BottomSheet } from "./ui/bottom-sheet";
import { DiffViewerFileList, STATUS_ICON, STATUS_BADGE, STATUS_ICON_CLASS } from "./DiffViewerFileList";

export interface StepFilter {
  /** Relative file paths that belong to this step */
  filePaths: string[];
  /** Human-readable label, e.g. "Edited models.py, views.py" */
  label: string;
  /** Transcript entry seq to scroll back to in the feed */
  scrollToSeq?: number;
  /** SDK turn ID — used to fetch the exact step diff from the API */
  turnId?: string;
}

interface DiffViewerProps {
  jobId: string;
  jobState?: string;
  resolution?: string | null;
  archivedAt?: string | null;
  onAskSent?: () => void;
  stepFilter?: StepFilter | null;
  onClearStepFilter?: () => void;
  onNavigateToStep?: (seq: number, turnId?: string) => void;
}

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

export default function DiffViewer({ jobId, jobState, onAskSent, stepFilter, onClearStepFilter, onNavigateToStep }: DiffViewerProps) {
  const navigate = useNavigate();
  const recipeRun = useStore((state) => state.recipeCurrentRuns[jobId]);
  const allDiffs = useStore(selectJobDiffs(jobId));
  const isMobile = useIsMobile();
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [showAllChanges, setShowAllChanges] = useState(false);
  const [mobileFilePickerOpen, setMobileFilePickerOpen] = useState(false);

  // Step-specific diffs fetched from the API (when turnId is available)
  const [stepDiffs, setStepDiffs] = useState<import("../api/types").DiffFileModel[] | null>(null);
  const [stepDiffsLoading, setStepDiffsLoading] = useState(false);

  // Motivation data from the step-diff API
  const [stepContext, setStepContext] = useState<string | null>(null);
  const [fileMotivations, setFileMotivations] = useState<Record<string, FileMotivation>>({});
  const [hunkMotivations, setHunkMotivations] = useState<Record<string, HunkMotivation>>({});
  const [showIntent, setShowIntent] = useState(true);

  // WS7: Anti-skim — track which files the reviewer has viewed
  const [viewedFiles, setViewedFiles] = useState<Set<number>>(new Set());

  // WS2: Blast radius — context files read but not written
  const [contextFiles, setContextFiles] = useState<{ filePath: string; readCount: number }[]>([]);

  // WS1: Sort by churn toggle
  const [sortByChurn, setSortByChurn] = useState(false);

  // Split (side-by-side) vs unified (single) diff view
  const [splitView, setSplitView] = useState(false);


  // Lazy-loaded truncated file diffs (keyed by file path)
  const [loadedTruncatedFiles, setLoadedTruncatedFiles] = useState<Record<string, import("../api/types").DiffFileModel>>({});
  const [truncatedFileLoading, setTruncatedFileLoading] = useState<string | null>(null);

  const loadTruncatedFile = useCallback(async (filePath: string) => {
    setTruncatedFileLoading(filePath);
    try {
      const full = await fetchDiffFile(jobId, filePath);
      setLoadedTruncatedFiles((prev) => ({ ...prev, [filePath]: full }));
    } catch {
      toast.error(`Failed to load diff for ${filePath}`);
    } finally {
      setTruncatedFileLoading(null);
    }
  }, [jobId]);

  // Fetch step-specific diff from API when filter has a turnId
  useEffect(() => {
    if (!stepFilter?.turnId || showAllChanges) {
      setStepDiffs(null);
      setStepContext(null);
      setFileMotivations({});
      setHunkMotivations({});
      return;
    }
    let cancelled = false;
    setStepDiffsLoading(true);
    fetchStepDiff(jobId, stepFilter.turnId)
      .then((res: StepDiffResponse) => {
        const files = res.changedFiles ?? [];
        if (!cancelled) {
          setStepDiffs(files.length > 0 ? files : null);
          setStepContext(res.stepContext ?? null);
          setFileMotivations(res.fileMotivations ?? {});
          setHunkMotivations(res.hunkMotivations ?? {});
        }
      })
      .catch(() => {
        if (!cancelled) {
          setStepDiffs(null);
          setStepContext(null);
          setFileMotivations({});
          setHunkMotivations({});
        }
      })
      .finally(() => {
        if (!cancelled) setStepDiffsLoading(false);
      });
    return () => { cancelled = true; };
  }, [jobId, stepFilter?.turnId, showAllChanges]);

  // Fetch telemetry for blast radius (context files) + review signals
  useEffect(() => {
    let cancelled = false;
    fetchJobTelemetry(jobId)
      .then((telem) => {
        if (cancelled) return;
        // WS2: Extract read-only context files (read but never written)
        const topFiles = (telem as Record<string, unknown>).fileAccess as { topFiles?: { filePath: string; readCount: number; writeCount: number }[] } | undefined;
        if (topFiles?.topFiles) {
          setContextFiles(
            topFiles.topFiles
              .filter((f) => f.writeCount === 0 && f.readCount > 0)
              .map((f) => ({ filePath: f.filePath, readCount: f.readCount })),
          );
        }

      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [jobId]);

  // Filter diffs when step filter is active and not toggled to "all"
  const isFiltered = !!stepFilter && !showAllChanges;
  const diffs = useMemo(() => {
    let result: DiffFileModel[];
    if (!isFiltered || !stepFilter) {
      result = allDiffs;
    } else if (stepDiffs !== null) {
      result = stepDiffs;
    } else {
      const filterPaths = new Set(stepFilter.filePaths);
      result = allDiffs.filter((f) =>
        filterPaths.has(f.path) ||
        stepFilter.filePaths.some((fp) => f.path.endsWith(fp) || fp.endsWith(f.path)),
      );
    }
    // WS1: optionally sort by churn (write_count descending)
    if (sortByChurn) {
      return [...result].sort((a, b) => (b.writeCount ?? 0) - (a.writeCount ?? 0));
    }
    return result;
  }, [allDiffs, stepFilter, isFiltered, stepDiffs, sortByChurn]);

  // WS7: Mark file as viewed when selected
  useEffect(() => {
    if (diffs.length > 0 && selectedIdx >= 0 && selectedIdx < diffs.length) {
      setViewedFiles((prev) => {
        if (prev.has(selectedIdx)) return prev;
        const next = new Set(prev);
        next.add(selectedIdx);
        return next;
      });
    }
  }, [selectedIdx, diffs.length]);

  // Reset selection when filter changes
  useEffect(() => { setSelectedIdx(0); }, [isFiltered, stepFilter]);
  // Reset toggle when filter is cleared externally
  useEffect(() => { if (!stepFilter) { setShowAllChanges(false); setStepDiffs(null); setStepContext(null); setFileMotivations({}); setHunkMotivations({}); } }, [stepFilter]);

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
  const canAsk = recipeRun === null;
  const disabledReason = recipeRun === undefined
    ? "Execution identity is still loading. No message or Resume will be sent."
    : "Recipe questions must target an exact live attempt. Use the Run inspector, or explicit Resume for a new run.";
  const isReview = jobState === "review";
  const isTerminal = ["completed", "failed", "canceled"].includes(jobState ?? "");
  const needsResume = isReview || isTerminal;

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
  const [editorReady, setEditorReady] = useState(false);
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

  const rawSelectedFile = diffs[selectedIdx];
  // Use the lazily-loaded full version if available for truncated files
  const selectedFile = rawSelectedFile?.truncated && loadedTruncatedFiles[rawSelectedFile.path]
    ? loadedTruncatedFiles[rawSelectedFile.path]
    : rawSelectedFile;

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
      "  background: url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none'%3E%3Crect x='1' y='1' width='14' height='14' rx='2.5' stroke='rgba(180,180,200,0.9)' stroke-width='2'/%3E%3C/svg%3E\") center center / 18px no-repeat;",
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

    // Apply decorations after a short delay to let Monaco process new content
    const applyDecorations = () => {
      const ed = diffEditorRef.current?.getModifiedEditor();
      if (!ed) return;
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
      decorationIdsRef.current = ed.deltaDecorations(decorationIdsRef.current, newDecorations);
    };

    // Apply immediately and again after a tick (Monaco may need time to process new models)
    applyDecorations();
    const timer = setTimeout(applyDecorations, 100);
    return () => clearTimeout(timer);
  }, [selectedIdx, checkedHunks, hunkLineRanges, canAsk, modified, editorReady]);

  // V8: Inject layered diff CSS
  useLayeredDiffStyles();

  // V8: Motivation layer — already partially wired via showIntent; add job-level fallback
  useMotivationLayers({
    jobId,
    file: selectedFile,
    enabled: showIntent,
    editorRef: diffEditorRef,
    editorReady,
    stepFileMotivations: Object.keys(fileMotivations).length > 0 ? fileMotivations : undefined,
    stepHunkMotivations: Object.keys(hunkMotivations).length > 0 ? hunkMotivations : undefined,
  });

  // DiffEditor mount handler — wires the glyph-margin click listener
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleEditorMount = useCallback((editor: any, monaco: any) => {
    diffEditorRef.current = editor;
    monacoRef.current = monaco;
    setEditorReady(true);
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
    if (!canAsk || !askMsg.trim() || checkedHunks.size === 0) return;

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
      if (needsResume) {
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
  }, [jobId, askMsg, checkedHunks, diffs, needsResume, navigate, onAskSent, canAsk]);

  const totalAdditions = diffs.reduce((sum, f) => sum + (f.additions ?? 0), 0);
  const totalDeletions = diffs.reduce((sum, f) => sum + (f.deletions ?? 0), 0);

  if (diffs.length === 0 && !stepFilter) {
    return (
      <div className="rounded-lg border border-border bg-card p-8 text-center">
        <p className="text-sm text-muted-foreground">No changes detected</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 md:h-full md:min-h-0">
      {/* Step filter banner */}
      {stepFilter && (
        <div className="flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2">
          <Filter size={14} className="text-primary shrink-0" />
          <span className="text-xs text-muted-foreground">
            {isFiltered ? (
              <>
                Showing changes from:{" "}
                {stepFilter.scrollToSeq != null && onNavigateToStep ? (
                  <button
                    onClick={() => onNavigateToStep(stepFilter.scrollToSeq!, stepFilter.turnId)}
                    className="text-primary hover:text-primary/80 underline underline-offset-2 decoration-primary/40 hover:decoration-primary transition-colors"
                  >
                    {stepFilter.label}
                  </button>
                ) : (
                  <span className="text-foreground/80">{stepFilter.label}</span>
                )}
              </>
            ) : "Showing all changes"}
          </span>
          <div className="ml-auto flex items-center gap-1.5 shrink-0">
            <button
              onClick={() => setShowAllChanges(!showAllChanges)}
              className={cn(
                "px-2 py-1 md:py-0.5 rounded text-[11px] font-medium transition-colors min-h-[44px] md:min-h-0",
                isFiltered
                  ? "bg-primary/15 text-primary border border-primary/30"
                  : "bg-muted/30 text-muted-foreground hover:bg-accent/40 border border-transparent",
              )}
            >
              Step Only
            </button>
            <button
              onClick={() => setShowAllChanges(!showAllChanges)}
              className={cn(
                "px-2 py-1 md:py-0.5 rounded text-[11px] font-medium transition-colors min-h-[44px] md:min-h-0",
                !isFiltered
                  ? "bg-primary/15 text-primary border border-primary/30"
                  : "bg-muted/30 text-muted-foreground hover:bg-accent/40 border border-transparent",
              )}
            >
              All Changes
            </button>
            <Tooltip content="Clear step filter — show all changes">
              <button
                onClick={onClearStepFilter}
                className="p-1.5 min-h-[44px] md:min-h-0 min-w-[44px] md:min-w-0 flex items-center justify-center text-muted-foreground/50 hover:text-muted-foreground transition-colors ml-1"
              >
                <X size={13} />
              </button>
            </Tooltip>
          </div>
        </div>
      )}

      {/* Layer 1: Step context banner — shows WHY this step was performed */}
      {stepContext && isFiltered && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2">
          <Lightbulb size={14} className="text-amber-400 shrink-0 mt-0.5" />
          <p className="text-xs text-muted-foreground leading-relaxed line-clamp-3">{stepContext}</p>
        </div>
      )}

      {stepDiffsLoading && isFiltered && (
        <div className="flex justify-center py-6">
          <Spinner />
        </div>
      )}

      {!stepDiffsLoading && diffs.length === 0 && stepFilter && (
        <div className="rounded-lg border border-border bg-card p-8 text-center">
          <p className="text-sm text-muted-foreground">No matching changes for this step</p>
        </div>
      )}

      {diffs.length > 0 && (
      <>
      {/* Mobile file picker bottom sheet */}
      {isMobile && (
        <BottomSheet open={mobileFilePickerOpen} onClose={() => setMobileFilePickerOpen(false)} title={`${diffs.length} files  +${totalAdditions} -${totalDeletions}`}>
          <div className="flex flex-col -mx-4">
            {diffs.map((file, i) => {
              const Icon = STATUS_ICON[file.status] ?? FileCode;
              return (
                <button
                  key={i}
                  type="button"
                  onClick={() => { setSelectedIdx(i); setMobileFilePickerOpen(false); }}
                  className={cn(
                    "flex items-center gap-2 px-4 py-3 text-sm transition-colors w-full min-h-[44px]",
                    i === selectedIdx ? "bg-accent" : "hover:bg-accent/50",
                  )}
                >
                  <Icon size={14} className={cn("shrink-0", STATUS_ICON_CLASS[file.status])} />
                  <span className="flex-1 min-w-0 text-left truncate text-foreground">{file.path}</span>
                  <span className={cn("text-xs border rounded px-1 shrink-0", STATUS_BADGE[file.status])}>
                    +{file.additions} -{file.deletions}
                  </span>
                </button>
              );
            })}
          </div>
        </BottomSheet>
      )}
      <div className="flex flex-col md:flex-row gap-3 md:gap-0 h-[calc(100dvh-14rem)] md:h-full min-h-[300px]">
        {/* File list sidebar — hidden on mobile, replaced by bottom sheet */}
        <DiffViewerFileList
          diffs={diffs}
          selectedIdx={selectedIdx}
          setSelectedIdx={setSelectedIdx}
          sidebarWidth={sidebarWidth}
          canAsk={canAsk}
          sortByChurn={sortByChurn}
          setSortByChurn={setSortByChurn}
          splitView={splitView}
          setSplitView={setSplitView}
          fileMotivations={fileMotivations}
          viewedFiles={viewedFiles}
          contextFiles={contextFiles}
          totalAdditions={totalAdditions}
          totalDeletions={totalDeletions}
          isFileFullyChecked={isFileFullyChecked}
          isFilePartiallyChecked={isFilePartiallyChecked}
          toggleFile={toggleFile}
        />

        {/* Resize handle — desktop only */}
        {!isMobile && (
          <div
            className="hidden md:flex items-center justify-center w-1.5 shrink-0 cursor-col-resize rounded-full bg-border hover:bg-primary/60 transition-colors active:bg-primary"
            {...dragHandlers}
          />
        )}

        {/* Monaco Diff Editor */}
        <div className="flex-1 min-h-0 overflow-hidden rounded-lg border border-border bg-card relative flex flex-col">
          {/* V8: Layer toggles toolbar — sliding switches matching mockup */}
          <div className="flex items-center gap-4 px-3 py-1.5 border-b border-border bg-[#2d2d2d] shrink-0">
            <button onClick={() => setShowIntent(!showIntent)} className="flex items-center gap-[5px] text-[11px] text-[#a0a0a0] hover:text-[#d4d4d4] cursor-pointer select-none">
              <span className={cn("relative w-6 h-[13px] rounded-[7px] transition-colors", showIntent ? "bg-[#569cd6]" : "bg-[#555]")}>
                <span className={cn("absolute top-[2px] w-[9px] h-[9px] bg-[#e0e0e0] rounded-full transition-[left]", showIntent ? "left-[13px]" : "left-[2px]")} />
              </span>
              <span>Motivation</span>
            </button>
            <span className="flex-1" />
            <span className="text-[11px] text-[#808080]">{selectedFile?.path}</span>
          </div>
          {loading ? (
            <div className="flex items-center justify-center h-full">
              <Spinner />
            </div>
          ) : selectedFile?.truncated && !loadedTruncatedFiles[selectedFile.path] ? (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
              <FileCode size={32} className="opacity-50" />
              <p className="text-sm">Large file diff not loaded</p>
              <Button
                size="sm"
                variant="outline"
                disabled={truncatedFileLoading === selectedFile.path}
                onClick={() => loadTruncatedFile(selectedFile.path)}
              >
                {truncatedFileLoading === selectedFile.path ? (
                  <Spinner className="mr-2 h-3 w-3" />
                ) : (
                  <Download size={13} className="mr-2" />
                )}
                Load diff ({selectedFile.rawSize ? `${Math.round(selectedFile.rawSize / 1024)} KB` : "large"})
              </Button>
            </div>
          ) : selectedFile ? (
            <>
            <DiffEditor
              original={original}
              modified={modified}
              language={guessLanguage(selectedFile.path)}
              theme="vs-dark"
              onMount={handleEditorMount}
              options={{
                readOnly: true,
                domReadOnly: true,
                minimap: { enabled: !isMobile },
                renderSideBySide: splitView && !isMobile,
                scrollBeyondLastLine: false,
                fontSize: isMobile ? 12 : 13,
                lineNumbersMinChars: 3,
                glyphMargin: true,
                lineDecorationsWidth: 4,
                folding: true,
              }}
            />
            {/* Mobile floating file picker button */}
            {isMobile && (
              <button
                onClick={() => setMobileFilePickerOpen(true)}
                className="absolute top-2 right-2 z-10 flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-border bg-card/90 backdrop-blur-sm text-xs font-medium text-muted-foreground hover:text-foreground shadow-sm transition-colors"
              >
                <FolderOpen size={13} />
                <span className="max-w-[120px] truncate">{selectedFile.path.split("/").pop()}</span>
                <span className="text-muted-foreground/60">{selectedIdx + 1}/{diffs.length}</span>
              </button>
            )}
            </>
          ) : null}
        </div>
      </div>
      </>
      )}

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
                className="flex w-full rounded-md border border-input bg-transparent px-3 py-1.5 text-base md:text-sm text-foreground shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 resize-none pr-8 overflow-y-auto"
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
          {recipeRun && <a className="text-xs text-primary underline" href={`/jobs/${encodeURIComponent(jobId)}?run=${encodeURIComponent(recipeRun.id)}`}>Open run inspector</a>}
          <span className="text-xs text-muted-foreground">
            {disabledReason} — asking about changes is only available for pending diffs
          </span>
        </div>
      )}

    </div>
  );
}
