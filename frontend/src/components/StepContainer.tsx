import { useMemo, useState } from "react";
import { ChevronRight, GitBranch, User } from "lucide-react";
import { cn } from "../lib/utils";
import { useStore, selectStepEntries } from "../store";
import type { Step } from "../store";
import { useIsMobile } from "../hooks/useIsMobile";
import { StepHeader } from "./StepHeader";
import { AgentMarkdown } from "./AgentMarkdown";
import { FilesTouchedChips } from "./FilesTouchedChips";
import { Sheet } from "./ui/sheet";

/* ---------- ToolCallRow (expandable) ---------- */

function ToolCallRow({ entry }: { entry: import("../store").TranscriptEntry }) {
  const [open, setOpen] = useState(false);
  const hasDetail = !!(entry.toolResult || entry.toolArgs);

  return (
    <div>
      <button
        type="button"
        onClick={() => hasDetail && setOpen((v) => !v)}
        className={cn(
          "flex items-center gap-2 w-full text-left text-xs py-1 rounded",
          hasDetail ? "hover:bg-muted/50 cursor-pointer" : "cursor-default",
        )}
      >
        {hasDetail && (
          <ChevronRight
            size={12}
            className={cn(
              "shrink-0 text-muted-foreground transition-transform",
              open && "rotate-90",
            )}
          />
        )}
        {!hasDetail && <span className="w-3 shrink-0" />}
        <span className="shrink-0 mt-px">
          {entry.toolSuccess === false ? "✗" : "✓"}
        </span>
        <span className="font-mono text-foreground/80 truncate flex-1">
          {entry.toolDisplay || entry.toolName}
        </span>
        {entry.toolDurationMs != null && (
          <span className="shrink-0 text-muted-foreground tabular-nums">
            {entry.toolDurationMs < 1000
              ? `${entry.toolDurationMs}ms`
              : `${(entry.toolDurationMs / 1000).toFixed(1)}s`}
          </span>
        )}
      </button>

      {open && (
        <div className="ml-7 mb-2 border-l border-border pl-3">
          {entry.toolArgs && (
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer hover:text-foreground select-none py-0.5">
                Arguments
              </summary>
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all text-foreground/70 bg-muted/30 rounded p-2">
                {entry.toolArgs}
              </pre>
            </details>
          )}
          {entry.toolResult && (
            <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs text-foreground/70 bg-muted/30 rounded p-2">
              {entry.toolResult}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

/* ---------- StepContainer ---------- */

/** SDK-internal tools that are metadata, not user-visible work. */
const SDK_INTERNAL_TOOLS = new Set(["report_intent", "manage_todo_list", "TodoWrite"]);

interface StepContainerProps {
  step: Step;
  isActive: boolean;
  expanded?: boolean;
  onToggle?: () => void;
  onViewDiff?: (step: Step) => void;
}

export function StepContainer({ step, isActive, expanded: externalExpanded, onToggle: externalToggle, onViewDiff }: StepContainerProps) {
  const isMobile = useIsMobile();
  const [localExpanded, setLocalExpanded] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);

  const expanded = externalExpanded ?? localExpanded;
  const toggleExpanded = externalToggle ?? (() => setLocalExpanded((v) => !v));

  const stepEntries = useStore(selectStepEntries(step.jobId, step.stepId));

  const currentTool = useMemo(() => {
    if (step.status !== "active") return null;
    const tools = stepEntries.filter((e) => e.role === "tool_running" && !SDK_INTERNAL_TOOLS.has(e.toolName ?? ""));
    return tools.length > 0 ? tools[tools.length - 1] : null;
  }, [stepEntries, step.status]);

  const agentMessage = useMemo(() => {
    const msgs = stepEntries.filter((e) => e.role === "agent");
    return msgs.length > 0 ? msgs[msgs.length - 1] : null;
  }, [stepEntries]);

  const toolCalls = useMemo(
    () => stepEntries.filter((e) => e.role === "tool_call" && !SDK_INTERNAL_TOOLS.has(e.toolName ?? "")),
    [stepEntries],
  );

  const operatorMessages = useMemo(
    () => stepEntries.filter((e) => e.role === "operator"),
    [stepEntries],
  );

  // Does this step have content worth expanding?
  const hasExpandableContent = toolCalls.length > 0
    || agentMessage != null
    || (step.filesWritten ?? []).length > 0
    || (step.startSha != null && step.endSha != null && step.startSha !== step.endSha);

  // Streaming delta for active step
  const streamingKey = `${step.jobId}:__default__`;
  const streamingText = useStore((s) => s.streamingMessages[streamingKey]);

  const handleToggle = () => {
    if (!hasExpandableContent) return;
    if (isMobile) {
      setSheetOpen(true);
    } else {
      toggleExpanded();
    }
  };

  return (
    <div
      className={cn(
        "border-l-2 pl-4 pr-4 py-3 transition-colors",
        isMobile && "min-h-[44px]",
        isActive
          ? "border-l-blue-500 bg-blue-500/5"
          : step.status === "done"
            ? "border-l-emerald-500/30"
            : step.status === "pending"
              ? "border-l-muted-foreground/20"
              : "border-l-transparent",
      )}
    >
      <StepHeader
        step={step}
        expanded={expanded}
        onToggle={handleToggle}
        hideChevron={isMobile}
        hasExpandableContent={hasExpandableContent}
      />

      {/* Running: show latest tool or streaming delta */}
      {isActive && currentTool && (
        <div className="mt-1 flex items-center gap-2 text-sm text-muted-foreground">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
          <span className="truncate">
            {currentTool.toolIntent || currentTool.toolDisplay || currentTool.toolName}
          </span>
        </div>
      )}

      {isActive && !currentTool && streamingText && (
        <div className="mt-2 text-sm text-foreground/90 leading-relaxed line-clamp-2">
          <span>{streamingText}</span>
          <span className="inline-block w-0.5 h-4 bg-foreground/50 animate-pulse ml-0.5" />
        </div>
      )}

      {/* Operator messages — shown inline with chat bubble treatment */}
      {operatorMessages.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {operatorMessages.map((msg) => (
            <div key={msg.seq} className="flex items-start gap-2 justify-end">
              <div className="rounded-lg bg-primary/10 border border-primary/20 px-3 py-1.5 max-w-[85%]">
                <div className="text-xs text-foreground/80 leading-relaxed">
                  <AgentMarkdown content={msg.content} />
                </div>
              </div>
              <div className="shrink-0 w-5 h-5 rounded-full bg-primary/20 flex items-center justify-center mt-0.5">
                <User size={10} className="text-primary" />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Expanded: summary + agent message */}
      {!isMobile && expanded && agentMessage && (
        <div className="mt-2 text-sm text-foreground/90 leading-relaxed">
          <AgentMarkdown content={agentMessage.content} />
        </div>
      )}

      {/* Expanded: file chips */}
      {!isMobile && expanded && <FilesTouchedChips step={step} />}

      {/* Expanded: tool call list */}
      {!isMobile && expanded && toolCalls.length > 0 && (
        <div className="mt-3 space-y-0.5 border-t pt-3">
          {toolCalls.map((tc) => (
            <ToolCallRow key={`${tc.seq}-${tc.toolName}`} entry={tc} />
          ))}
        </div>
      )}

      {/* Step diff button — always visible when there are changes */}
      {!isMobile && step.startSha && step.endSha && step.startSha !== step.endSha && (
        <button
          onClick={() => onViewDiff?.(step)}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground mt-2"
        >
          <GitBranch size={12} />
          View changes in this step
        </button>
      )}

      {/* Mobile: bottom sheet with full step details */}
      {isMobile && (
        <Sheet open={sheetOpen} onClose={() => setSheetOpen(false)} title={step.label}>
          {agentMessage && (
            <div className="text-sm text-foreground/90 leading-relaxed mb-4">
              <AgentMarkdown content={agentMessage.content} />
            </div>
          )}
          <FilesTouchedChips step={step} />
          {step.startSha && step.endSha && step.startSha !== step.endSha && (
            <button
              onClick={() => { setSheetOpen(false); onViewDiff?.(step); }}
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground mb-3"
            >
              <GitBranch size={12} />
              View changes in this step
            </button>
          )}
          {toolCalls.length > 0 && (
            <div className="space-y-0.5 border-t pt-3">
              {toolCalls.map((tc) => (
                <ToolCallRow key={`${tc.seq}-${tc.toolName}`} entry={tc} />
              ))}
            </div>
          )}
        </Sheet>
      )}
    </div>
  );
}
