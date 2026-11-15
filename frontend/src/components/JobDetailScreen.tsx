import { useEffect, useState, useCallback, useRef, useMemo, Suspense, Component, type ReactNode } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, PanelLeftClose } from "lucide-react";
import { toast } from "sonner";
import { useStore, selectJobs, enrichJob, selectJobDiffs } from "../store";
import type { JobSummary } from "../store";
import { useSSE } from "../hooks/useSSE";
import { formatJobTerminalLabel } from "../lib/terminalLabels";
import { fetchJob, cancelJob, fetchJobTranscript, fetchJobDiff, fetchApprovals, resolveJob, fetchArtifacts, resumeJob, archiveJob, fetchJobSnapshot, fetchProject, fetchProjectTaskLinks } from "../api/client";
import { CuratedFeed } from "./CuratedFeed";
import { ActivityTimeline } from "./ActivityTimeline";
import { lazyRetry } from "../lib/lazyRetry";

import { MetricsPanel } from "./MetricsPanel";
import { CompleteJobDialog } from "./CompleteJobDialog";
import { Button } from "./ui/button";
import { Spinner } from "./ui/spinner";
import { JobDetailSkeleton } from "./JobDetailSkeleton";
import { ConfirmDialog } from "./ui/confirm-dialog";
import { cn } from "../lib/utils";
import type { StepFilter } from "./DiffViewer";
import type { ReviewSubView } from "./review/ReviewSubTabs";
import { ActivityPanel } from "./ActivityPanel";
import { ViewTabBar } from "./ViewTabBar";
import { mergeTranscriptEntries } from "../lib/transcriptIdentity";
import type { ProjectResponse, TaskLinkResponse } from "../api/types";
import { pathBasename } from "../lib/paths";

import { JobHeaderCard } from "./JobHeaderCard";
import { MobileBottomNav, MobileFooterActions } from "./JobDetailMobile";

const WorkspaceBrowser = lazyRetry(() => import("./WorkspaceBrowser"));
const ArtifactViewer = lazyRetry(() => import("./ArtifactViewer"));
const ReviewDashboard = lazyRetry(() => import("./ReviewDashboard"));


const SKELETON_DELAY_MS = 500;

/** Error boundary for lazy-loaded tabs — shows a recovery button instead of a blank panel. */
class TabErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center py-16 gap-3">
          <p className="text-sm text-muted-foreground">This panel failed to load.</p>
          <button
            onClick={() => this.setState({ error: null })}
            className="px-3 py-1.5 text-sm font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90"
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}


