import { useState, useEffect } from "react";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  XCircle,
  CheckCircle2,
  AlertTriangle,
  ArrowDownCircle,
  GitMerge,
} from "lucide-react";
import type { JobSummary } from "../store";
import { StateBadge } from "./StateBadge";
import { SdkBadge } from "./SdkBadge";
import { MetadataChipStrip } from "./MetadataChipStrip";
import { JobActions, type JobActionsProps } from "./JobActions";
import { ConnectionStatusIndicator } from "./ConnectionStatusIndicator";
import { NavMenuSlideout } from "./NavMenuSlideout";
import { BottomSheet } from "./ui/bottom-sheet";
import { Tooltip } from "./ui/tooltip";
import { isActiveSetupStep, setupStepLabel } from "../lib/utils";

/** States where the card should default to expanded (user needs context/actions). */
const EXPAND_STATES = new Set(["review", "failed", "canceled", "completed"]);

/** Top accent color keyed by job state. */
const ACCENT: Record<string, string> = {
  preparing: "border-t-violet-500",
  queued: "border-t-yellow-500",
  running: "border-t-blue-500",
  waiting_for_approval: "border-t-orange-500",
  review: "border-t-cyan-500",
  completed: "border-t-green-500",
  failed: "border-t-red-500",
  canceled: "border-t-gray-400",
};

interface JobHeaderCardProps {
  job: JobSummary;
  isPreparing: boolean;
  hasMergeConflict: boolean;
  onNavigateHome: () => void;
  onCostClick: () => void;
  actionProps: Omit<JobActionsProps, "layout">;
}

