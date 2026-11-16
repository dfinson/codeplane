/* eslint-disable react-refresh/only-export-components -- exports useSearchHighlight alongside components */
/**
 * CuratedFeed — curated, structured activity view with progressive disclosure.
 *
 * Design principles:
 * - Whitelist rendering: only high-signal info is visible by default
 * - Action clustering: consecutive similar tools → single chip ("Read 5 files")
 * - Progressive disclosure: expand clusters on click for full detail
 * - Agent messages always shown in full (they ARE the high signal)
 * - Reasoning shown as subtle secondary text
 * - Minimal visual weight, muted colors
 */

import { useRef, useEffect, useState, useCallback, useMemo, memo, createContext, useContext } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { useNavigate } from "react-router-dom";
import {
  Send, Bot, User, ChevronDown, ChevronUp, Brain,
  ShieldQuestion, CheckCircle2, XCircle as XCircleIcon,
  ArrowDown, Search, PauseCircle, X,
  Clock, Milestone, Telescope,
} from "lucide-react";
import { toast } from "sonner";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useStore, selectJobTranscript, selectApprovals, selectBatchApprovals, selectJobPlan, selectSecondarySessions, selectContextHandoffs } from "../store";
import type { TranscriptEntry, ApprovalRequest, BatchApproval, PlanStep } from "../store";
import { sendOperatorMessage, continueJob, resumeJob, pauseJob, resolveApproval, resolveBatch, trustJob, ApiError } from "../api/client";
import { AgentMarkdown } from "./AgentMarkdown";
import { SdkIcon } from "./SdkBadge";
import { MicButton } from "./VoiceButton";
import { Button } from "./ui/button";
import { Tooltip } from "./ui/tooltip";
import { Spinner } from "./ui/spinner";
import { cn } from "../lib/utils";
import { isToolOutcome } from "../lib/transcriptIdentity";
import { trimWorktreePaths } from "./ToolRenderers";
import type { ActionCluster, AgentTurn, FeedItem } from "./CuratedFeedLogic";
import { buildFeedItems } from "./CuratedFeedLogic";
import { PhaseBox, SubAgentBubble } from "./CuratedFeedPreviews";
import { SecondarySessionCard } from "./SecondarySessionCard";
import { HandoffCard } from "./HandoffCard";

// ---------------------------------------------------------------------------
// Search highlight context — provides the active search query to children
// ---------------------------------------------------------------------------
const SearchHighlightCtx = createContext("");
export const useSearchHighlight = () => useContext(SearchHighlightCtx);

/** Wrapper that injects search highlight from context into AgentMarkdown. */
function HighlightedMarkdown({ content }: { content: string }) {
  const hl = useSearchHighlight();
  return <AgentMarkdown content={content} highlight={hl || undefined} />;
}

// ---------------------------------------------------------------------------
// Feed item renderers — message blocks
// ---------------------------------------------------------------------------

const OperatorMessage = memo(function OperatorMessage({ entry }: { entry: TranscriptEntry }) {
  return (
    <div className="flex gap-2 sm:gap-3 py-3 justify-end">
      <div className="min-w-0 max-w-[85%] rounded-lg bg-primary/[0.18] border border-primary/25 px-3 py-2">
        <div className="text-[15px] sm:text-sm text-foreground leading-relaxed">
          <HighlightedMarkdown content={entry.content ?? ""} />
        </div>
      </div>
      <div className="shrink-0 w-5 h-5 sm:w-6 sm:h-6 rounded-full bg-primary/25 flex items-center justify-center">
        <User size={13} className="text-primary" />
      </div>
    </div>
  );
});