export function JobDetailScreen() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const jobs = useStore(selectJobs);
  const job: JobSummary | undefined = jobId ? jobs[jobId] : undefined;
  const [loading, setLoading] = useState(!job);
  const [showSkeleton, setShowSkeleton] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [resolveLoading, setResolveLoading] = useState<string | null>(null);
  const [completeOpen, setCompleteOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [markDoneOpen, setMarkDoneOpen] = useState(false);
  const [tab, setTab] = useState("live");
  const [stepFilter, setStepFilter] = useState<StepFilter | null>(null);
  const [requestedReviewSubView, setRequestedReviewSubView] = useState<ReviewSubView | null>(null);
  const [scrollToSeq, setScrollToSeq] = useState<number | null>(null);
  const [scrollToTurnId, setScrollToTurnId] = useState<string | null>(null);
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null);
  const [searchActive, setSearchActive] = useState(false);
  const [visibleTurnId, setVisibleTurnId] = useState<string | null>(null);
  const [owningProject, setOwningProject] = useState<ProjectResponse | null>(null);
  const [taskLink, setTaskLink] = useState<TaskLinkResponse | null>(null);
  // Reset selectedTurnId when navigating to a different job
  useEffect(() => { setSelectedTurnId(null); setSearchActive(false); setVisibleTurnId(null); }, [jobId]);
  // Update document title to show job identity in browser tabs
  useEffect(() => {
    const label = job?.title || jobId || "";
    if (label) {
      document.title = `${label} — CodePlane`;
    }
    return () => { document.title = "CodePlane"; };
  }, [job?.title, jobId]);
  const [mobileActivityOpen, setMobileActivityOpen] = useState(false);
  const [mobileMoreOpen, setMobileMoreOpen] = useState(false);
  const diffs = useStore(selectJobDiffs(jobId ?? ""));
  const hasChanges = diffs.length > 0;
  const hasWorktree = !!job?.worktreePath && !job?.archivedAt;
  const hasArtifacts = true;
  const [artifactCount, setArtifactCount] = useState(0);
  const artifactVersion = useStore((s) => jobId ? (s.artifactVersions[jobId] ?? 0) : 0);
  const projectBoardUrl = owningProject
    ? `/projects/id/${encodeURIComponent(owningProject.id)}/board`
    : "/projects";

  useEffect(() => {
    if (!job?.projectId) {
      setOwningProject(null);
      return;
    }
    let ignore = false;
    fetchProject(job.projectId)
      .then((project) => {
        if (!ignore) setOwningProject(project);
      })
      .catch(() => { if (!ignore) setOwningProject(null); });
    return () => { ignore = true; };
  }, [job?.projectId]);

  useEffect(() => {
    if (!owningProject || !jobId) {
      setTaskLink(null);
      return;
    }
    let ignore = false;
    fetchProjectTaskLinks(owningProject.id)
      .then((response) => {
        if (!ignore) setTaskLink(response.items.find((item) => item.jobId === jobId) ?? null);
      })
      .catch(() => { if (!ignore) setTaskLink(null); });
    return () => { ignore = true; };
  }, [jobId, owningProject]);

  // Map a transcript turnId to the nearest activity-timeline step turnId.
  // Many transcript turns have no corresponding step in the activity timeline;
  // walking backward through the transcript finds the closest preceding step.
  const activityTimeline = useStore((s) => jobId ? s.activityTimelines[jobId] : undefined);
  const transcript = useStore((s) => jobId ? s.transcript[jobId] : undefined);
  const stepTurnIds = useMemo(() => {
    if (!activityTimeline) return [] as string[];
    return activityTimeline.activities.flatMap((a) => a.steps.map((s) => s.turnId));
  }, [activityTimeline]);
  const stepTurnIdSet = useMemo(() => new Set(stepTurnIds), [stepTurnIds]);

  const mapToStepTurnId = useCallback((turnId: string | null): string | null => {
    if (!turnId || stepTurnIdSet.size === 0) return turnId;
    if (stepTurnIdSet.has(turnId)) return turnId;
    if (!transcript) return turnId;
    // Find the position of this turnId in the transcript, then walk backward
    const idx = transcript.findIndex((e) => e.turnId === turnId);
    if (idx < 0) return turnId;
    for (let i = idx - 1; i >= 0; i--) {
      const tid = transcript[i]?.turnId;
      if (tid && stepTurnIdSet.has(tid)) return tid;
    }
    // Fallback: walk forward
    for (let i = idx + 1; i < transcript.length; i++) {
      const tid = transcript[i]?.turnId;
      if (tid && stepTurnIdSet.has(tid)) return tid;
    }
    return turnId;
  }, [stepTurnIdSet, transcript]);

  // Map visible turnId from feed scroll position to the nearest activity step
  const visibleStepTurnId = useMemo(
    () => mapToStepTurnId(visibleTurnId),
    [visibleTurnId, mapToStepTurnId],
  );

  const handleTabChange = useCallback((v: string) => {
    setTab(v);
    if (v !== "review") setStepFilter(null);
    if (v !== "live") setScrollToSeq(null);
  }, []);

  // ── Mobile swipe-to-switch-tab ──
  const mobileTabOrder = useMemo(() => {
    const tabs = ["live"];
    if (hasChanges) tabs.push("review");
    tabs.push("files", "metrics");
    tabs.push("artifacts");
    return tabs;
  }, [hasChanges]);
  const touchRef = useRef<{ x: number; y: number; t: number; el: EventTarget | null } | null>(null);
  const [slideDir, setSlideDir] = useState<"left" | "right" | null>(null);

  /** Walk from `el` up to `boundary` — return true if any ancestor scrolls horizontally. */
  const isInsideHScrollable = useCallback((el: EventTarget | null, boundary: HTMLElement) => {
    let node = el as HTMLElement | null;
    while (node && node !== boundary) {
      // Monaco uses .monaco-scrollable-element; also catch any generic overflow-x
      if (node.classList?.contains("monaco-scrollable-element") || node.classList?.contains("monaco-editor")) return true;
      if (node.scrollWidth > node.clientWidth + 1) {
        const ov = getComputedStyle(node).overflowX;
        if (ov === "auto" || ov === "scroll") return true;
      }
      node = node.parentElement;
    }
    return false;
  }, []);

  const onSwipeTouchStart = useCallback((e: React.TouchEvent) => {
    if (window.innerWidth >= 768) return;
    const touch = e.touches[0];
    if (!touch) return;
    touchRef.current = { x: touch.clientX, y: touch.clientY, t: Date.now(), el: e.target };
  }, []);
  const onSwipeTouchEnd = useCallback((e: React.TouchEvent) => {
    const start = touchRef.current;
    if (!start || window.innerWidth >= 768) return;
    touchRef.current = null;
    // If touch originated inside a horizontally scrollable element, don't hijack the swipe
    if (isInsideHScrollable(start.el, e.currentTarget as HTMLElement)) return;
    const touch = e.changedTouches[0];
    if (!touch) return;
    const dx = touch.clientX - start.x;
    const dy = touch.clientY - start.y;
    const dt = Date.now() - start.t;
    // Require: ≥60px horizontal, clearly horizontal (2:1 ratio), quick (<400ms)
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 2 || dt > 400) return;

    // Activity overlay: swipe right (dx>0) on Live opens it; swipe left (dx<0) when open closes it
    if (dx > 0 && tab === "live" && !mobileActivityOpen) {
      setMobileActivityOpen(true);
      return;
    }
    if (mobileActivityOpen) {
      if (dx < 0) setMobileActivityOpen(false);
      return; // swipe-right while overlay is open: no-op (don't fall through to tab switching)
    }

    const idx = mobileTabOrder.indexOf(tab);
    if (idx === -1) return;
    const len = mobileTabOrder.length;
    // Swipe left (dx<0) = next tab; swipe right (dx>0) = previous tab
    // Carousel: wrap around at edges
    const nextIdx = dx < 0 ? (idx + 1) % len : (idx - 1 + len) % len;
    const nextTab = mobileTabOrder[nextIdx];
    if (!nextTab) return;
    // Clear then re-set so consecutive same-direction swipes re-trigger the CSS animation
    setSlideDir(null);
    requestAnimationFrame(() => setSlideDir(dx < 0 ? "right" : "left"));
    handleTabChange(nextTab);
  }, [tab, mobileTabOrder, mobileActivityOpen, handleTabChange, isInsideHScrollable]);

  const handleViewStepChanges = useCallback((filePaths: string[], label: string, seq?: number, turnId?: string) => {
    setStepFilter({ filePaths, label, scrollToSeq: seq, turnId });
    setRequestedReviewSubView("changes");
    setTab("review");
  }, []);

  const handleClearStepFilter = useCallback(() => {
    setStepFilter(null);
  }, []);

  const handleNavigateToStep = useCallback((seq: number, turnId?: string) => {
    setScrollToSeq(seq);
    setTab("live");
    if (turnId) {
      setSelectedTurnId(mapToStepTurnId(turnId));
      setSearchActive(true);
    }
  }, [mapToStepTurnId]);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      fetchArtifacts(jobId)
        .then((res) => {
          if (cancelled) return;
          setArtifactCount(res.items.length);
          const terminal = ["review", "completed", "failed", "canceled"].includes(job?.state ?? "");
          if (terminal && (res.collectionStatus === "pending" || res.collectionStatus === "collecting")) {
            retryTimer = setTimeout(load, 1000);
          }
        })
        .catch((err) => {
          if (!cancelled) console.error("Failed to fetch artifacts", err);
        });
    };
    load();
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [jobId, job?.state, artifactVersion]);



  // Open a new terminal session in the drawer, scoped to this job's worktree.
  // Each click intentionally creates a new session — multiple sessions per job are supported.
  const createTerminalSession = useStore((s) => s.createTerminalSession);
  const terminalSessions = useStore((s) => s.terminalSessions);
  const jobTerminalCount = Object.values(terminalSessions).filter((s) => s.jobId === jobId).length;

  const handleOpenJobTerminal = useCallback(() => {
    if (!job?.worktreePath || !jobId) return;
    const label = formatJobTerminalLabel(job, jobId);
    createTerminalSession({ cwd: job.worktreePath, label, jobId });
  }, [job, jobId, createTerminalSession]);

  // Open a job-scoped SSE connection for full event streaming (no suppression
  // even when >20 active jobs). Closed automatically when navigating away.
  useSSE(jobId);

  useEffect(() => {
    if (!loading) return;
    const timer = setTimeout(() => setShowSkeleton(true), SKELETON_DELAY_MS);
    return () => clearTimeout(timer);
  }, [loading]);

  useEffect(() => {
    if (!jobId) { setLoading(false); return; }
    let cancelled = false;
    const existing = useStore.getState().jobs[jobId];
    if (existing) setLoading(false);
    fetchJob(jobId)
      .then((f) => {
        if (cancelled) return;
        useStore.setState((s) => ({ jobs: { ...s.jobs, [f.id]: enrichJob(f as JobSummary) } }));
      })
      .catch((err) => console.error("Failed to fetch job", err))
      .finally(() => {
        if (!cancelled && !existing) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [jobId]);

  // Load historical transcript from the backend event store.
  // Logs are fetched directly by LogsPanel based on the active min-level.
  useEffect(() => {
    if (!jobId) return;
    fetchJobTranscript(jobId).then((transcript) => {
        useStore.setState((s) => {
          const existingTranscript = s.transcript[jobId] ?? [];
          const mergedTx = mergeTranscriptEntries(transcript, existingTranscript);
          return {
            transcript: { ...s.transcript, [jobId]: mergedTx },
          };
        });
    }).catch((err) => console.error("Failed to fetch job transcript", err));
  }, [jobId]);

  // Hydrate activity timeline from snapshot (turn summaries).
  useEffect(() => {
    if (!jobId) return;
    fetchJobSnapshot(jobId)
      .then((snapshot) => useStore.getState().hydrateJob(snapshot))
      .catch(() => { /* best-effort */ });
  }, [jobId]);

  // Load pending approvals so late-joining clients can approve/reject.
  useEffect(() => {
    if (!jobId) return;
    fetchApprovals(jobId).then((approvals) => {
      useStore.setState((s) => {
        const updated = { ...s.approvals };
        for (const a of approvals) updated[a.id] = a;
        return { approvals: updated };
      });
    }).catch((err) => console.error("Failed to fetch approvals", err));
  }, [jobId]);

  // Load diff data: on mount and when job state changes (e.g. reaches terminal state).
  const jobState = job?.state;
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    fetchJobDiff(jobId)
      .then((files) => {
        if (cancelled) return;
        useStore.setState((s) => ({
          diffs: { ...s.diffs, [jobId]: files },
        }));
      })
      .catch((err) => {
        if (!cancelled) console.error("Failed to fetch job diff", err);
      });
    return () => { cancelled = true; };
  }, [jobId, jobState]);

  const doCancelJob = useCallback(async () => {
    if (!jobId) return;
    try {
      const updated = await cancelJob(jobId);
      await archiveJob(jobId);
      useStore.setState((s) => ({
        jobs: { ...s.jobs, [updated.id]: { ...updated, archivedAt: new Date().toISOString() } },
      }));
      toast.success("Job canceled and cleaned up");
      navigate(projectBoardUrl);
    } catch (e) { toast.error(String(e)); }
  }, [jobId, navigate, projectBoardUrl]);

  const handleResume = useCallback(async () => {
    if (!jobId) return;
    setActionLoading(true);
    try {
      const result = await resumeJob(jobId);
      useStore.setState((state) => {
        const existing = state.jobs[jobId];
        if (!existing) return state;
        return {
          ...state,
          jobs: {
            ...state.jobs,
            [jobId]: {
              ...existing,
              state: result.state,
              branch: result.branch,
              worktreePath: result.worktreePath,
              updatedAt: result.updatedAt,
              completedAt: null,
              archivedAt: null,
            },
          },
        };
      });
      toast.success(`Resumed: ${result.id}`);
      navigate(`/jobs/${jobId}`);
    } catch (e) { toast.error(String(e)); }
    finally { setActionLoading(false); }
  }, [jobId, navigate]);

  const doDiscardJob = useCallback(async (toastMsg: string) => {
    if (!jobId) return;
    try {
      await resolveJob(jobId, "discard");
      await archiveJob(jobId);
      useStore.setState((s) => {
        const existing = s.jobs[jobId];
        if (!existing) return s;
        return { jobs: { ...s.jobs, [jobId]: { ...existing, resolution: "discarded", archivedAt: new Date().toISOString() } } };
      });
      toast.success(toastMsg);
      navigate(projectBoardUrl);
    } catch (e) { toast.error(String(e)); }
  }, [jobId, navigate, projectBoardUrl]);

  const handleResolve = useCallback(async (action: "merge" | "smart_merge" | "create_pr" | "agent_merge") => {
    if (!jobId) return;
    setResolveLoading(action);
    try {
      const res = await resolveJob(jobId, action);
      const refreshedJob = action === "agent_merge"
        ? null
        : await fetchJob(jobId).catch(() => null);
      const refreshedSummary = refreshedJob
        ? enrichJob(refreshedJob as JobSummary)
        : null;
      const conflictLike =
        res.resolution === "conflict" ||
        (refreshedSummary?.mergeStatus ?? null) === "conflict" ||
        ((res.conflictFiles?.length ?? 0) > 0) ||
        ((refreshedSummary?.conflictFiles?.length ?? 0) > 0);
      useStore.setState((s) => {
        const existing = s.jobs[jobId];
        const baseJob = refreshedSummary ?? existing;
        if (!baseJob) return {};
        const nextJob = action === "agent_merge"
          ? {
              ...baseJob,
              state: "running",
              resolution: null,
              archivedAt: null,
              conflictFiles: res.conflictFiles ?? baseJob.conflictFiles,
              resolutionError: null,
            }
          : {
              ...baseJob,
              resolution: res.resolution,
              prUrl: res.prUrl ?? baseJob.prUrl,
              conflictFiles: res.conflictFiles ?? baseJob.conflictFiles,
              resolutionError: res.resolution === "unresolved" ? (res.error ?? null) : null,
              mergeStatus:
                res.resolution === "merged"
                  ? "merged"
                  : res.resolution === "conflict"
                    ? "conflict"
                    : baseJob.mergeStatus,
            };
        return {
          jobs: {
            ...s.jobs,
            [jobId]: nextJob,
          },
        };
      });
      if (res.prUrl) {
        toast.success("PR created", {
          description: res.prUrl,
          action: { label: "Open", onClick: () => window.open(res.prUrl!, "_blank") },
        });
      } else if (action === "agent_merge") {
        toast.success("Resolving with agent…");
      } else if (res.resolution === "merged") {
        toast.success("Merged");
      } else if (res.resolution === "pr_created") {
        toast.success("PR created");
      } else if (conflictLike) {
        toast.error("Merge conflict detected");
      } else {
        toast.error(res.error ?? "Merge did not complete");
      }
    } catch (e: unknown) {
      toast.error(String(e));
    }
    finally { setResolveLoading(null); }
  }, [jobId]);

  if (!jobId) return null;

  if (loading && showSkeleton) return <JobDetailSkeleton />;
  if (loading) return null;

  if (!job) {
    return (
      <div className="flex flex-col items-center gap-3 py-16">
        <p className="text-muted-foreground">Job not found</p>
        <Button variant="ghost" onClick={() => navigate("/projects")}>
          <ArrowLeft size={16} />
          Back to Projects
        </Button>
      </div>
    );
  }

  const canCancel = ["preparing", "queued", "running", "waiting_for_approval"].includes(job.state);
  const canResume = job.state === "failed";
  const isRunning = job.state === "running";
  const isPreparing = job.state === "preparing";

  const hasMergeConflict =
    !["merged", "pr_created", "discarded"].includes(job.resolution ?? "") &&
    (job.resolution === "conflict" ||
    job.mergeStatus === "conflict" ||
    ((job.conflictFiles?.length ?? 0) > 0));
  const needsResolution =
    job.state === "review" &&
    (job.resolution === "unresolved" || job.resolution === "conflict" || !job.resolution);
  const isResolved =
    job.state === "completed" &&
    !!job.resolution &&
    job.resolution !== "unresolved" &&
    job.resolution !== "conflict";
  const canArchive = (job.state === "failed" || job.state === "canceled" || (job.state === "completed" && !isResolved)) && !job.archivedAt;

  return (
    <div className="px-0 md:flex md:flex-col md:h-full md:min-h-0 md:pb-2">
      <nav className="hidden md:flex items-center gap-1.5 px-4 py-2 text-xs text-muted-foreground" aria-label="Breadcrumb">
        {owningProject ? (
          <>
            <Link to={projectBoardUrl} className="hover:text-foreground">{owningProject.name}</Link>
            <span>/</span>
            <Link
              to={`/projects/id/${encodeURIComponent(owningProject.id)}/repos/${encodeURIComponent(job.repo)}/jobs`}
              className="hover:text-foreground"
            >
              {pathBasename(job.repo) || job.repo}
            </Link>
            <span>/</span>
            {taskLink && (
              <>
                <Link
                  to={`/projects/id/${encodeURIComponent(owningProject.id)}/board/task/${encodeURIComponent(taskLink.id)}`}
                  className="hover:text-foreground"
                >
                  {taskLink.storyNodeId || taskLink.trackerTicketRef || "Task"}
                </Link>
                <span>/</span>
              </>
            )}
          </>
        ) : (
          <>
            <Link to="/projects" className="hover:text-foreground">Projects</Link>
            <span>/</span>
            <span>Original project is unavailable</span>
            <span>/</span>
          </>
        )}
        <span className="truncate text-foreground">{job.title || job.id}</span>
      </nav>
      {/* ── Collapsible job header card (all viewports) ── */}
      <JobHeaderCard
        job={job}
        isPreparing={isPreparing}
        hasMergeConflict={hasMergeConflict}
        onNavigateHome={() => navigate(projectBoardUrl)}
        onCostClick={() => handleTabChange("metrics")}
        actionProps={{
          canCancel,
          canResume,
          needsResolution,
          hasChanges,
          hasMergeConflict,
          isResolved,
          canArchive,
          jobState: job.state,
          archivedAt: job.archivedAt,
          actionLoading,
          resolveLoading,
          onCancelOpen: () => setCancelOpen(true),
          onResume: handleResume,
          onResolve: handleResolve,
          onDiscardOpen: () => setDiscardOpen(true),
          onMarkDoneOpen: () => setMarkDoneOpen(true),
          onCompleteOpen: () => setCompleteOpen(true),
        }}
      />

      {/* ── Desktop: View tab bar ── */}
      <ViewTabBar
        activeTab={tab}
        onTabChange={handleTabChange}
        hasChanges={hasChanges}
        hasArtifacts={hasArtifacts}
        artifactCount={artifactCount}
        hasWorktree={hasWorktree}
        jobTerminalCount={jobTerminalCount}
        onOpenTerminal={handleOpenJobTerminal}
      />

      {completeOpen && job && (
        <CompleteJobDialog job={job} open onClose={() => setCompleteOpen(false)} onArchived={() => navigate(projectBoardUrl)} />
      )}

      {/* Tab content — activity sidebar is scoped to Live tab only */}
      <div
        className={cn(
          "min-h-0 pb-[52px] md:pb-0 md:flex-1 md:flex md:overflow-hidden md:mt-2 md:mx-3 md:gap-3",
          slideDir === "left" && "animate-slide-left",
          slideDir === "right" && "animate-slide-right",
        )}
        onTouchStart={onSwipeTouchStart}
        onTouchEnd={onSwipeTouchEnd}
        onAnimationEnd={() => setSlideDir(null)}
      >
        {/* ── Content panel ── */}
        <div className="flex-1 min-w-0 md:flex md:flex-col md:overflow-hidden">
      {tab === "live" && (
        <div className="flex flex-row relative md:h-full md:min-h-0">
          {/* ── Activity panel — part of the Live view ── */}
          <ActivityPanel
            jobId={jobId}
            jobState={job.state}
            selectedTurnId={selectedTurnId}
            searchActive={searchActive}
            visibleStepTurnId={visibleStepTurnId}
            onStepClick={(turnId) => {
              setScrollToTurnId(turnId);
              setSelectedTurnId(turnId);
            }}
          />
          {/* Activity overlay — slides in from left (mobile only) */}
          {mobileActivityOpen && (
            <div className="md:hidden absolute inset-0 z-30 flex">
              <div className="w-[85%] max-w-xs h-full bg-card border-r border-border shadow-xl animate-slide-left overflow-hidden">
                <button
                  onClick={() => setMobileActivityOpen(false)}
                  className="flex items-center gap-2 px-4 py-2.5 w-full text-left border-b border-border hover:bg-accent/50 transition-colors"
                >
                  <PanelLeftClose size={13} className="text-muted-foreground shrink-0" />
                  <span className="text-sm font-semibold text-muted-foreground">Activity</span>
                </button>
                <div className="flex-1 overflow-hidden" style={{ height: 'calc(100% - 41px)' }}>
                  <ActivityTimeline
                    jobId={jobId}
                    jobState={job.state}
                    onStepClick={(turnId) => {
                      setMobileActivityOpen(false);
                      setScrollToTurnId(turnId);
                      setSelectedTurnId(turnId);
                    }}
                    selectedTurnId={selectedTurnId}
                    searchActive={searchActive}
                    visibleStepTurnId={visibleStepTurnId}
                  />
                </div>
              </div>
              <div className="flex-1 bg-black/40" onClick={() => setMobileActivityOpen(false)} />
            </div>
          )}
          <div className="flex flex-col gap-4 flex-1 min-w-0">
            {/* §7.2 — Structural warnings accumulated during execution */}
            <div className="h-[calc(100dvh-92px)] md:h-full min-h-[22rem]">
              <CuratedFeed
                jobId={jobId}
                sdk={job.sdk}
                interactive
                jobState={job.state}
                pausable={isRunning}
                prompt={job.prompt}
                promptTimestamp={job.createdAt}
                onViewStepChanges={handleViewStepChanges}
                onSearchHighlight={(turnId) => {
                  setSelectedTurnId(turnId ? mapToStepTurnId(turnId) : null);
                  setSearchActive(turnId !== null);
                }}
                onVisibleTurnId={setVisibleTurnId}
                visibleStepTurnId={visibleStepTurnId}
                scrollToSeq={scrollToSeq}
                scrollToTurnId={scrollToTurnId}
                stepTurnIds={stepTurnIds}
              />
            </div>
          </div>
        </div>
      )}



      {tab === "files" && (
        <TabErrorBoundary>
          <Suspense fallback={<div className="flex justify-center py-10"><Spinner /></div>}>
            <WorkspaceBrowser jobId={jobId} />
          </Suspense>
        </TabErrorBoundary>
      )}

      {tab === "metrics" && (
        <MetricsPanel jobId={jobId} isRunning={isRunning} />
      )}

      {tab === "review" && (
        <TabErrorBoundary>
          <Suspense fallback={<div className="flex justify-center py-10"><Spinner /></div>}>
            <ReviewDashboard
              jobId={jobId}
              hasChanges={hasChanges}
              jobState={job.state}
              resolution={job.resolution}
              archivedAt={job.archivedAt}
              onAskSent={() => setTab("live")}
              stepFilter={stepFilter}
              onClearStepFilter={handleClearStepFilter}
              onNavigateToStep={handleNavigateToStep}
              requestedSubView={requestedReviewSubView}
              onSubViewHandled={() => setRequestedReviewSubView(null)}
            />
          </Suspense>
        </TabErrorBoundary>
      )}

      {tab === "artifacts" && (
        <TabErrorBoundary>
          <Suspense fallback={<div className="flex justify-center py-10"><Spinner /></div>}>
            <ArtifactViewer jobId={jobId} onCountChange={setArtifactCount} />
          </Suspense>
        </TabErrorBoundary>
      )}


      </div>{/* end content panel */}
      </div>{/* end activity + content flex wrapper */}

      {/* ── Mobile contextual footer — shows review actions above the bottom tab bar ── */}
      <MobileFooterActions
        needsResolution={needsResolution}
        hasChanges={hasChanges}
        tab={tab}
        hasMergeConflict={hasMergeConflict}
        resolveLoading={resolveLoading}
        onResolve={handleResolve}
        onDiscardOpen={() => setDiscardOpen(true)}
      />

      {/* ── Mobile bottom tab bar (iOS-style) ── */}
      <MobileBottomNav
        tab={tab}
        handleTabChange={handleTabChange}
        hasChanges={hasChanges}
        hasArtifacts={hasArtifacts}
        artifactCount={artifactCount}
        mobileActivityOpen={mobileActivityOpen}
        setMobileActivityOpen={setMobileActivityOpen}
        mobileMoreOpen={mobileMoreOpen}
        setMobileMoreOpen={setMobileMoreOpen}
      />

      <ConfirmDialog
        open={cancelOpen}
        onClose={() => setCancelOpen(false)}
        onConfirm={doCancelJob}
        title="Cancel & Clean Up?"
        description="This will stop the running agent, archive the job, and remove the worktree and branch."
        confirmLabel="Cancel & Clean Up"
      />

      <ConfirmDialog
        open={discardOpen}
        onClose={() => setDiscardOpen(false)}
        onConfirm={() => doDiscardJob("Changes discarded and cleaned up")}
        title="Discard & Clean Up?"
        description="All changes in the worktree will be deleted and the job will be archived. This cannot be undone."
        confirmLabel="Discard & Clean Up"
      />

      <ConfirmDialog
        open={markDoneOpen}
        onClose={() => setMarkDoneOpen(false)}
        onConfirm={() => doDiscardJob("Job completed and archived")}
        title="Mark as Done?"
        description="The job will be marked as complete and archived. The worktree and branch will be cleaned up."
        confirmLabel="Mark Done & Archive"
      />


    </div>
  );
}