export function JobHeaderCard({
  job,
  isPreparing,
  hasMergeConflict,
  onNavigateHome,
  onCostClick,
  actionProps,
}: JobHeaderCardProps) {
  const shouldAutoExpand = EXPAND_STATES.has(job.state);
  const [expanded, setExpanded] = useState(shouldAutoExpand);
  const [userOverride, setUserOverride] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);

  useEffect(() => {
    if (!userOverride) {
      setExpanded(EXPAND_STATES.has(job.state));
    }
  }, [job.state, userOverride]);

  const toggleDesktop = () => {
    setExpanded((e) => !e);
    setUserOverride(true);
  };

  const isActive = ["running", "agent_running", "queued"].includes(job.state);
  const accent = ACCENT[job.state] ?? "border-t-gray-500/40";
  const statusBanner = (() => {
    if (job.modelDowngraded) {
      return (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2">
          <div className="flex items-start gap-2 text-amber-500">
            <ArrowDownCircle size={14} className="mt-0.5 shrink-0" />
            <div>
              <p className="text-sm font-medium">Model downgraded</p>
              <p className="text-xs text-amber-400">
                {job.requestedModel} → {job.actualModel}
              </p>
            </div>
          </div>
        </div>
      );
    }

    if (job.state === "review") {
      const isConflict = hasMergeConflict;
      const isSignOff = actionProps.needsResolution && actionProps.hasChanges && !isConflict;
      return (
        <div className={`rounded-md border px-3 py-2 ${isConflict ? "border-amber-500/30 bg-amber-500/10" : isSignOff ? "border-blue-500/30 bg-blue-500/10" : "border-green-500/30 bg-green-500/10"}`}>
          <div className={`flex items-start gap-2 ${isConflict ? "text-amber-500" : isSignOff ? "text-blue-500" : "text-green-500"}`}>
            <GitMerge size={14} className="mt-0.5 shrink-0" />
            <div>
              <p className="text-sm font-medium">
                {isConflict ? "Merge conflict" : isSignOff ? "Review required" : "Ready"}
              </p>
            </div>
          </div>
        </div>
      );
    }

    if (job.state === "failed") {
      return (
        <div className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2">
          <div className="flex items-start gap-2 text-red-500">
            <XCircle size={14} className="mt-0.5 shrink-0" />
            <div>
              <p className="text-sm font-medium">Job failed</p>
              <p className="text-xs text-red-400 whitespace-pre-wrap break-words">
                {job.failureReason ?? "No additional details available"}
              </p>
            </div>
          </div>
        </div>
      );
    }

    if (job.state === "canceled") {
      return (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2">
          <div className="flex items-start gap-2 text-amber-500">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            <div>
              <p className="text-sm font-medium">Job canceled</p>
            </div>
          </div>
        </div>
      );
    }

    if (job.state === "completed") {
      return (
        <div className="rounded-md border border-green-500/30 bg-green-500/10 px-3 py-2">
          <div className="flex items-start gap-2 text-green-600">
            <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
            <div>
              <p className="text-sm font-medium">Job completed</p>
              <p className="text-xs text-green-600/80">
                {job.resolution === "merged" && "Changes merged into base branch"}
                {job.resolution === "pr_created" && "Pull request created"}
                {job.resolution === "discarded" && "Changes discarded"}
                {job.resolution === "conflict" && "Merge conflict — needs manual resolution"}
              </p>
            </div>
          </div>
        </div>
      );
    }

    return null;
  })();

  return (
    <>
      {/* ────────────────────────────────────────────────────────────────── */}
      {/* Mobile (< md): compact accent rail — tap title to open sheet     */}
      {/* ────────────────────────────────────────────────────────────────── */}
      <div className={`md:hidden shrink-0 border-t-2 ${accent} border-b border-border/50 bg-card shadow-sm`}>
        <div className="flex items-center gap-2.5 h-11 px-3">
          <button onClick={onNavigateHome} className="shrink-0 hover:opacity-80 transition-opacity" aria-label="Back to dashboard">
            <img src="/mark.png" alt="" className="h-6 w-6 object-contain brightness-110 drop-shadow-[0_0_3px_rgba(255,255,255,0.08)]" />
          </button>

          <button onClick={() => setSheetOpen(true)} className="flex items-center gap-1.5 min-w-0">
            <h1 className="text-sm font-semibold text-foreground truncate">{job.title || job.id}</h1>
          </button>

          <span aria-live="polite"><StateBadge state={job.observationOnly ? "observing" : job.state} /></span>

          <div className="flex-1" />
          <div className="flex items-center gap-1.5 shrink-0">
            <ConnectionStatusIndicator />
            <NavMenuSlideout />
          </div>
        </div>
      </div>

      {/* Mobile bottom sheet — full job detail view */}
      <BottomSheet open={sheetOpen} onClose={() => setSheetOpen(false)} title="Job Details">
        <div className="space-y-3">
          <div>
            <h2 className="text-base font-bold text-foreground break-words">{job.title || job.id}</h2>
            {job.title && <p className="text-xs text-muted-foreground font-mono mt-0.5">{job.id}</p>}
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <StateBadge state={job.observationOnly ? "observing" : job.state} />
            <SdkBadge sdk={job.sdk} />
          </div>

          {(job.description || job.prompt) && (
            <p className="text-sm text-muted-foreground">{job.description ?? job.prompt}</p>
          )}

          {job.progressHeadline && isActive && (
            <p className="text-sm italic text-primary/70">{job.progressHeadline}</p>
          )}
          {(isPreparing || isActiveSetupStep(job.setupStep)) && (
            <div className="flex items-center gap-2 text-sm text-violet-400 animate-pulse">
              <Loader2 size={14} className="animate-spin" />
              {setupStepLabel(job.setupStep)}
            </div>
          )}

          {statusBanner}

          <MetadataChipStrip job={job} hasMergeConflict={hasMergeConflict} onCostClick={() => { setSheetOpen(false); onCostClick(); }} />

          <div className="pt-2 border-t border-border">
            <JobActions
              {...actionProps}
              onCancelOpen={() => { setSheetOpen(false); actionProps.onCancelOpen(); }}
              onResume={() => { setSheetOpen(false); actionProps.onResume(); }}
              onResolve={(a) => { setSheetOpen(false); actionProps.onResolve(a); }}
              onDiscardOpen={() => { setSheetOpen(false); actionProps.onDiscardOpen(); }}
              onMarkDoneOpen={() => { setSheetOpen(false); actionProps.onMarkDoneOpen(); }}
              onCompleteOpen={() => { setSheetOpen(false); actionProps.onCompleteOpen(); }}
              layout="full"
            />
          </div>
        </div>
      </BottomSheet>

      {/* ────────────────────────────────────────────────────────────────── */}
      {/* Desktop (>= md): top navigation bar                              */}
      {/* ────────────────────────────────────────────────────────────────── */}
      <div className="hidden md:flex items-center gap-3 shrink-0 mx-3 mt-2 px-4 h-10">
        <Tooltip content="Back to dashboard">
          <button onClick={onNavigateHome} className="shrink-0 hover:opacity-80 transition-opacity" aria-label="Back to dashboard">
            <img src="/mark.png" alt="" className="h-7 w-7 object-contain brightness-110 drop-shadow-[0_0_3px_rgba(255,255,255,0.08)]" />
          </button>
        </Tooltip>
        <div className="flex-1" />
        <div className="flex items-center gap-1.5 shrink-0">
          <ConnectionStatusIndicator />
          <NavMenuSlideout />
        </div>
      </div>

      {/* ────────────────────────────────────────────────────────────────── */}
      {/* Desktop (>= md): collapsible job overview card                   */}
      {/* ────────────────────────────────────────────────────────────────── */}
      <div className={`hidden md:block shrink-0 mx-3 mt-1.5 rounded-lg border-t-[3px] ${accent} border border-border ring-1 ring-white/[0.04] bg-card shadow-md`}>
        {/* ── Row 1: identity bar ── */}
        <div className="flex items-center gap-3 px-4 pt-3 pb-1.5">
          <button onClick={toggleDesktop} className="flex items-center gap-2 min-w-0 group">
            {expanded
              ? <ChevronDown size={16} className="text-muted-foreground shrink-0 group-hover:text-foreground transition-colors" />
              : <ChevronRight size={16} className="text-muted-foreground shrink-0 group-hover:text-foreground transition-colors" />}
            <h1 className="text-base lg:text-lg font-semibold text-foreground truncate">{job.title || job.id}</h1>
          </button>

          <span aria-live="polite"><StateBadge state={job.observationOnly ? "observing" : job.state} /></span>
          <SdkBadge sdk={job.sdk} />

          {/* Collapsed inline context */}
          {!expanded && (
            <span className="text-xs text-muted-foreground/70 truncate min-w-0">
              {job.progressHeadline && isActive
                ? job.progressHeadline
                : (isPreparing || isActiveSetupStep(job.setupStep))
                  ? setupStepLabel(job.setupStep)
                  : job.branch
                    ? `${job.branch} → ${job.baseRef}`
                    : null}
            </span>
          )}
          <div className="flex-1" />
          <JobActions {...actionProps} layout="bar" />
        </div>

        {!expanded && (job.state === "failed" || job.state === "review") && (
          <div className="px-4 pb-2">
            {statusBanner}
          </div>
        )}

        {/* ── Expanded body ── */}
        {expanded && (
          <div className="px-4 pb-3 space-y-2.5">
            {(job.description || job.prompt) && (
              <p className="text-sm text-foreground/60 line-clamp-2">{job.description ?? job.prompt}</p>
            )}

            {job.progressHeadline && isActive && (
              <p className="text-xs italic text-primary/70 truncate">
                ● {job.progressHeadline}
              </p>
            )}
            {(isPreparing || isActiveSetupStep(job.setupStep)) && (
              <p className="text-xs text-violet-400 animate-pulse flex items-center gap-1">
                <Loader2 size={12} className="animate-spin" />
                {setupStepLabel(job.setupStep)}
              </p>
            )}

            {statusBanner}

            <MetadataChipStrip job={job} hasMergeConflict={hasMergeConflict} onCostClick={onCostClick} />
          </div>
        )}
      </div>
    </>
  );
}