const AgentTurnBlock = memo(function AgentTurnBlock({
  turn,
  clusters,
  sdk,
  isStreaming,
  streamingText,
  streamingReasoningText,
  onViewStepChanges,
}: {
  turn: AgentTurn;
  clusters: ActionCluster[];
  sdk?: string;
  isStreaming?: boolean;
  streamingText?: string;
  streamingReasoningText?: string;
  onViewStepChanges?: (filePaths: string[], label: string, scrollToSeq?: number, turnId?: string) => void;
}) {
  const hasTools = clusters.length > 0;
  const messageContent = turn.message?.content?.trim() ?? "";
  const displayMessage = streamingText || messageContent;
  const hasMessage = !!displayMessage;
  const hasReasoning = !!(turn.reasoning?.content || streamingReasoningText);

  return (
    <div className="py-3 space-y-2">
      {/* Tool phases as stacked boxes */}
      {hasTools && (
        <div className="space-y-1.5">
          {clusters.map((c, i) => {
            if (c.kind === "agent") {
              return <SubAgentBubble key={i} cluster={c} sdk={sdk} />;
            }
            const hasSubsequentActivity = i < clusters.length - 1 || hasMessage;
            return (
              <PhaseBox
                key={i}
                cluster={c}
                defaultExpanded={i === clusters.length - 1}
                onViewStepChanges={onViewStepChanges}
                hasSubsequentActivity={hasSubsequentActivity}
              />
            );
          })}
        </div>
      )}

      {/* Agent bubble — message + reasoning grouped together */}
      {(hasMessage || (hasReasoning && !hasTools)) && (
        <div>
          <div className={cn(
            "min-w-0 rounded-lg px-2.5 sm:px-3 py-2 space-y-1.5",
            isStreaming ? "bg-card/90 border border-primary/20 animate-activity-shimmer" : "bg-card/60",
          )}>
            {hasReasoning && (
              <ReasoningHint content={turn.reasoning?.content ?? ""} streamingText={streamingReasoningText} />
            )}
            {displayMessage && (
              <div className="text-[15px] sm:text-[14px] text-foreground leading-relaxed">
                <HighlightedMarkdown content={displayMessage} />
                {isStreaming && (
                  <span className="inline-block w-1.5 h-4 bg-primary/60 animate-pulse ml-0.5 align-text-bottom" />
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Reasoning only (no message yet, but tools present) — show below tools */}
      {hasReasoning && hasTools && !hasMessage && (
        <ReasoningHint content={turn.reasoning?.content ?? ""} streamingText={streamingReasoningText} />
      )}

      {/* Streaming with no committed message yet and no reasoning bubble shown */}
      {!displayMessage && isStreaming && streamingText && !hasReasoning && (
        <div>
          <div className="min-w-0 rounded-lg bg-card/90 border border-primary/20 animate-activity-shimmer px-3 py-2">
            <div className="text-[15px] sm:text-[14px] text-foreground leading-relaxed">
              <HighlightedMarkdown content={streamingText} />
              <span className="inline-block w-1.5 h-4 bg-primary/60 animate-pulse ml-0.5 align-text-bottom" />
            </div>
          </div>
        </div>
      )}
    </div>
  );
});

const CondensedTurnBlock = memo(function CondensedTurnBlock({
  turn,
  clusters,
  sdk,
  onViewStepChanges,
}: {
  turn: AgentTurn;
  clusters: ActionCluster[];
  sdk?: string;
  onViewStepChanges?: (filePaths: string[], label: string, scrollToSeq?: number, turnId?: string) => void;
}) {
  return (
    <div className="py-1 space-y-1">
      {clusters.map((c, i) => (
        c.kind === "agent"
          ? <SubAgentBubble key={i} cluster={c} sdk={sdk} />
          : <PhaseBox key={i} cluster={c} defaultExpanded={i === clusters.length - 1} onViewStepChanges={onViewStepChanges} hasSubsequentActivity={i < clusters.length - 1} />
      ))}
      {turn.reasoning?.content && (
        <div className="mt-1">
          <div className="flex-1 min-w-0 rounded-lg bg-card/40 px-2.5 sm:px-3 py-2">
            <ReasoningHint content={turn.reasoning.content} />
          </div>
        </div>
      )}
    </div>
  );
});

function ReasoningHint({ content, streamingText }: { content: string; streamingText?: string }) {
  const [expanded, setExpanded] = useState(false);
  const isLiveStreaming = !!streamingText && !content;
  const displayContent = streamingText || content;
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isLiveStreaming && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [isLiveStreaming, displayContent]);

  const showExpanded = expanded || isLiveStreaming;

  return (
    <div className={cn(
      "text-xs text-foreground/60 leading-snug border-l-2 pl-2.5",
      isLiveStreaming ? "animate-reasoning-pulse" : "border-primary/30",
    )}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 hover:text-foreground/80 transition-colors text-left w-full"
      >
        <Brain size={12} className="shrink-0 text-primary/50" />
        {showExpanded ? (
          <div ref={scrollRef} className="whitespace-pre-wrap max-h-48 overflow-y-auto flex-1 min-w-0 italic text-left">
            {trimWorktreePaths(displayContent)}
            {isLiveStreaming && (
              <span className="inline-block w-1 h-3 bg-primary/60 animate-pulse ml-0.5 align-text-bottom" />
            )}
          </div>
        ) : (
          <span className="text-[11px] text-primary/50 italic">Thinking…</span>
        )}
      </button>
    </div>
  );
}

function InlineApprovalCard({ approval }: { approval: ApprovalRequest }) {
  const [resolving, setResolving] = useState<"approved" | "rejected" | null>(null);
  const [showFeedback, setShowFeedback] = useState(false);
  const [feedbackText, setFeedbackText] = useState("");
  const isPlanApproval = approval.proposedAction === "execute_plan";
  const planSteps = useStore(selectJobPlan(approval.jobId));

  const handleResolve = async (resolution: "approved" | "rejected") => {
    // For plan rejections, show feedback form first
    if (resolution === "rejected" && isPlanApproval && !showFeedback) {
      setShowFeedback(true);
      return;
    }
    setResolving(resolution);
    try {
      const notes = feedbackText.trim() || undefined;
      await resolveApproval(approval.id, resolution, notes);
    } catch (err) {
      toast.error("Failed to resolve approval");
      console.error("Failed to resolve approval:", err);
    } finally {
      setResolving(null);
      setShowFeedback(false);
    }
  };

  const isResolved = !!approval.resolvedAt;

  return (
    <div className={cn(
      "rounded-lg border px-4 py-3 my-2",
      isResolved ? "border-border/40 bg-card/30" : "border-amber-600/30 bg-amber-950/10",
    )}>
      <div className="flex items-start gap-2.5">
        <ShieldQuestion size={15} className={cn("shrink-0 mt-0.5", isResolved ? "text-muted-foreground/40" : isPlanApproval ? "text-blue-400" : "text-amber-400")} />
        <div className="flex-1 min-w-0 space-y-2">
          <p className="text-sm text-foreground/80">{approval.description}</p>
          {isPlanApproval && planSteps.length > 0 ? (
            <ol className="text-[12px] text-muted-foreground/80 bg-black/20 rounded px-3 py-2 space-y-1 list-decimal list-inside max-h-48 overflow-auto">
              {planSteps.map((step: PlanStep, i: number) => (
                <li key={step.planStepId ?? i} className="leading-relaxed">
                  {step.label}
                </li>
              ))}
            </ol>
          ) : approval.proposedAction && !isPlanApproval ? (
            <pre className="text-[11px] text-muted-foreground/60 bg-black/20 rounded px-2 py-1 whitespace-pre-wrap max-h-24 overflow-auto">
              {approval.proposedAction}
            </pre>
          ) : null}
          {isResolved ? (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground/50">
              {approval.resolution === "approved"
                ? <><CheckCircle2 size={12} className="text-emerald-400/60" /> Approved</>
                : <><XCircleIcon size={12} className="text-red-400/60" /> Rejected</>
              }
            </div>
          ) : showFeedback ? (
            <div className="space-y-2">
              <textarea
                className="w-full text-xs bg-black/30 border border-border/40 rounded px-2 py-1.5 text-foreground/80 placeholder:text-muted-foreground/40 resize-none focus:outline-none focus:border-primary/40"
                rows={3}
                placeholder="What should the agent change? (optional)"
                value={feedbackText}
                onChange={(e) => setFeedbackText(e.target.value)}
                autoFocus
              />
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => handleResolve("rejected")}
                  disabled={!!resolving}
                  className="text-xs h-7 sm:h-7 min-h-[44px] sm:min-h-0 border-red-700/40 text-red-400 hover:bg-red-950/30"
                >
                  {resolving === "rejected" ? <Spinner className="w-3 h-3" /> : "Submit & Revise"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => { setShowFeedback(false); setFeedbackText(""); }}
                  disabled={!!resolving}
                  className="text-xs h-7 sm:h-7 min-h-[44px] sm:min-h-0 text-muted-foreground/60"
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => handleResolve("approved")}
                disabled={!!resolving}
                className="text-xs h-7 sm:h-7 min-h-[44px] sm:min-h-0 border-emerald-700/40 text-emerald-400 hover:bg-emerald-950/30"
              >
                {resolving === "approved" ? <Spinner className="w-3 h-3" /> : isPlanApproval ? "Approve Plan" : "Approve"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => handleResolve("rejected")}
                disabled={!!resolving}
                className="text-xs h-7 sm:h-7 min-h-[44px] sm:min-h-0 border-red-700/40 text-red-400 hover:bg-red-950/30"
              >
                {resolving === "rejected" ? <Spinner className="w-3 h-3" /> : isPlanApproval ? "Revise Plan" : "Reject"}
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const ACTION_ICON: Record<string, string> = {
  allow: "○",
  warn: "◐",
  transform: "◐",
  escalate: "●",
  deny: "●",
};

function InlineBatchApprovalCard({ batch }: { batch: BatchApproval }) {
  const [resolving, setResolving] = useState<string | null>(null);

  const handleResolve = async (resolution: "approved" | "rejected") => {
    setResolving(resolution);
    try {
      await resolveBatch(batch.jobId, batch.batchId, resolution);
    } catch (err) {
      toast.error("Failed to resolve batch");
      console.error("Failed to resolve batch:", err);
    } finally {
      setResolving(null);
    }
  };

  const handleTrust = async () => {
    setResolving("trust");
    try {
      await resolveBatch(batch.jobId, batch.batchId, "approved");
      await trustJob(batch.jobId);
      toast.success("Session trusted — future actions will auto-approve");
    } catch (err) {
      toast.error("Failed to trust session");
      console.error("Failed to trust session:", err);
    } finally {
      setResolving(null);
    }
  };

  const isResolved = !!batch.resolvedAt;

  return (
    <div className={cn(
      "rounded-lg border px-4 py-3 my-2",
      isResolved ? "border-border/40 bg-card/30" : "border-amber-600/30 bg-amber-950/10",
    )}>
      <div className="flex items-start gap-2.5">
        <ShieldQuestion size={15} className={cn("shrink-0 mt-0.5", isResolved ? "text-muted-foreground/40" : "text-amber-400")} />
        <div className="flex-1 min-w-0 space-y-2">
          <p className="text-sm font-medium text-foreground/80">
            {batch.actions.length === 1
              ? "Approval Required"
              : `${batch.actions.length} actions require approval`}
          </p>
          <div className="space-y-1">
            {batch.actions.map((action) => (
              <div key={action.id} className="flex items-start gap-1.5 text-xs text-muted-foreground/70">
                <span className="text-amber-400/80 font-mono shrink-0">{ACTION_ICON[action.recommendedAction] ?? "●"}</span>
                <span className="min-w-0 break-words whitespace-pre-wrap">
                  {action.description}
                  {action.effect === "destructive" && <span className="text-red-400/70 ml-1">destructive</span>}
                </span>
              </div>
            ))}
          </div>
          {isResolved ? (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground/50">
              {batch.resolution === "approved"
                ? <><CheckCircle2 size={12} className="text-emerald-400/60" /> Approved</>
                : <><XCircleIcon size={12} className="text-red-400/60" /> Rejected</>
              }
            </div>
          ) : (
            <div className="flex gap-2 flex-wrap">
              <Button
                size="sm"
                variant="outline"
                onClick={() => handleResolve("approved")}
                disabled={!!resolving}
                className="text-xs h-7 sm:h-7 min-h-[44px] sm:min-h-0 border-emerald-700/40 text-emerald-400 hover:bg-emerald-950/30"
              >
                {resolving === "approved" ? <Spinner className="w-3 h-3" /> : "Approve"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={handleTrust}
                disabled={!!resolving}
                className="text-xs h-7 sm:h-7 min-h-[44px] sm:min-h-0 border-emerald-700/40 text-emerald-300 hover:bg-emerald-950/30"
              >
                {resolving === "trust" ? <Spinner className="w-3 h-3" /> : "Trust Session"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => handleResolve("rejected")}
                disabled={!!resolving}
                className="text-xs h-7 sm:h-7 min-h-[44px] sm:min-h-0 border-red-700/40 text-red-400 hover:bg-red-950/30"
              >
                {resolving === "rejected" ? <Spinner className="w-3 h-3" /> : "Reject"}
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function DividerLine({ entry }: { entry: TranscriptEntry }) {
  const text = entry.content || "Session";
  const isStep = text !== "Session";
  return (
    <div className="flex items-center gap-2.5 py-4">
      <div className="flex-1 border-t border-border/50" />
      <div className="flex items-center gap-1.5 shrink-0">
        {isStep ? (
          <CheckCircle2 size={12} className="text-emerald-400/70" />
        ) : (
          <Milestone size={12} className="text-muted-foreground/40" />
        )}
        <span className={cn(
          "text-[11px] font-medium tracking-wide",
          isStep ? "text-foreground/60" : "text-muted-foreground/40 uppercase",
        )}>
          {text}
        </span>
      </div>
      <div className="flex-1 border-t border-border/50" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agent activity pulse bar — ambient heartbeat when agent is working
// ---------------------------------------------------------------------------

function AgentActivityBar({ jobId, sdk, jobState }: { jobId: string; sdk?: string; jobState?: string }) {
  const job = useStore((s) => s.jobs[jobId]);
  const streamingMessages = useStore((s) => s.streamingMessages);
  const sessions = useStore(selectSecondarySessions(jobId));
  const preflightActive = Object.values(sessions).some((s) => s.kind === "preflight" && s.status === "running");
  const isJobLive = jobState === "running" || jobState === "waiting_for_approval";

  const hasStream = Object.keys(streamingMessages).some((k) => k.startsWith(`${jobId}:`));
  if (!isJobLive || hasStream) return null;

  const headline = preflightActive
    ? "Scout exploring codebase…"
    : (job?.progressHeadline || null);
  const isApproval = jobState === "waiting_for_approval";

  return (
    <div className="animate-activity-shimmer rounded-md border border-border/30 px-3 py-2 mb-1 flex items-center gap-2.5 transition-opacity duration-300">
      <div className="relative shrink-0">
        {preflightActive ? (
          <Telescope size={14} className="text-blue-400" />
        ) : (
          <SdkIcon sdk={sdk} size={14} fallback={<Bot size={13} className="text-muted-foreground/60" />} />
        )}
        <span className={cn(
          "absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full",
          isApproval ? "bg-amber-400 animate-pulse" : "bg-emerald-400",
        )} style={{ animationDuration: "2s" }} />
      </div>
      {headline && <span className="text-xs text-muted-foreground/70 truncate flex-1 min-w-0">{headline}</span>}
      {!headline && <span className="flex-1" aria-hidden="true" />}
      <Clock size={10} className="text-muted-foreground/30 shrink-0" aria-hidden="true" />
      <span className="sr-only">{headline || "Agent is working"}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function CuratedFeed({
  jobId,
  sdk,
  interactive,
  pausable,
  jobState,
  prompt,
  promptTimestamp,
  onViewStepChanges,
  onSearchHighlight,
  onVisibleTurnId,
  visibleStepTurnId,
  scrollToSeq,
  scrollToTurnId,
  stepTurnIds,
}: {
  jobId: string;
  sdk?: string;
  interactive?: boolean;
  pausable?: boolean;
  jobState?: string;
  prompt?: string;
  promptTimestamp?: string;
  onViewStepChanges?: (filePaths: string[], label: string, scrollToSeq?: number, turnId?: string) => void;
  onSearchHighlight?: (turnId: string | null) => void;
  onVisibleTurnId?: (turnId: string | null) => void;
  visibleStepTurnId?: string | null;
  scrollToSeq?: number | null;
  scrollToTurnId?: string | null;
  stepTurnIds?: string[];
}) {
  const navigate = useNavigate();
  const rawEntries = useStore(selectJobTranscript(jobId));
  const allApprovals = useStore(selectApprovals);
  const allBatchApprovals = useStore(selectBatchApprovals);
  const streamingMessages = useStore((s) => s.streamingMessages);
  const allStreamingReasoning = useStore((s) => s.streamingReasoning);
  const jobApprovals = Object.values(allApprovals).filter((a) => a.jobId === jobId);
  const jobBatchApprovals = Object.values(allBatchApprovals).filter((b) => b.jobId === jobId);
  const isJobLive = jobState === "running" || jobState === "waiting_for_approval";

  const entries = useMemo<TranscriptEntry[]>(() => [
    ...(prompt
      ? [{ jobId, timestamp: promptTimestamp ?? "", kind: "message.user", content: prompt }]
      : []),
    ...rawEntries.filter((e) => {
      if (!e.content?.trim() && !isToolOutcome(e.kind) && e.kind !== "tool.call.started") return false;
      if (prompt && e.kind === "message.user" && e.content === prompt) return false;
      return true;
    }),
  ], [rawEntries, jobId, prompt, promptTimestamp]);

  const sessions = useStore(selectSecondarySessions(jobId));
  const sessionList = useMemo(() => Object.values(sessions), [sessions]);
  const handoffs = useStore(selectContextHandoffs(jobId));

  const baseFeedItems = useMemo(
    () => buildFeedItems(entries, jobApprovals, jobBatchApprovals),
    [entries, jobApprovals, jobBatchApprovals],
  );

  const feedItems = useMemo(() => {
    if (sessionList.length === 0 && handoffs.length === 0) return baseFeedItems;

    // Interleave secondary sessions and context handoffs by timestamp.
    const merged: FeedItem[] = [];
    let sessionIdx = 0;
    let handoffIdx = 0;
    const sorted = [...sessionList].sort((a, b) => a.startedAt.localeCompare(b.startedAt));

    for (const item of baseFeedItems) {
      // Insert any sessions that started before this feed item's timestamp
      const itemTs = item.type === "turn" || item.type === "condensed"
        ? item.turn.firstTimestamp
        : item.type === "operator" || item.type === "divider"
          ? item.entry.timestamp
          : item.type === "approval"
            ? item.approval.requestedAt
            : item.type === "batch_approval"
              ? item.batch.requestedAt
              : "";

      while (sessionIdx < sorted.length && sorted[sessionIdx]!.startedAt <= itemTs) {
        merged.push({ type: "secondary_session", session: sorted[sessionIdx]! });
        sessionIdx++;
      }
      // Insert handoffs that occurred before this feed item
      while (handoffIdx < handoffs.length && handoffs[handoffIdx]!.timestamp <= itemTs) {
        merged.push({ type: "context_handoff", handoff: handoffs[handoffIdx]! });
        handoffIdx++;
      }
      merged.push(item);
    }
    // Append remaining sessions at the end
    while (sessionIdx < sorted.length) {
      merged.push({ type: "secondary_session", session: sorted[sessionIdx]! });
      sessionIdx++;
    }
    // Append remaining handoffs at the end
    while (handoffIdx < handoffs.length) {
      merged.push({ type: "context_handoff", handoff: handoffs[handoffIdx]! });
      handoffIdx++;
    }
    return merged;
  }, [baseFeedItems, sessionList, handoffs]);

  // Virtualizer — auto-scroll only when user is at the bottom.
  const viewportRef = useRef<HTMLDivElement>(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const isAtBottomRef = useRef(true);

  const virtualizer = useVirtualizer({
    count: feedItems.length,
    getScrollElement: () => viewportRef.current,
    estimateSize: () => 120,
    overscan: 5,
  });

  // Phase 2: Track which feed items existed at hydration time.
  const hydratedCountRef = useRef(feedItems.length);
  useEffect(() => {
    hydratedCountRef.current = feedItems.length;
  }, [jobId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-scroll: when user is at the bottom and new items arrive, follow them.
  useEffect(() => {
    if (isAtBottomRef.current && feedItems.length > 0) {
      virtualizer.scrollToIndex(feedItems.length - 1, { align: "end" });
    }
  }, [feedItems.length, virtualizer]);

  // Scroll to a specific feed item when scrollToSeq is set
  const [highlightRange, setHighlightRange] = useState<{ start: number; end: number } | null>(null);
  const handledSeqRef = useRef<number | null>(null);
  useEffect(() => {
    if (scrollToSeq == null) { handledSeqRef.current = null; return; }
    if (feedItems.length === 0) return;
    if (handledSeqRef.current === scrollToSeq) return;
    const idx = feedItems.findIndex((item) => {
      if (item.type === "turn" || item.type === "condensed") {
        return item.turn.toolCalls.some((tc) => tc.sequence === scrollToSeq);
      }
      if (item.type === "operator" || item.type === "divider") {
        return item.entry.sequence === scrollToSeq;
      }
      return false;
    });
    if (idx >= 0) {
      handledSeqRef.current = scrollToSeq;
      virtualizer.scrollToIndex(idx, { align: "start", behavior: "smooth" });
      setTimeout(() => setHighlightRange({ start: idx, end: idx }), 300);
    }
  }, [scrollToSeq, feedItems, virtualizer]);

  // Scroll to a specific turn when scrollToTurnId is set
  const handledTurnIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (scrollToTurnId == null) { handledTurnIdRef.current = null; return; }
    if (feedItems.length === 0) return;
    if (handledTurnIdRef.current === scrollToTurnId) return;
    const idx = feedItems.findIndex((item) => {
      if (item.type === "turn" || item.type === "condensed") {
        return item.turn.turnId === scrollToTurnId;
      }
      return false;
    });
    if (idx >= 0) {
      handledTurnIdRef.current = scrollToTurnId;
      // Find end of range: next step's first feed item (exclusive), or end of feed
      let endIdx = idx;
      if (stepTurnIds && stepTurnIds.length > 0) {
        const stepPos = stepTurnIds.indexOf(scrollToTurnId);
        const nextStepTurnId = stepPos >= 0 && stepPos < stepTurnIds.length - 1
          ? stepTurnIds[stepPos + 1]
          : null;
        if (nextStepTurnId) {
          const nextIdx = feedItems.findIndex((item, i) =>
            i > idx && (item.type === "turn" || item.type === "condensed") && item.turn.turnId === nextStepTurnId
          );
          endIdx = nextIdx > idx ? nextIdx - 1 : feedItems.length - 1;
        } else {
          endIdx = feedItems.length - 1;
        }
      }
      virtualizer.scrollToIndex(idx, { align: "start", behavior: "smooth" });
      setTimeout(() => setHighlightRange({ start: idx, end: endIdx }), 300);
    }
  }, [scrollToTurnId, feedItems, virtualizer, stepTurnIds]);

  // Phase 4: Track topmost visible turnId for bidirectional sidebar linking
  const onVisibleTurnIdRef = useRef(onVisibleTurnId);
  onVisibleTurnIdRef.current = onVisibleTurnId;
  const lastVisibleTurnIdRef = useRef<string | null>(null);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
    isAtBottomRef.current = atBottom;
    setShowScrollBtn(!atBottom);

    const vItems = virtualizer.getVirtualItems();
    if (vItems.length > 0) {
      const topTurnId = getTurnIdForIndexRef.current(vItems[0]!.index);
      if (topTurnId !== lastVisibleTurnIdRef.current) {
        lastVisibleTurnIdRef.current = topTurnId;
        onVisibleTurnIdRef.current?.(topTurnId);
      }
    }
  };

  const scrollToBottom = useCallback(() => {
    if (feedItems.length > 0) {
      virtualizer.scrollToIndex(feedItems.length - 1, { align: "end", behavior: "smooth" });
      setShowScrollBtn(false);
    }
  }, [feedItems.length, virtualizer]);

  // Message composer state
  const [msg, setMsg] = useState("");
  const [sending, setSending] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const waveformContainerRef = useRef<HTMLDivElement>(null);

  const isReview = jobState === "review";
  const isTerminal = ["completed", "failed", "canceled"].includes(jobState ?? "");

  const handleSend = useCallback(async () => {
    if (!msg.trim() || !jobId || sending) return;
    const text = msg.trim();
    setMsg("");
    setSending(true);

    // Optimistic update: show the operator message immediately in the transcript
    const optimisticEntry: TranscriptEntry = {
      jobId,
      timestamp: new Date().toISOString(),
      kind: "message.user",
      content: text,
    };
    const state = useStore.getState();
    const existing = state.transcript[jobId] ?? [];
    useStore.setState({
      transcript: { ...state.transcript, [jobId]: [...existing, optimisticEntry] },
    });

    try {
      if (isTerminal) {
        const followup = await continueJob(jobId, text);
        toast.success("Follow-up job started");
        navigate(`/jobs/${followup.id}`);
      } else if (isReview) {
        try {
          await sendOperatorMessage(jobId, text);
        } catch {
          await resumeJob(jobId, text);
          toast.success("Job resumed");
        }
      } else {
        await sendOperatorMessage(jobId, text);
      }
    } catch (err) {
      // Rollback optimistic entry on failure
      const cur = useStore.getState();
      const curEntries = cur.transcript[jobId] ?? [];
      useStore.setState({
        transcript: { ...cur.transcript, [jobId]: curEntries.filter((e) => e !== optimisticEntry) },
      });
      setMsg(text);
      const detail =
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : "Unknown error";
      toast.error(`Failed to send: ${detail}`);
      console.error("Failed to send message:", err);
    } finally {
      setSending(false);
    }
  }, [msg, jobId, sending, isReview, isTerminal, navigate]);

  const handlePause = useCallback(async () => {
    if (!jobId) return;
    setPausing(true);
    try {
      await pauseJob(jobId);
      toast.info("Agent paused");
    } catch (err) {
      toast.error("Failed to pause");
      console.error("Failed to pause job:", err);
    } finally {
      setPausing(false);
    }
  }, [jobId]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Search
  const [debouncedQuery, setDebouncedQuery] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(searchQuery), 150);
    return () => clearTimeout(t);
  }, [searchQuery]);

  const matchingIndices = useMemo<Set<number> | null>(() => {
    const q = debouncedQuery.trim().toLowerCase();
    if (!q) return null;
    const set = new Set<number>();
    feedItems.forEach((item, idx) => {
      let match = false;
      if (item.type === "operator") match = !!item.entry.content?.toLowerCase().includes(q);
      else if (item.type === "turn" || item.type === "condensed") {
        const turn = item.turn;
        match = !!(turn.message?.content?.toLowerCase().includes(q)
          || turn.reasoning?.content?.toLowerCase().includes(q)
          || turn.toolCalls.some((t) =>
            t.toolDisplay?.toLowerCase().includes(q)
            || t.toolName?.toLowerCase().includes(q)
          )
        );
      } else if (item.type === "approval") match = item.approval.description.toLowerCase().includes(q);
      else if (item.type === "batch_approval") match = item.batch.summary.toLowerCase().includes(q);

      else match = true;
      if (match) set.add(idx);
    });
    return set;
  }, [feedItems, debouncedQuery]);

  const matchCount = matchingIndices !== null ? matchingIndices.size : null;
  const activeHighlight = debouncedQuery.trim().toLowerCase();

  const EMPTY_MATCH_LIST: number[] = [];
  const matchList = useMemo(() => {
    if (!matchingIndices || matchingIndices.size === 0) return EMPTY_MATCH_LIST;
    return Array.from(matchingIndices).sort((a, b) => a - b);
  }, [matchingIndices]); // eslint-disable-line react-hooks/exhaustive-deps

  const [currentMatchPos, setCurrentMatchPos] = useState(0);

  const getTurnIdForIndex = useCallback((idx: number): string | null => {
    for (let i = idx; i >= 0; i--) {
      const item = feedItems[i];
      if (!item) continue;
      if (item.type === "turn" || item.type === "condensed") {
        return item.turn.turnId ?? null;
      }
    }
    return null;
  }, [feedItems]);

  const onSearchHighlightRef = useRef(onSearchHighlight);
  onSearchHighlightRef.current = onSearchHighlight;
  const getTurnIdForIndexRef = useRef(getTurnIdForIndex);
  getTurnIdForIndexRef.current = getTurnIdForIndex;

  const lastJumpedQueryRef = useRef("");
  useEffect(() => {
    const q = debouncedQuery.trim();
    if (!q) { lastJumpedQueryRef.current = ""; onSearchHighlightRef.current?.(null); return; }
    if (matchList.length === 0) return;
    if (lastJumpedQueryRef.current === q) return;
    lastJumpedQueryRef.current = q;
    setCurrentMatchPos(0);
    const first = matchList[0]!;
    virtualizer.scrollToIndex(first, { align: "center" });
    setHighlightRange({ start: first, end: first });
    onSearchHighlightRef.current?.(getTurnIdForIndexRef.current(first));
  }, [debouncedQuery, matchList]); // eslint-disable-line react-hooks/exhaustive-deps

  const jumpToMatch = useCallback((pos: number) => {
    if (matchList.length === 0) return;
    const clamped = ((pos % matchList.length) + matchList.length) % matchList.length;
    setCurrentMatchPos(clamped);
    const feedIdx = matchList[clamped]!;
    virtualizer.scrollToIndex(feedIdx, { align: "center" });
    setHighlightRange({ start: feedIdx, end: feedIdx });
    onSearchHighlightRef.current?.(getTurnIdForIndexRef.current(feedIdx));
  }, [matchList, virtualizer]);

  const nextMatch = useCallback(() => jumpToMatch(currentMatchPos + 1), [jumpToMatch, currentMatchPos]);
  const prevMatch = useCallback(() => jumpToMatch(currentMatchPos - 1), [jumpToMatch, currentMatchPos]);

  const searchInputRef = useRef<HTMLInputElement>(null);
  useHotkeys("mod+f", (e) => { e.preventDefault(); setSearchOpen(true); setTimeout(() => searchInputRef.current?.focus(), 0); }, { enableOnFormTags: true });
  useHotkeys("Escape", () => { if (searchOpen) { setSearchOpen(false); setSearchQuery(""); onSearchHighlightRef.current?.(null); } }, { enableOnFormTags: true });

  return (
    <div className="flex flex-col h-full relative">
      <div
        ref={viewportRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto rounded-lg border border-border bg-card"
      >
        {/* Inline search */}
        <div className="sticky top-0 z-10 px-3 sm:px-4 pt-2.5 pb-6" style={{ background: "linear-gradient(to bottom, hsl(var(--card)) 40%, hsl(var(--card) / 0) 100%)" }}>
          <div
            className={cn(
              "flex items-center gap-2 transition-colors cursor-text",
              searchOpen ? "text-foreground" : "text-muted-foreground/40 hover:text-muted-foreground/60",
            )}
            onClick={() => { if (!searchOpen) { setSearchOpen(true); setTimeout(() => searchInputRef.current?.focus(), 0); } }}
          >
            <Search size={13} className="shrink-0" />
            {searchOpen ? (
              <>
                <input
                  ref={searchInputRef}
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") { e.preventDefault(); if (e.shiftKey) { prevMatch(); } else { nextMatch(); } }
                  }}
                  placeholder="Search transcript…"
                  className="flex-1 bg-transparent text-base sm:text-sm text-foreground outline-none placeholder:text-muted-foreground/40"
                  autoFocus
                />
                {matchCount !== null && (
                  <span className="text-[11px] tabular-nums text-muted-foreground/60 shrink-0">
                    {matchCount > 0 ? `${currentMatchPos + 1}/${matchCount}` : "0 results"}
                  </span>
                )}
                {matchCount !== null && matchCount > 0 && (
                  <div className="flex items-center shrink-0">
                    <Tooltip content="Previous match (Shift+Enter)">
                      <button onClick={prevMatch} className="p-1.5 min-h-[44px] min-w-[32px] flex items-center justify-center text-muted-foreground/50 hover:text-muted-foreground" aria-label="Previous match">
                        <ChevronUp size={14} />
                      </button>
                    </Tooltip>
                    <Tooltip content="Next match (Enter)">
                      <button onClick={nextMatch} className="p-1.5 min-h-[44px] min-w-[32px] flex items-center justify-center text-muted-foreground/50 hover:text-muted-foreground" aria-label="Next match">
                        <ChevronDown size={14} />
                      </button>
                    </Tooltip>
                  </div>
                )}
                <Tooltip content="Close search">
                  <button onClick={() => { setSearchOpen(false); setSearchQuery(""); onSearchHighlightRef.current?.(null); }} className="p-1.5 min-h-[44px] sm:min-h-0 min-w-[44px] sm:min-w-0 flex items-center justify-center text-muted-foreground/40 hover:text-muted-foreground shrink-0">
                    <X size={14} />
                  </button>
                </Tooltip>
              </>
            ) : (
              <>
                <span className="flex-1 text-base sm:text-sm">Search…</span>
                <kbd className="hidden sm:inline text-[10px] text-muted-foreground/30 font-mono shrink-0">{navigator.platform.includes("Mac") ? "⌘" : "Ctrl"}+F</kbd>
              </>
            )}
          </div>
          <div className="mx-2 mt-2.5 mb-1 border-b border-border/40" />
        </div>

        <div
          style={{ height: virtualizer.getTotalSize(), position: "relative" }}
        >
          {virtualizer.getVirtualItems().map((vItem) => {
            const item = feedItems[vItem.index];
            if (!item) return null;
            const dimmed = matchingIndices !== null && !matchingIndices.has(vItem.index);
            const isActiveMatch = highlightRange !== null && vItem.index >= highlightRange.start && vItem.index <= highlightRange.end;
            const isLastInRange = highlightRange !== null && vItem.index === highlightRange.end;
            const isNew = vItem.index >= hydratedCountRef.current;

            return (
              <div
                key={vItem.key}
                ref={virtualizer.measureElement}
                data-index={vItem.index}
                className={cn(
                  isActiveMatch && "animate-glow-flicker",
                  isNew && !isActiveMatch && "animate-feed-enter",
                )}
                onAnimationEnd={isLastInRange ? () => {
                  hydratedCountRef.current = Math.max(hydratedCountRef.current, highlightRange.end + 1);
                  setHighlightRange(null);
                } : undefined}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  transform: `translateY(${vItem.start}px)`,
                  ...(isActiveMatch ? { zIndex: 1 } : {}),
                  ...(dimmed ? { opacity: 0.15, pointerEvents: "none" as const } : {}),
                }}
              >
                <div className={cn(
                "px-3 sm:px-4 overflow-x-hidden transition-colors",
                visibleStepTurnId && (item.type === "turn" || item.type === "condensed") && item.turn.turnId === visibleStepTurnId && "border-l-2 border-primary/30 pl-2 sm:pl-3",
              )}>
                  <SearchHighlightCtx.Provider value={activeHighlight}>
                    <FeedItemRenderer
                      item={item}
                      jobId={jobId}
                      sdk={sdk}
                      streamingMessages={streamingMessages}
                      streamingReasoning={allStreamingReasoning}
                      isJobLive={isJobLive}
                      onViewStepChanges={onViewStepChanges}
                    />
                  </SearchHighlightCtx.Provider>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Scroll-to-bottom */}
      {showScrollBtn && (
        <div className="absolute bottom-20 left-1/2 -translate-x-1/2 z-10">
          <button
            onClick={scrollToBottom}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-card/90 border border-border/50 text-xs text-muted-foreground shadow-lg hover:text-foreground transition-colors"
          >
            <ArrowDown size={12} />
            Jump to bottom
          </button>
        </div>
      )}

      {/* Phase 1: Agent activity pulse */}
      <AgentActivityBar jobId={jobId} sdk={sdk} jobState={jobState} />

      {/* Phase 1: Stateful message composer */}
      {interactive && (
        <div className={cn(
          "rounded-lg border bg-card px-3 py-2 mt-1 transition-colors",
          jobState === "waiting_for_approval" ? "border-amber-600/40" :
          isReview ? "border-primary/40" :
          isTerminal ? "border-border border-dashed" :
          "border-border",
        )}>
          <div className="flex items-end gap-2">
            <div className="flex-1 relative">
              <textarea
                value={msg}
                onChange={(e) => setMsg(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={
                  jobState === "waiting_for_approval" ? "The agent needs your decision above \u2191" :
                  isReview ? "Send follow-up to resume\u2026" :
                  isTerminal ? "Start a follow-up job\u2026" :
                  "Message the agent\u2026"
                }
                rows={1}
                className="w-full resize-none bg-transparent text-base sm:text-sm text-foreground placeholder:text-muted-foreground/30 outline-none py-2 pr-8 max-h-32"
                style={{ minHeight: "2.25rem" }}
                disabled={sending}
              />
              <div ref={waveformContainerRef} />
            </div>
            <div className="flex items-center gap-1 pb-1.5">
              <MicButton
                onStateChange={() => {}}
                waveformContainerRef={waveformContainerRef}
                onTranscript={(text: string) => setMsg((prev: string) => prev + text)}
              />
              {pausable && isJobLive && jobState === "running" && (
                <Tooltip content="Pause the agent after the current step">
                  <button
                    onClick={handlePause}
                    disabled={pausing}
                    className="p-1.5 text-muted-foreground/40 hover:text-amber-400 transition-colors"
                  >
                    <PauseCircle size={15} />
                  </button>
                </Tooltip>
              )}
              <Tooltip content={isReview ? "Resume job with follow-up" : isTerminal ? "Create follow-up job" : "Send message to the agent"}>
                <button
                  onClick={handleSend}
                  disabled={!msg.trim() || sending}
                  className={cn(
                    "p-1.5 rounded-md transition-colors",
                    msg.trim() ? "text-primary hover:bg-primary/10" : "text-muted-foreground/20",
                  )}
                >
                  {sending ? <Spinner className="w-4 h-4" /> : <Send size={15} />}
                </button>
              </Tooltip>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Feed item dispatch
// ---------------------------------------------------------------------------

const FeedItemRenderer = memo(function FeedItemRenderer({
  item,
  jobId,
  sdk,
  streamingMessages,
  streamingReasoning,
  isJobLive,
  onViewStepChanges,
}: {
  item: FeedItem;
  jobId: string;
  sdk?: string;
  streamingMessages: Record<string, string>;
  streamingReasoning: Record<string, string>;
  isJobLive: boolean;
  onViewStepChanges?: (filePaths: string[], label: string, scrollToSeq?: number, turnId?: string) => void;
}) {
  switch (item.type) {
    case "operator":
      return <OperatorMessage entry={item.entry} />;
    case "turn": {
      const streamKey = item.turn.turnId ? `${jobId}:${item.turn.turnId}` : `${jobId}:__default__`;
      const streamingText = isJobLive ? streamingMessages[streamKey] : undefined;
      const streamingReasoningText = isJobLive ? streamingReasoning[streamKey] : undefined;
      const isStreaming = !!streamingText && !item.turn.message?.content;
      return (
        <AgentTurnBlock
          turn={item.turn}
          clusters={item.clusters}
          sdk={sdk}
          isStreaming={isStreaming}
          streamingText={streamingText}
          streamingReasoningText={streamingReasoningText}
          onViewStepChanges={onViewStepChanges}
        />
      );
    }
    case "condensed":
      return <CondensedTurnBlock turn={item.turn} clusters={item.clusters} sdk={sdk} onViewStepChanges={onViewStepChanges} />;
    case "approval":
      return <InlineApprovalCard approval={item.approval} />;
    case "batch_approval":
      return <InlineBatchApprovalCard batch={item.batch} />;
    case "secondary_session":
      return <SecondarySessionCard session={item.session} />;
    case "context_handoff":
      return <HandoffCard handoff={item.handoff} />;

    case "divider":
      return <DividerLine entry={item.entry} />;
    default:
      return null;
  }
});
