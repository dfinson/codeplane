/**
 * CuratedFeedPreviews — expandable phase boxes and inline preview renderers.
 *
 * Renders tool clusters as collapsible phase cards with file chips and
 * inline diffs/command output/search results.
 */

import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import {
  ChevronDown, ChevronRight, GitBranch, GitFork,
  FileText, Pencil, FilePlus, Terminal, Globe, Cpu, Bot,
  Search,
} from "lucide-react";
import Ansi from "ansi-to-react";
import { useStore, selectStreamingToolOutput } from "../store";
import { processTerminalOutput, stripAnsi } from "../lib/ansi";
import { useShallow } from "zustand/react/shallow";
import type { TranscriptEntry } from "../store";
import { SdkIcon } from "./SdkBadge";
import { Tooltip } from "./ui/tooltip";
import { cn } from "../lib/utils";
import { pathBasename } from "../lib/paths";
import { isFailedTool, isRunningTool, isToolOutcome, toolOutputKey } from "../lib/transcriptIdentity";
import {
  formatDuration,
  trimWorktreePaths,
  parseArgs,
  stripMcpPrefix,
  TruncatedPayload,
} from "./ToolRenderers";
import { RunningToolIndicator } from "./RunningToolIndicator";
import { SyntaxBlock } from "./SyntaxBlock";
import { detectLanguage } from "../lib/detectLanguage";
import { useSearchHighlight } from "./CuratedFeed";
import { AgentMarkdown } from "./AgentMarkdown";
import type { ActionCluster, ClusterKind, PhaseFile } from "./CuratedFeedLogic";
import {
  KIND_LABELS,
  KIND_ICON_COLORS,
  deduplicateByFile,
  computeLineDiff,
} from "./CuratedFeedLogic";

// Icon lookup — matches KIND_LABELS keys
const KIND_ICONS: Record<ClusterKind, typeof FileText> = {
  read: FileText,
  write: Pencil,
  create: FilePlus,
  execute: Terminal,
  search: Search,
  agent: Cpu,
  web: Globe,
  other: Bot,
};

/** Wrapper that injects search highlight from context into AgentMarkdown. */
function HighlightedMarkdown({ content }: { content: string }) {
  const hl = useSearchHighlight();
  return <AgentMarkdown content={content} highlight={hl || undefined} />;
}

// ---------------------------------------------------------------------------
// PhaseBox
// ---------------------------------------------------------------------------

