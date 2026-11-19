import { useState, useRef, useEffect, useCallback, forwardRef } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { ExternalLink, AlertTriangle, ArrowDownCircle, Loader2, Coins, FolderGit2 } from "lucide-react";
import type { JobSummary } from "../store";
import { Tooltip } from "./ui/tooltip";
import { cn, isActiveSetupStep, setupStepLabel } from "../lib/utils";
import { pathBasename } from "../lib/paths";
import { jobCostLabel, jobCostWithTokens } from "../lib/jobCost";

interface MetadataChipStripProps {
  job: JobSummary;
  hasMergeConflict: boolean;
  className?: string;
  onCostClick?: () => void;
}

const Chip = forwardRef<HTMLSpanElement, { children: React.ReactNode; className?: string; onClick?: (e: React.MouseEvent) => void }>(
  function Chip({ children, className, onClick }, ref) {
    return (
      <span ref={ref} onClick={onClick} className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium bg-muted text-muted-foreground whitespace-nowrap", className)}>
        {children}
      </span>
    );
  },
);

function CostChip({ job, onCostClick }: { job: JobSummary; onCostClick?: () => void }) {
  const [showTooltip, setShowTooltip] = useState(false);
  const chipRef = useRef<HTMLButtonElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ top: 0, left: 0 });

  const close = useCallback(() => setShowTooltip(false), []);

  // Position tooltip below chip when shown
  useEffect(() => {
    if (!showTooltip || !chipRef.current) return;
    const rect = chipRef.current.getBoundingClientRect();
    setPos({ top: rect.bottom + 4, left: rect.left });
  }, [showTooltip]);

  useEffect(() => {
    if (!showTooltip) return;
    const handler = (e: MouseEvent | TouchEvent) => {
      if (
        chipRef.current?.contains(e.target as Node) ||
        tooltipRef.current?.contains(e.target as Node)
      ) return;
      close();
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("touchstart", handler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("touchstart", handler);
    };
  }, [showTooltip, close]);

  const label = jobCostWithTokens(job);

  return (
    <span
      className="inline-flex"
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <button
        ref={chipRef}
        type="button"
        className={cn(
          "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
          "bg-emerald-500/15 text-emerald-400",
          onCostClick && "cursor-pointer hover:bg-emerald-500/25 transition-colors",
        )}
        onClick={onCostClick}
        aria-label="View cost breakdown"
      >
        <Coins size={10} />
        {label}
      </button>
      {showTooltip && createPortal(
        <div
          ref={tooltipRef}
          role="tooltip"
          style={{ position: "fixed", top: pos.top, left: pos.left }}
          className="z-50 w-52 rounded-lg border border-border bg-popover p-2.5 shadow-lg text-[11px] text-popover-foreground"
          onMouseEnter={() => setShowTooltip(true)}
          onMouseLeave={() => setShowTooltip(false)}
        >
          <div className="space-y-1">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Reported usage</span>
              <span className="font-medium">{jobCostLabel(job)}</span>
            </div>
            {!!job.costUsage?.length && job.totalCostUsd != null && (
              <div className="flex justify-between gap-2">
                <span className="text-muted-foreground">Legacy aggregate (unit unverified)</span>
                <span>{job.totalCostUsd}</span>
              </div>
            )}
            {job.inputTokens != null && job.inputTokens > 0 && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Input tokens</span>
                <span>{formatTokens(job.inputTokens)}</span>
              </div>
            )}
            {job.totalTokens != null && job.inputTokens != null && job.outputTokens != null && (job.totalTokens - job.inputTokens - job.outputTokens) > 0 && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Cache read <Tooltip content="Tokens served from prompt cache — reused context billed at a reduced rate"><span className="cursor-help">ⓘ</span></Tooltip></span>
                <span>{formatTokens(job.totalTokens - job.inputTokens - job.outputTokens)}</span>
              </div>
            )}
            {job.outputTokens != null && job.outputTokens > 0 && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Output tokens</span>
                <span>{formatTokens(job.outputTokens)}</span>
              </div>
            )}
            {job.totalTokens != null && job.totalTokens > 0 && (
              <div className="flex justify-between border-t border-border pt-1 mt-1">
                <span className="text-muted-foreground">Total tokens</span>
                <span className="font-medium">{formatTokens(job.totalTokens)}</span>
              </div>
            )}
          </div>
          <p className="mt-2 text-[10px] text-muted-foreground/70 leading-tight">
            Native units are not dollars. USD is shown only with verified currency provenance.
            Historical aggregates have unverified units and are not converted.
          </p>
          {onCostClick && (
            <p className="mt-1 text-[10px] text-primary/70">Click for full breakdown →</p>
          )}
        </div>,
        document.body,
      )}
    </span>
  );
}

export function MetadataChipStrip({ job, hasMergeConflict, className, onCostClick }: MetadataChipStripProps) {
  const isPreparing = job.state === "preparing";
  const repoName = pathBasename(job.repo) || job.repo || "External observation";
  const navigate = useNavigate();

  return (
    <div className={cn("flex items-center gap-1.5 overflow-x-auto scrollbar-none", className)}>
      <Chip
        className={job.repo && !job.observationOnly ? "cursor-pointer hover:bg-muted/80" : undefined}
        onClick={job.repo && !job.observationOnly ? (e: React.MouseEvent) => {
          e.stopPropagation();
          if (job.repo) navigate(`/projects/${encodeURIComponent(job.repo)}`);
        } : undefined}
      ><FolderGit2 size={10} />{repoName}</Chip>
      {job.branch && !job.observationOnly && (
        <Chip>{job.branch} → {job.baseRef}</Chip>
      )}
      {job.model && <Chip>{job.model}</Chip>}
      <Chip>{timeAgo(job.createdAt)}</Chip>
      {job.prUrl && (
        <Tooltip content="Open the pull request on GitHub">
          <a href={job.prUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium bg-muted text-primary hover:underline whitespace-nowrap">
            <ExternalLink size={10} /> PR
          </a>
        </Tooltip>
      )}
      {/* Cost / token chip */}
      {jobCostLabel(job) != null && (
        <CostChip job={job} onCostClick={onCostClick} />
      )}
      {/* Status chips */}
      {job.modelDowngraded && (
        <Tooltip content={`Requested ${job.requestedModel ?? "model"} but received ${job.actualModel ?? "a different model"}`}>
          <Chip className="bg-amber-500/15 text-amber-500">
            <ArrowDownCircle size={10} /> Downgraded
          </Chip>
        </Tooltip>
      )}
      {hasMergeConflict && (
        <Tooltip content="Agent's changes conflict with the target branch — manual resolution needed">
          <Chip className="bg-amber-500/15 text-amber-500">
            <AlertTriangle size={10} /> Conflict
          </Chip>
        </Tooltip>
      )}
      {job.state === "failed" && (
        <Chip className="bg-red-500/15 text-red-500">
          Failed{job.failureReason ? `: ${job.failureReason.slice(0, 40)}` : ""}
        </Chip>
      )}
      {(isPreparing || isActiveSetupStep(job.setupStep)) && (
        <Chip className="bg-violet-500/15 text-violet-400">
          <Loader2 size={10} className="animate-spin" />
          {setupStepLabel(job.setupStep)}
        </Chip>
      )}
    </div>
  );
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function formatTokens(tokens: number): string {
  return tokens.toLocaleString();
}
