import { ArrowLeft, Radio, RotateCcw, XCircle, GitMerge, GitPullRequest, Trash2, FolderTree, TerminalSquare, MoreHorizontal, ListTree, Package, BarChart3, CheckCircle2, Archive, ScanSearch } from "lucide-react";
import type { JobSummary } from "../store";
import { StateBadge } from "./StateBadge";
import { Button } from "./ui/button";
import * as PopoverPrimitive from "@radix-ui/react-popover";
import { cn } from "../lib/utils";

// ────────────────────────────────────────────────────────────────────────────
// MobileStatusRail — compact header bar visible only below md breakpoint
// ────────────────────────────────────────────────────────────────────────────

interface MobileStatusRailProps {
  job: JobSummary;
  onBack: () => void;
  onCancelOpen: () => void;
  onResume: () => void;
  onOpenTerminal: () => void;
  canCancel: boolean;
  canResume: boolean;
  hasWorktree: boolean;
  jobTerminalCount: number;
  // Action props (for overflow menu)
  needsResolution: boolean;
  hasChanges: boolean;
  hasMergeConflict: boolean;
  isResolved: boolean;
  canArchive: boolean;
  resolveLoading: string | null;
  onResolve: (action: "merge" | "smart_merge" | "create_pr" | "agent_merge") => void;
  onDiscardOpen: () => void;
  onMarkDoneOpen: () => void;
  onCompleteOpen: () => void;
}