export function PhaseBox({
  cluster,
  defaultExpanded,
  onViewStepChanges,
  hasSubsequentActivity,
}: {
  cluster: ActionCluster;
  defaultExpanded?: boolean;
  onViewStepChanges?: (filePaths: string[], label: string, scrollToSeq?: number, turnId?: string) => void;
  hasSubsequentActivity?: boolean;
}) {
  const searchQuery = useSearchHighlight();
  const hasSearchMatch = !!searchQuery && cluster.entries.some((e) =>
    e.toolDisplay?.toLowerCase().includes(searchQuery)
    || e.toolName?.toLowerCase().includes(searchQuery)
  );
  const [manualExpanded, setManualExpanded] = useState(defaultExpanded ?? false);
  const expanded = manualExpanded || hasSearchMatch;
  const setExpanded = setManualExpanded;
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const Icon = KIND_ICONS[cluster.kind];
  const jobId = cluster.entries[0]?.jobId;
  const worktreeRoot = useStore((state) => jobId ? state.jobs[jobId]?.worktreePath : null);
  const files = useMemo(
    () => deduplicateByFile(cluster.entries, worktreeRoot),
    [cluster.entries, worktreeRoot],
  );
  const totalDuration = cluster.entries.reduce((sum, e) => sum + (e.toolDurationMs ?? 0), 0);
  const hasEdits = cluster.kind === "write" || cluster.kind === "create";
  const hasFailure = cluster.entries.some(isFailedTool);

  const firstSeq = cluster.entries[0]?.sequence;
  const turnId = cluster.entries[0]?.turnId;

  const handleViewChanges = useCallback(() => {
    if (!onViewStepChanges) return;
    const paths = files.map((f) => f.relativePath);
    const verb = KIND_LABELS[cluster.kind].singular;
    const names = files.map((f) => f.fileName);
    const shown = names.slice(0, 2).join(", ");
    const rest = names.length > 2 ? ` +${names.length - 2} more` : "";
    onViewStepChanges(paths, `${verb} ${shown}${rest}`, firstSeq, turnId ?? undefined);
  }, [onViewStepChanges, files, cluster.kind, firstSeq, turnId]);

  if (!expanded) {
    return (
      <div className="flex items-center gap-1">
        <button
          onClick={() => setExpanded(true)}
          className={cn(
            "flex items-center gap-2 py-1.5 px-2.5 rounded-md flex-1 text-left",
            "text-xs text-muted-foreground hover:text-foreground hover:bg-accent/40 transition-colors",
            "border border-transparent hover:border-border/50",
          )}
        >
          <Icon size={12} className={cn("shrink-0", KIND_ICON_COLORS[cluster.kind])} />
          <span className="font-medium">{cluster.label}</span>
          {cluster.entries[0]?.toolGroupSummary && (
            <span className="text-[11px] text-muted-foreground/[0.55] italic truncate ml-1 flex-1 min-w-0">
              {cluster.entries[0].toolGroupSummary}
            </span>
          )}
          {totalDuration > 0 && (
            <span className="text-[10px] opacity-40 ml-auto shrink-0">{formatDuration(totalDuration)}</span>
          )}
          {hasFailure && (
            <span className={cn(
              "text-[10px] font-medium shrink-0 ml-1",
              hasSubsequentActivity ? "text-muted-foreground/50" : "text-amber-400",
            )}>
              {hasSubsequentActivity ? "recovered" : "error"}
            </span>
          )}
          <ChevronRight size={11} className="opacity-40 shrink-0" />
        </button>
        {hasEdits && onViewStepChanges && (
          <Tooltip content="View changes in diff viewer">
            <button
              onClick={handleViewChanges}
              className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium text-primary/70 hover:text-primary hover:bg-primary/10 transition-colors shrink-0"
            >
              <GitBranch size={10} />
              <span>View Changes</span>
            </button>
          </Tooltip>
        )}
      </div>
    );
  }

  const selectedFile = files.find((f) => f.key === selectedKey);

  return (
    <div className="rounded-md border border-border/50 bg-muted/10 overflow-hidden">
      <div className="flex items-center gap-1 pr-1">
        <button
          onClick={() => setExpanded(false)}
          className="flex items-center gap-2 px-3 py-1.5 flex-1 text-left text-xs text-muted-foreground hover:text-foreground hover:bg-accent/20 transition-colors"
        >
          <Icon size={12} className={cn("shrink-0", KIND_ICON_COLORS[cluster.kind])} />
          <span className="font-medium">{cluster.label}</span>
          {totalDuration > 0 && (
            <span className="text-[10px] opacity-40 ml-auto shrink-0">{formatDuration(totalDuration)}</span>
          )}
          <ChevronDown size={11} className="opacity-40 shrink-0" />
        </button>
        {hasEdits && onViewStepChanges && (
          <Tooltip content="View changes in diff viewer">
            <button
              onClick={handleViewChanges}
              className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium text-primary/70 hover:text-primary hover:bg-primary/10 transition-colors shrink-0"
            >
              <GitBranch size={10} />
              <span>View Changes</span>
            </button>
          </Tooltip>
        )}
      </div>

      <div className="flex flex-wrap gap-1.5 px-3 py-2 border-t border-border/20">
        {files.map((f) => (
          <FileChip
            key={f.key}
            file={f}
            selected={selectedKey === f.key}
            onClick={() => setSelectedKey(selectedKey === f.key ? null : f.key)}
          />
        ))}
      </div>

      {selectedFile && (
        <div className="border-t border-border/20">
          <InlinePreview file={selectedFile} kind={cluster.kind} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SubAgentBubble
// ---------------------------------------------------------------------------

export function SubAgentBubble({
  cluster,
  sdk,
}: {
  cluster: ActionCluster;
  sdk?: string;
}) {
  const [expanded, setExpanded] = useState(false);

  const completedEntry = cluster.entries.find((e) => isToolOutcome(e.kind));
  const runningEntry = cluster.entries.find(isRunningTool);
  const entry = completedEntry ?? runningEntry ?? cluster.entries[cluster.entries.length - 1]!;

  const args = parseArgs(entry.arguments);
  const description = (args.description as string) || entry.toolDisplay?.replace(/^Task:\s*/i, "") || "Sub-agent task";
  const isRunning = !completedEntry && !!runningEntry;
  const result = completedEntry?.result ?? entry.result;
  const totalDuration = cluster.entries.reduce((sum, e) => sum + (e.toolDurationMs ?? 0), 0);
  const hasResult = !!result?.trim();

  return (
    <div className="rounded-md border border-border/30 bg-card/50 overflow-hidden">
      <button
        onClick={() => hasResult && setExpanded(!expanded)}
        className={cn(
          "flex items-center gap-2.5 px-3 py-2 w-full text-left transition-colors",
          hasResult && "hover:bg-accent/20 cursor-pointer",
          !hasResult && "cursor-default",
        )}
      >
        <GitFork size={12} className={cn("shrink-0", isRunning ? "text-primary" : "text-muted-foreground/40")} />
        <span className={cn(
          "text-xs flex-1 min-w-0",
          isRunning ? "text-foreground/80 font-medium" : "text-muted-foreground",
        )}>
          {description}
          {isRunning && (
            <span className="inline-block w-1 h-3 bg-primary/60 animate-pulse ml-1.5 align-text-bottom rounded-sm" />
          )}
        </span>
        {totalDuration > 0 && !isRunning && (
          <span className="text-[10px] text-muted-foreground/30 shrink-0">{formatDuration(totalDuration)}</span>
        )}
        {hasResult && (
          expanded
            ? <ChevronDown size={11} className="opacity-30 shrink-0" />
            : <ChevronRight size={11} className="opacity-30 shrink-0" />
        )}
      </button>

      {isRunning && (
        <RunningToolIndicator jobId={entry.jobId} startedAt={entry.timestamp} />
      )}

      {expanded && hasResult && (
        <div className="border-t border-border/20 px-3 py-2">
          <div className="flex gap-2.5">
            <div className="shrink-0 w-5 h-5 rounded-full bg-muted/30 flex items-center justify-center mt-0.5">
              <SdkIcon sdk={sdk} size={12} fallback={<Bot size={11} className="text-muted-foreground/50" />} />
            </div>
            <div className="flex-1 min-w-0 text-xs text-foreground/80 leading-relaxed max-h-80 overflow-y-auto">
              <HighlightedMarkdown content={trimWorktreePaths(result!)} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FileChip
// ---------------------------------------------------------------------------

export function FileChip({
  file,
  selected,
  onClick,
}: {
  file: PhaseFile;
  selected: boolean;
  onClick: () => void;
}) {
  const failed = file.entries.some(isFailedTool);
  const isRunning = file.entries.some(isRunningTool);
  const editCount = file.entries.length > 1 ? file.entries.length : undefined;

  return (
    <button
      onClick={onClick}
      title={file.key}
      className={cn(
        "inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono",
        "transition-colors cursor-pointer select-none",
        selected
          ? "bg-primary/20 text-primary border border-primary/40"
          : "bg-muted/40 text-muted-foreground hover:bg-accent/50 hover:text-foreground border border-transparent",
        failed && "text-red-400",
        isRunning && "animate-pulse",
      )}
    >
      <span className="truncate max-w-[140px] sm:max-w-[200px]">{file.fileName}</span>
      {editCount && <span className="text-[9px] opacity-50">×{editCount}</span>}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Inline preview components
// ---------------------------------------------------------------------------

function InlinePreview({ file, kind }: { file: PhaseFile; kind: ClusterKind }) {
  return (
    <div>
      {kind !== "execute" && kind !== "search" && (
        <div
          className="px-3 py-1 text-[11px] font-mono text-muted-foreground/60 border-b border-border/10"
          title={file.key}
        >
          {file.relativePath}
        </div>
      )}
      <InlinePreviewContent entries={file.entries} kind={kind} filePath={file.relativePath} />
    </div>
  );
}

function InlinePreviewContent({ entries, kind, filePath }: { entries: TranscriptEntry[]; kind: ClusterKind; filePath?: string }) {
  switch (kind) {
    case "execute":
      return <CommandPreview entries={entries} />;
    case "write":
      return <EditPreview entries={entries} filePath={filePath} />;
    case "read":
      return <ReadPreview entries={entries} filePath={filePath} />;
    case "create":
      return <CreatePreview entries={entries} filePath={filePath} />;
    case "search":
      return <SearchPreview entries={entries} />;
    default:
      return <GenericPreview entries={entries} />;
  }
}

function CommandPreview({ entries }: { entries: TranscriptEntry[] }) {
  const entry = entries[entries.length - 1]!;
  const args = parseArgs(entry.arguments);
  const command = trimWorktreePaths((args.command as string) ?? "");
  const failed = isFailedTool(entry);
  const isRunning = isRunningTool(entry);
  const jobs = useStore((s) => s.jobs);
  const createTerminalSession = useStore((s) => s.createTerminalSession);
  const job = jobs[entry.jobId];
  const canOpenTerminal = !!job?.worktreePath;
  const streamingOutput = useStore(useShallow(selectStreamingToolOutput(entry.jobId)));
  const outputRef = useRef<HTMLPreElement>(null);

  const liveOutput = useMemo(() => {
    if (!isRunning || !streamingOutput) return "";
    return streamingOutput[toolOutputKey(entry).slice(entry.jobId.length + 1)] ?? "";
  }, [entry, isRunning, streamingOutput]);

  useEffect(() => {
    if (outputRef.current && liveOutput) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [liveOutput]);

  const handleOpenTerminal = useCallback(() => {
    if (!job?.worktreePath) return;
    createTerminalSession({ cwd: job.worktreePath, jobId: entry.jobId, label: job.branch || pathBasename(job.repo) || "Terminal" });
  }, [job, entry.jobId, createTerminalSession]);

  return (
    <div className="font-mono text-[13px] sm:text-xs">
      <div className={cn("px-3 py-1.5 flex items-start gap-2", failed ? "bg-red-950/20" : "bg-zinc-950/30")}>
        <div className="flex-1 min-w-0">
          <span className="text-muted-foreground">$ </span>
          <span className="text-foreground/90">{command}</span>
          {isRunning && !liveOutput && (
            <span className="ml-2 inline-block w-1.5 h-3 bg-primary/70 animate-pulse rounded-sm align-middle" />
          )}
        </div>
        {isRunning && canOpenTerminal && (
          <Tooltip content="Open terminal in worktree">
            <button
              onClick={handleOpenTerminal}
              className="shrink-0 p-1.5 sm:p-1 rounded text-foreground/60 hover:text-primary hover:bg-primary/20 transition-colors min-h-[44px] min-w-[44px] sm:min-h-0 sm:min-w-0 flex items-center justify-center"
              aria-label="Open terminal"
            >
              <Terminal size={15} className="sm:w-3 sm:h-3" />
            </button>
          </Tooltip>
        )}
      </div>
      {isRunning && liveOutput && (
        <pre
          ref={outputRef}
          className="px-3 py-1.5 text-[12px] sm:text-[11px] text-muted-foreground/80 whitespace-pre-wrap break-words max-h-48 overflow-y-auto border-l-2 border-primary/30 bg-zinc-950/20"
        >
          <Ansi>{processTerminalOutput(liveOutput)}</Ansi>
          <span className="inline-block w-1.5 h-3 bg-primary/70 animate-pulse rounded-sm align-middle ml-0.5" />
        </pre>
      )}
      {isRunning && (
        <RunningToolIndicator jobId={entry.jobId} startedAt={entry.timestamp} />
      )}
      {entry.result && (
        <div className="px-3 py-1.5">
          <SyntaxBlock content={stripAnsi(trimWorktreePaths(entry.result))} language="bash" maxLength={600} />
        </div>
      )}
      {failed && entry.toolIssue && (
        <div className="px-3 py-1 text-red-400 text-[11px]">{entry.toolIssue}</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// DiffLines
// ---------------------------------------------------------------------------

const MAX_DIFF_LINES = 30;

export function DiffLines({ oldStr, newStr }: { oldStr: string; newStr: string }) {
  const lines = useMemo(() => computeLineDiff(oldStr, newStr), [oldStr, newStr]);
  const capped = lines.length > MAX_DIFF_LINES;
  const visible = capped ? lines.slice(0, MAX_DIFF_LINES) : lines;
  const gutterWidth = Math.max(
    ...lines.map((l) => Math.max(l.oldNo ?? 0, l.newNo ?? 0)),
  ).toString().length;

  return (
    <div className="font-mono text-[11px] leading-relaxed overflow-x-auto">
      {visible.map((line, i) => {
        const isCollapse = line.text === "···";
        if (isCollapse) {
          return (
            <div key={i} className="text-muted-foreground/40 select-none px-1">
              {"  ".repeat(gutterWidth)}  ···
            </div>
          );
        }
        const oldGutter = line.oldNo != null ? String(line.oldNo).padStart(gutterWidth) : " ".repeat(gutterWidth);
        const newGutter = line.newNo != null ? String(line.newNo).padStart(gutterWidth) : " ".repeat(gutterWidth);
        const prefix = line.type === "del" ? "-" : line.type === "add" ? "+" : " ";
        return (
          <div
            key={i}
            className={cn(
              "px-1 whitespace-pre",
              line.type === "del" && "bg-red-500/10 text-red-400/80",
              line.type === "add" && "bg-green-500/10 text-green-400/80",
              line.type === "ctx" && "text-muted-foreground/60",
            )}
          >
            <span className="text-muted-foreground/30 select-none">{oldGutter} {newGutter} </span>
            {prefix} {line.text}
          </div>
        );
      })}
      {capped && (
        <div className="text-muted-foreground/40 text-[10px] px-1 py-0.5">
          +{lines.length - MAX_DIFF_LINES} more lines
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// EditPreview
// ---------------------------------------------------------------------------

function EditPreview({ entries }: { entries: TranscriptEntry[]; filePath?: string }) {
  return (
    <div className="text-[13px] sm:text-xs space-y-0">
      {entries.map((entry, i) => {
        const args = parseArgs(entry.arguments);
        const name = stripMcpPrefix(entry.toolName ?? "");
        const failed = isFailedTool(entry);

        if (name === "multi_replace_string_in_file" || name === "MultiEdit") {
          const edits = (args.replacements ?? args.edits ?? []) as Array<Record<string, unknown>>;
          return (
            <div key={i} className="px-3 py-1.5 space-y-1.5">
              <div className="flex items-center gap-2 text-muted-foreground">
                <Pencil size={10} className="text-amber-400 shrink-0" />
                <span>{edits.length} edits {failed ? "→ failed" : "→ applied"}</span>
              </div>
              {edits.slice(0, 6).map((e, j) => {
                const oldStr = (e.old_string ?? e.old_str ?? e.oldString) as string | undefined;
                const newStr = (e.new_string ?? e.new_str ?? e.newString) as string | undefined;
                return oldStr && newStr ? (
                  <div key={j} className={cn(j > 0 && "border-t border-border/10 pt-1.5")}>
                    <DiffLines oldStr={oldStr} newStr={newStr} />
                  </div>
                ) : null;
              })}
              {edits.length > 6 && (
                <div className="text-muted-foreground/50 text-[10px]">+{edits.length - 6} more</div>
              )}
            </div>
          );
        }

        const oldStr = (args.old_str ?? args.old_string ?? args.oldString) as string | undefined;
        const newStr = (args.new_str ?? args.new_string ?? args.newString) as string | undefined;
        return (
          <div key={i} className={cn("px-3 py-1.5", i > 0 && "border-t border-border/10")}>
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Pencil size={10} className="text-amber-400 shrink-0" />
              <span>{failed ? "Failed" : "Applied"}</span>
              {entry.toolDurationMs != null && (
                <span className="text-[10px] opacity-40">{formatDuration(entry.toolDurationMs)}</span>
              )}
            </div>
            {typeof oldStr === "string" && typeof newStr === "string" && (
              <DiffLines oldStr={oldStr} newStr={newStr} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ReadPreview
// ---------------------------------------------------------------------------

function ReadPreview({ entries, filePath }: { entries: TranscriptEntry[]; filePath?: string }) {
  const entry = entries[entries.length - 1]!;
  const args = parseArgs(entry.arguments);
  const startLine = (args.startLine ?? args.start_line) as number | undefined;
  const endLine = (args.endLine ?? args.end_line) as number | undefined;
  const range = startLine && endLine ? `lines ${startLine}–${endLine}` : null;
  const lang = detectLanguage(filePath);

  return (
    <div className="text-[13px] sm:text-xs">
      {range && (
        <div className="px-3 py-1 text-muted-foreground/60">{range}</div>
      )}
      {entry.result && (
        <SyntaxBlock
          content={trimWorktreePaths(entry.result)}
          language={lang}
          maxLength={800}
          showLineNumbers={!!startLine}
          startLine={startLine ?? 1}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// CreatePreview
// ---------------------------------------------------------------------------

function CreatePreview({ entries, filePath }: { entries: TranscriptEntry[]; filePath?: string }) {
  const entry = entries[0]!;
  const args = parseArgs(entry.arguments);
  const fileContent = (args.content ?? args.file_text) as string | undefined;
  const lang = detectLanguage(filePath);

  return (
    <div className="px-3 py-1.5 text-xs">
      <div className="flex items-center gap-2 text-muted-foreground">
        <FilePlus size={10} className="text-green-400 shrink-0" />
        <span>Created</span>
        {entry.toolDurationMs != null && (
          <span className="text-[10px] opacity-40">{formatDuration(entry.toolDurationMs)}</span>
        )}
      </div>
      {fileContent ? (
        <div className="mt-1 border-l-2 border-green-500/30">
          <SyntaxBlock content={trimWorktreePaths(fileContent)} language={lang} maxLength={800} />
        </div>
      ) : entry.result ? (
        <div className="mt-1 font-mono">
          <TruncatedPayload content={trimWorktreePaths(entry.result)} maxLength={400} />
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SearchPreview
// ---------------------------------------------------------------------------

function SearchPreview({ entries }: { entries: TranscriptEntry[] }) {
  const entry = entries[entries.length - 1]!;
  const lines = entry.result?.split("\n").filter((l) => l.trim()).length;

  return (
    <div className="text-[13px] sm:text-xs">
      {lines != null && (
        <div className="px-3 py-1 text-muted-foreground/60">→ {lines} results</div>
      )}
      {entry.result && (
        <SyntaxBlock content={trimWorktreePaths(entry.result)} maxLength={600} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// GenericPreview
// ---------------------------------------------------------------------------

function GenericPreview({ entries }: { entries: TranscriptEntry[] }) {
  const entry = entries[entries.length - 1]!;
  return (
    <div className="px-3 py-1.5 text-[13px] sm:text-xs">
      {entry.toolDisplay && (
        <div className="text-muted-foreground mb-1">{entry.toolDisplay}</div>
      )}
      {entry.result && (
        <SyntaxBlock content={trimWorktreePaths(entry.result)} maxLength={400} />
      )}
    </div>
  );
}
