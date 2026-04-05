import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, ListChecks, ChevronRight } from "lucide-react";
import { cn } from "../lib/utils";
import { useStore, selectJobSteps, selectActiveStep, selectStepGroups } from "../store";
import type { JobSummary, Step, StepGroup } from "../store";
import { useIsMobile } from "../hooks/useIsMobile";
import { StepContainer } from "./StepContainer";
import { StepSearchBar } from "./StepSearchBar";
import type { FilterChipKey } from "./StepSearchBar";
import { ResumeBanner } from "./ResumeBanner";

interface StepListViewProps {
  job: JobSummary;
  /** Step ID to auto-scroll and expand on mount (from deep link) */
  targetStepId?: string | null;
  /** Called when user clicks "View changes in this step" */
  onViewDiff?: (step: { stepId: string; startSha: string | null; endSha: string | null }) => void;
}

export function StepListView({ job, targetStepId, onViewDiff }: StepListViewProps) {
  const jobId = job.id;
  const steps = useStore(selectJobSteps(jobId));
  const activeStep = useStore(selectActiveStep(jobId));
  const stepGroups = useStore(selectStepGroups(jobId));
  const isMobile = useIsMobile();
  const activeStepRef = useRef<HTMLDivElement | null>(null);
  const listTopRef = useRef<HTMLDivElement | null>(null);
  const stepRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const isRunning = job.state === "running" || job.state === "agent_running";

  // Expanded step tracking (supports external triggers from search/deep links)
  const [expandedStepIds, setExpandedStepIds] = useState<Set<string>>(new Set());

  const toggleStep = useCallback((stepId: string) => {
    setExpandedStepIds((prev) => {
      const next = new Set(prev);
      if (next.has(stepId)) next.delete(stepId);
      else next.add(stepId);
      return next;
    });
  }, []);

  const scrollToStep = useCallback((stepId: string) => {
    const el = stepRefs.current.get(stepId);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  // Deep link: auto-scroll and expand target step
  useEffect(() => {
    if (!targetStepId || steps.length === 0) return;
    const match = steps.find((s) => s.stepId === targetStepId);
    if (match) {
      setExpandedStepIds((prev) => new Set(prev).add(targetStepId));
      // Defer scroll to allow render
      requestAnimationFrame(() => scrollToStep(targetStepId));
    }
  }, [targetStepId, steps, scrollToStep]);

  const scrollToActiveStep = () => {
    activeStepRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const scrollToTop = () => {
    listTopRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const scrollToLastError = useCallback(() => {
    const failed = [...steps].reverse().find((s) => s.status === "failed");
    if (failed) {
      setExpandedStepIds((prev) => new Set(prev).add(failed.stepId));
      requestAnimationFrame(() => scrollToStep(failed.stepId));
    }
  }, [steps, scrollToStep]);

  const handleSearchSelect = useCallback((result: { stepId: string | null }) => {
    if (!result.stepId) return;
    setExpandedStepIds((prev) => new Set(prev).add(result.stepId!));
    requestAnimationFrame(() => scrollToStep(result.stepId!));
  }, [scrollToStep]);

  const hasErrors = steps.some((s) => s.status === "failed");

  // Filter chips: track active filter and compute which steps match
  const [activeFilter, setActiveFilter] = useState<FilterChipKey | null>(null);

  const stepMatchesFilter = useCallback((step: Step, filter: FilterChipKey | null): boolean => {
    if (!filter) return true;
    switch (filter) {
      case "errors": return step.status === "failed";
      case "tools": return step.toolCount > 0;
      case "agent": return step.agentMessage != null;
      case "files": return (step.filesWritten ?? []).length > 0;
      case "running": return step.status === "running";
      default: return true;
    }
  }, []);

  // Compute visible filter chips dynamically from actual step data
  const visibleChips = useMemo(() => {
    const chips: { key: FilterChipKey; label: string; count?: number }[] = [];
    const errorCount = steps.filter((s) => s.status === "failed").length;
    if (errorCount > 0) chips.push({ key: "errors", label: "Errors", count: errorCount });
    const toolSteps = steps.filter((s) => s.toolCount > 0).length;
    if (toolSteps > 0) chips.push({ key: "tools", label: "Tool calls" });
    const agentSteps = steps.filter((s) => s.agentMessage != null).length;
    if (agentSteps > 0) chips.push({ key: "agent", label: "Agent messages" });
    const fileSteps = steps.filter((s) => (s.filesWritten ?? []).length > 0).length;
    if (fileSteps > 0) chips.push({ key: "files", label: "File changes", count: fileSteps });
    const runningSteps = steps.filter((s) => s.status === "running").length;
    if (runningSteps > 0) chips.push({ key: "running", label: "Running", count: runningSteps });
    return chips;
  }, [steps]);

  // Collapsed group tracking
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const toggleGroup = useCallback((groupId: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  }, []);

  // Build render items: interleave groups and ungrouped steps
  type RenderItem =
    | { kind: "step"; step: Step }
    | { kind: "group"; group: StepGroup; steps: Step[] };

  const renderItems = useMemo<RenderItem[]>(() => {
    if (stepGroups.length === 0) {
      return steps.map((step) => ({ kind: "step" as const, step }));
    }

    // Build a set of all grouped step IDs and a map from stepId to group
    const stepIdToGroup = new Map<string, StepGroup>();
    for (const group of stepGroups) {
      for (const sid of group.stepIds) {
        stepIdToGroup.set(sid, group);
      }
    }

    const items: RenderItem[] = [];
    const emittedGroups = new Set<string>();

    for (const step of steps) {
      const group = stepIdToGroup.get(step.stepId);
      if (group) {
        if (!emittedGroups.has(group.groupId)) {
          emittedGroups.add(group.groupId);
          const groupSteps = steps.filter((s) => group.stepIds.includes(s.stepId));
          items.push({ kind: "group", group, steps: groupSteps });
        }
        // Individual grouped steps are rendered inside the group, skip here
      } else {
        items.push({ kind: "step", step });
      }
    }

    return items;
  }, [steps, stepGroups]);

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <div ref={listTopRef} />

      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border">
        <ListChecks size={14} className="text-muted-foreground" />
        <span className="text-sm font-medium">Steps</span>
        {steps.length > 0 && (
          <span className="text-xs text-muted-foreground">{steps.length}</span>
        )}
        {isRunning && activeStep && (
          <span className="ml-auto text-[10px] font-medium text-blue-500">LIVE</span>
        )}
      </div>

      {/* Search & filters — only shown when there are steps */}
      {steps.length > 0 && (
        <StepSearchBar jobId={jobId} onSelect={handleSearchSelect} activeFilter={activeFilter} onFilterChange={setActiveFilter} visibleChips={visibleChips} />
      )}

      <ResumeBanner jobId={jobId} onJumpToFirst={scrollToTop} />

      {/* Empty / startup state */}
      {steps.length === 0 && (
        <div className="flex flex-col items-center justify-center py-10 px-4">
          {isRunning ? (
            <>
              <Loader2 className="h-6 w-6 text-muted-foreground/50 animate-spin mb-3" />
              <p className="text-sm text-muted-foreground">Waiting for first step…</p>
              <p className="text-xs text-muted-foreground/60 mt-1">The agent is initializing</p>
            </>
          ) : (
            <>
              <ListChecks className="h-6 w-6 text-muted-foreground/30 mb-3" />
              <p className="text-sm text-muted-foreground">No steps recorded</p>
            </>
          )}
        </div>
      )}

      {/* Step list (grouped + ungrouped) */}
      {steps.length > 0 && (
        <div className="flex flex-col divide-y divide-border/50">
          {renderItems.map((item) => {
            if (item.kind === "step") {
              const { step } = item;
              const isActive = step.stepId === activeStep?.stepId;
              const dimmed = activeFilter != null && !stepMatchesFilter(step, activeFilter);
              return (
                <div
                  key={step.stepId}
                  data-step-id={step.stepId}
                  ref={(el) => {
                    if (el) stepRefs.current.set(step.stepId, el);
                    if (isActive) activeStepRef.current = el;
                  }}
                  className={cn(dimmed && "opacity-40 transition-opacity")}
                >
                  <StepContainer
                    step={step}
                    isActive={isActive}
                    expanded={expandedStepIds.has(step.stepId)}
                    onToggle={() => toggleStep(step.stepId)}
                    onViewDiff={onViewDiff}
                  />
                </div>
              );
            }

            // Grouped steps
            const { group, steps: groupSteps } = item;
            const isCollapsed = collapsedGroups.has(group.groupId);
            const groupToolCount = groupSteps.reduce((s, st) => s + st.toolCount, 0);
            const groupDurationMs = groupSteps.reduce((s, st) => s + (st.durationMs ?? 0), 0);
            const groupHasActive = groupSteps.some((s) => s.stepId === activeStep?.stepId);
            const allDimmed = activeFilter != null && groupSteps.every((s) => !stepMatchesFilter(s, activeFilter));

            return (
              <div
                key={group.groupId}
                className={cn(allDimmed && "opacity-40 transition-opacity")}
              >
                {/* Group header */}
                <button
                  type="button"
                  onClick={() => toggleGroup(group.groupId)}
                  className={cn(
                    "flex items-center gap-2 w-full text-left px-4 py-2.5 transition-colors",
                    "hover:bg-accent/30",
                    groupHasActive && "bg-blue-500/5",
                  )}
                >
                  <ChevronRight
                    size={14}
                    className={cn(
                      "shrink-0 text-muted-foreground transition-transform",
                      !isCollapsed && "rotate-90",
                    )}
                  />
                  <span className="text-sm font-medium truncate flex-1">
                    {group.headline}
                  </span>
                  <span className="flex items-center gap-2 shrink-0 text-xs text-muted-foreground">
                    <span>{groupSteps.length} steps</span>
                    {groupToolCount > 0 && <span>{groupToolCount} tools</span>}
                    {groupDurationMs > 0 && (
                      <span className="tabular-nums">
                        {groupDurationMs < 1000 ? `${groupDurationMs}ms` : `${Math.round(groupDurationMs / 1000)}s`}
                      </span>
                    )}
                  </span>
                </button>

                {/* Group children */}
                {!isCollapsed && (
                  <div className="ml-3 border-l border-border/50">
                    {groupSteps.map((step) => {
                      const isActive = step.stepId === activeStep?.stepId;
                      const dimmed = activeFilter != null && !stepMatchesFilter(step, activeFilter);
                      return (
                        <div
                          key={step.stepId}
                          data-step-id={step.stepId}
                          ref={(el) => {
                            if (el) stepRefs.current.set(step.stepId, el);
                            if (isActive) activeStepRef.current = el;
                          }}
                          className={cn(
                            "border-b border-border/30 last:border-b-0",
                            dimmed && "opacity-40 transition-opacity",
                          )}
                        >
                          <StepContainer
                            step={step}
                            isActive={isActive}
                            expanded={expandedStepIds.has(step.stepId)}
                            onToggle={() => toggleStep(step.stepId)}
                            onViewDiff={onViewDiff}
                          />
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Jump-to quick actions */}
      {isRunning && activeStep && (
        isMobile ? (
          <button
            onClick={scrollToActiveStep}
            className="fixed bottom-20 left-1/2 -translate-x-1/2 z-40 px-4 py-2 rounded-full
                       bg-primary text-primary-foreground text-sm font-medium shadow-lg min-h-[44px]"
          >
            Jump to current step ↓
          </button>
        ) : (
          <div className="sticky bottom-0 flex gap-2 p-2 bg-card/95 backdrop-blur border-t border-border">
            <button
              onClick={scrollToActiveStep}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              Jump to current step
            </button>
            {hasErrors && (
              <button
                onClick={scrollToLastError}
                className="text-xs text-destructive/80 hover:text-destructive"
              >
                Jump to last error
              </button>
            )}
          </div>
        )
      )}
    </div>
  );
}