export function MobileStatusRail({
  job,
  onBack,
  onCancelOpen,
  onResume,
  onOpenTerminal,
  canCancel,
  canResume,
  hasWorktree,
  jobTerminalCount,
  needsResolution,
  hasChanges,
  hasMergeConflict,
  isResolved,
  canArchive,
  resolveLoading,
  onResolve,
  onDiscardOpen,
  onMarkDoneOpen,
  onCompleteOpen,
}: MobileStatusRailProps) {
  return (
    <div className="flex md:hidden items-center gap-2 h-10 px-2 border-b border-border bg-card shrink-0">
      <button onClick={onBack} className="p-1.5 -ml-1 text-muted-foreground hover:text-foreground transition-colors" aria-label="Back to dashboard">
        <ArrowLeft size={16} />
      </button>
      <span className="flex-1 min-w-0 text-sm font-semibold text-foreground truncate">
        {job.title || job.id}
      </span>
      <span aria-live="polite"><StateBadge state={job.state} /></span>
      <PopoverPrimitive.Root>
        <PopoverPrimitive.Trigger asChild>
          <button aria-label="Job actions" className="p-1.5 text-muted-foreground hover:text-foreground transition-colors">
            <MoreHorizontal size={16} />
          </button>
        </PopoverPrimitive.Trigger>
        <PopoverPrimitive.Portal>
          <PopoverPrimitive.Content
            side="bottom"
            align="end"
            sideOffset={4}
            className="z-50 min-w-[180px] rounded-md border border-border bg-popover p-1 shadow-md animate-in fade-in-0 zoom-in-95"
          >
            {canCancel && (
              <PopoverPrimitive.Close asChild>
                <button
                  onClick={onCancelOpen}
                  className="flex w-full items-center gap-2 rounded-sm px-3 py-2 text-sm text-destructive transition-colors hover:bg-accent"
                >
                  <XCircle size={13} /> Cancel Job
                </button>
              </PopoverPrimitive.Close>
            )}
            {canResume && (
              <PopoverPrimitive.Close asChild>
                <button
                  onClick={onResume}
                  className="flex w-full items-center gap-2 rounded-sm px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  <RotateCcw size={13} /> Resume
                </button>
              </PopoverPrimitive.Close>
            )}
            {needsResolution && hasChanges && !hasMergeConflict && (
              <PopoverPrimitive.Close asChild>
                <button
                  onClick={() => onResolve("smart_merge")}
                  disabled={resolveLoading !== null}
                  className="flex w-full items-center gap-2 rounded-sm px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
                >
                  <GitMerge size={13} /> Merge
                </button>
              </PopoverPrimitive.Close>
            )}
            {needsResolution && hasChanges && hasMergeConflict && (
              <PopoverPrimitive.Close asChild>
                <button
                  onClick={() => onResolve("agent_merge")}
                  disabled={resolveLoading !== null}
                  className="flex w-full items-center gap-2 rounded-sm px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
                >
                  <GitMerge size={13} /> Resolve Conflict
                </button>
              </PopoverPrimitive.Close>
            )}
            {needsResolution && hasChanges && (
              <>
                <PopoverPrimitive.Close asChild>
                  <button
                    onClick={() => onResolve("create_pr")}
                    disabled={resolveLoading !== null}
                    className="flex w-full items-center gap-2 rounded-sm px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
                  >
                    <GitPullRequest size={13} /> Create PR
                  </button>
                </PopoverPrimitive.Close>
                <PopoverPrimitive.Close asChild>
                  <button
                    onClick={onDiscardOpen}
                    className="flex w-full items-center gap-2 rounded-sm px-3 py-2 text-sm text-destructive transition-colors hover:bg-accent"
                  >
                    <Trash2 size={13} /> Discard
                  </button>
                </PopoverPrimitive.Close>
              </>
            )}
            {needsResolution && !hasChanges && (
              <PopoverPrimitive.Close asChild>
                <button
                  onClick={onMarkDoneOpen}
                  className="flex w-full items-center gap-2 rounded-sm px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  <CheckCircle2 size={13} /> Mark Done
                </button>
              </PopoverPrimitive.Close>
            )}
            {isResolved && !job.archivedAt && (
              <PopoverPrimitive.Close asChild>
                <button
                  onClick={onCompleteOpen}
                  className="flex w-full items-center gap-2 rounded-sm px-3 py-2 text-sm text-green-500 transition-colors hover:bg-accent"
                >
                  <CheckCircle2 size={13} /> Complete
                </button>
              </PopoverPrimitive.Close>
            )}
            {canArchive && (
              <PopoverPrimitive.Close asChild>
                <button
                  onClick={onCompleteOpen}
                  className="flex w-full items-center gap-2 rounded-sm px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  <Archive size={13} /> {job.state === "failed" ? "Abandon" : "Archive"}
                </button>
              </PopoverPrimitive.Close>
            )}
            {hasWorktree && (
              <>
                <div className="h-px bg-border my-1" />
                <PopoverPrimitive.Close asChild>
                  <button
                    onClick={onOpenTerminal}
                    className="flex w-full items-center gap-2 rounded-sm px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                  >
                    <TerminalSquare size={13} /> Terminal
                    {jobTerminalCount > 0 && <span className="ml-auto text-[10px] font-semibold text-primary">×{jobTerminalCount}</span>}
                  </button>
                </PopoverPrimitive.Close>
              </>
            )}
          </PopoverPrimitive.Content>
        </PopoverPrimitive.Portal>
      </PopoverPrimitive.Root>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// MobileBottomNav — iOS-style tab bar fixed at bottom
// ────────────────────────────────────────────────────────────────────────────

interface MobileBottomNavProps {
  hasRecipe?: boolean;
  tab: string;
  handleTabChange: (tab: string) => void;
  hasChanges: boolean;
  hasArtifacts: boolean;
  artifactCount: number;
  mobileActivityOpen: boolean;
  setMobileActivityOpen: (open: boolean | ((o: boolean) => boolean)) => void;
  mobileMoreOpen: boolean;
  setMobileMoreOpen: (open: boolean | ((o: boolean) => boolean)) => void;
}

export function MobileBottomNav({
  hasRecipe = false,
  tab,
  handleTabChange,
  hasChanges,
  hasArtifacts,
  artifactCount,
  mobileActivityOpen,
  setMobileActivityOpen,
  mobileMoreOpen,
  setMobileMoreOpen,
}: MobileBottomNavProps) {
  return (
    <nav className="fixed bottom-0 inset-x-0 z-50 md:hidden flex items-end justify-around border-t border-border bg-card/95 backdrop-blur-sm safe-area-pb landscape:items-center" style={{ height: 52 }}>
      {/* Activity toggle — visually distinct (opens overlay, not a tab) */}
      {!hasRecipe && <button
        onClick={() => { if (tab !== "live") handleTabChange("live"); setMobileActivityOpen((o: boolean) => !o); setMobileMoreOpen(false); }}
        className={cn(
          "flex flex-col items-center justify-center gap-0.5 flex-1 pt-1.5 pb-1 min-w-0 transition-colors landscape:flex-row landscape:gap-1 landscape:py-0.5",
          mobileActivityOpen ? "text-primary" : "text-muted-foreground active:text-foreground",
        )}
      >
        <ListTree size={20} strokeWidth={mobileActivityOpen ? 2.5 : 1.5} className="landscape:!size-4" />
        <span className={cn("text-[10px] leading-tight truncate landscape:hidden", mobileActivityOpen && "font-semibold")}>Activity</span>
      </button>}
      {[
        { id: "live", icon: Radio, label: hasRecipe ? "Run" : "Live" },
        ...(hasChanges ? [{ id: "review", icon: ScanSearch, label: "Review" }] : []),
        { id: "files", icon: FolderTree, label: "Files" },
      ].map(({ id, icon: Icon, label }) => (
        <button
          key={id}
          onClick={() => { setMobileActivityOpen(false); setMobileMoreOpen(false); handleTabChange(id); }}
          className={cn(
            "flex flex-col items-center justify-center gap-0.5 flex-1 pt-1.5 pb-1 min-w-0 transition-colors landscape:flex-row landscape:gap-1 landscape:py-0.5",
            tab === id && !mobileActivityOpen
              ? "text-primary"
              : "text-muted-foreground active:text-foreground",
          )}
        >
          <Icon size={20} strokeWidth={tab === id && !mobileActivityOpen ? 2.5 : 1.5} className="landscape:!size-4" />
          <span className={cn("text-[10px] leading-tight truncate landscape:hidden", tab === id && !mobileActivityOpen && "font-semibold")}>{label}</span>
        </button>
      ))}
      {/* More overflow — Shell, Metrics, Artifacts */}
      <div className="relative flex-1 min-w-0">
        <button
          onClick={() => setMobileMoreOpen((o: boolean) => !o)}
          className={cn(
            "flex flex-col items-center justify-center gap-0.5 w-full pt-1.5 pb-1 transition-colors landscape:flex-row landscape:gap-1 landscape:py-0.5",
            mobileMoreOpen || ["metrics", "artifacts"].includes(tab) ? "text-primary" : "text-muted-foreground active:text-foreground",
          )}
        >
          <MoreHorizontal size={20} strokeWidth={mobileMoreOpen || ["metrics", "artifacts"].includes(tab) ? 2.5 : 1.5} className="landscape:!size-4" />
          <span className={cn("text-[10px] leading-tight truncate landscape:hidden", (mobileMoreOpen || ["metrics", "artifacts"].includes(tab)) && "font-semibold")}>More</span>
        </button>
        {mobileMoreOpen && (
          <div className="absolute bottom-full right-0 mb-2 mr-1 rounded-md border border-border bg-popover shadow-lg py-1 min-w-[140px] animate-in fade-in-0 zoom-in-95">
            <button
              onClick={() => { setMobileMoreOpen(false); setMobileActivityOpen(false); handleTabChange("metrics"); }}
              className={cn("flex w-full items-center gap-2.5 px-3 py-2.5 text-sm transition-colors", tab === "metrics" ? "text-primary bg-accent" : "text-foreground hover:bg-accent")}
            >
              <BarChart3 size={15} /> Metrics
            </button>
            {hasArtifacts && (
              <button
                onClick={() => { setMobileMoreOpen(false); setMobileActivityOpen(false); handleTabChange("artifacts"); }}
                className={cn("flex w-full items-center gap-2.5 px-3 py-2.5 text-sm transition-colors", tab === "artifacts" ? "text-primary bg-accent" : "text-foreground hover:bg-accent")}
              >
                <Package size={15} /> Artifacts
                {artifactCount > 0 && <span className="ml-auto text-[10px] font-semibold text-primary">{artifactCount}</span>}
              </button>
            )}
          </div>
        )}
      </div>
    </nav>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// MobileFooterActions — contextual review actions above the bottom nav
// ────────────────────────────────────────────────────────────────────────────

interface MobileFooterActionsProps {
  needsResolution: boolean;
  hasChanges: boolean;
  tab: string;
  hasMergeConflict: boolean;
  resolveLoading: string | null;
  onResolve: (action: "merge" | "smart_merge" | "create_pr" | "agent_merge") => void;
  onDiscardOpen: () => void;
}

export function MobileFooterActions({
  needsResolution,
  hasChanges,
  tab,
  hasMergeConflict,
  resolveLoading,
  onResolve,
  onDiscardOpen,
}: MobileFooterActionsProps) {
  if (!(needsResolution && hasChanges && tab === "review")) return null;

  return (
    <div className="fixed bottom-[52px] inset-x-0 z-40 flex md:hidden items-center justify-center gap-2 px-3 py-2 border-t border-border bg-card/95 backdrop-blur-sm">
      {!hasMergeConflict && (
        <Button size="sm" className="flex-1 gap-1" loading={resolveLoading === "smart_merge"} disabled={resolveLoading !== null} onClick={() => onResolve("smart_merge")}>
          <GitMerge size={14} /> Merge
        </Button>
      )}
      {hasMergeConflict && (
        <Button size="sm" className="flex-1 gap-1" loading={resolveLoading === "agent_merge"} disabled={resolveLoading !== null} onClick={() => onResolve("agent_merge")}>
          <GitMerge size={14} /> Resolve
        </Button>
      )}
      <Button size="sm" variant="outline" className="flex-1 gap-1" loading={resolveLoading === "create_pr"} disabled={resolveLoading !== null} onClick={() => onResolve("create_pr")}>
        <GitPullRequest size={14} /> PR
      </Button>
      <Button size="sm" variant="outline" className="gap-1 text-destructive border-destructive/40" onClick={onDiscardOpen}>
        <Trash2 size={14} />
      </Button>
    </div>
  );
}
