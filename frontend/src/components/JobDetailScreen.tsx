import { useEffect, useState, useCallback, useRef, lazy, Suspense } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, RotateCcw, XCircle, ExternalLink, CheckCircle2, AlertTriangle, ArrowDownCircle, GitMerge, GitPullRequest, Trash2, Archive, FolderTree, GitBranch, BookOpen, TerminalSquare, ChevronDown } from "lucide-react";
import { toast } from "sonner";
import { useStore, selectJobs, enrichJob, selectJobDiffs } from "../store";
import type { JobSummary } from "../store";
import { useSSE } from "../hooks/useSSE";
import { fetchJob, cancelJob, rerunJob, fetchJobTranscript, fetchJobTimeline, fetchJobDiff, fetchApprovals, resolveJob, fetchArtifacts } from "../api/client";
import { StateBadge } from "./StateBadge";
import { SdkBadge } from "./SdkBadge";
import { TranscriptPanel } from "./TranscriptPanel";
import { MetricsPanel } from "./MetricsPanel";
import { ExecutionTimeline } from "./ExecutionTimeline";
import { PlanPanel } from "./PlanPanel";
import { CompleteJobDialog } from "./CompleteJobDialog";
import { Button } from "./ui/button";
import { Spinner } from "./ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "./ui/tabs";
import { JobDetailSkeleton } from "./JobDetailSkeleton";
import { useIsMobile } from "../hooks/useIsMobile";
import { cn } from "../lib/utils";
import { Tooltip } from "./ui/tooltip";
import { ConfirmDialog } from "./ui/confirm-dialog";

const WorkspaceBrowser = lazy(() => import("./WorkspaceBrowser"));
const DiffViewer = lazy(() => import("./DiffViewer"));
const ArtifactViewer = lazy(() => import("./ArtifactViewer"));

import { TerminalPanel } from "./TerminalPanel";
import { useStore as useTerminalStore } from "../store";

export function JobDetailScreen() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const jobs = useStore(selectJobs);
  const job: JobSummary | undefined = jobId ? jobs[jobId] : undefined;
  const [loading, setLoading] = useState(!job);
  const [actionLoading, setActionLoading] = useState(false);
  const [resolveLoading, setResolveLoading] = useState<string | null>(null);
  const [completeOpen, setCompleteOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [tab, setTab] = useState("live");
  const diffs = useStore(selectJobDiffs(jobId ?? ""));
  const hasChanges = diffs.length > 0;
  const hasWorktree = !!job?.worktreePath && !job?.archivedAt;
  const [hasArtifacts, setHasArtifacts] = useState(false);
  const [metaExpanded, setMetaExpanded] = useState(false);
  const isMobile = useIsMobile();

  // Measure available height for the Live tab using ResizeObserver
  const liveContainerRef = useRef<HTMLDivElement>(null);
  const [liveHeight, setLiveHeight] = useState<number | null>(null);

  useEffect(() => {
    const el = liveContainerRef.current;
    if (!el) return;

    const measure = () => {
      const rect = el.getBoundingClientRect();
      const available = window.innerHeight - rect.top - 16; // 16px bottom margin
      setLiveHeight(Math.max(available, 400));
    };

    measure();

    const ro = new ResizeObserver(measure);
    ro.observe(el);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [tab]);

  useEffect(() => {
    if (!jobId) return;
    fetchArtifacts(jobId)
      .then((res) => setHasArtifacts(res.items.length > 0))
      .catch(() => {});
  }, [jobId, job?.state]);

  useEffect(() => {
    if (!hasArtifacts && tab === "artifacts") setTab("live");
  }, [hasArtifacts, tab]);

  // Job-scoped terminal session
  const [jobTerminalSessionId, setJobTerminalSessionId] = useState<string | null>(null);
  const addJobTerminalSession = useTerminalStore((s) => s.addJobTerminalSession);
  const terminalSessions = useTerminalStore((s) => s.terminalSessions);

  const handleOpenJobTerminal = useCallback(async () => {
    if (!job?.worktreePath || !jobId) return;

    // Check if there's already a terminal session for this job
    const existing = Object.values(terminalSessions).find((s) => s.jobId === jobId);
    if (existing) {
      setJobTerminalSessionId(existing.id);
      return;
    }

    try {
      const res = await fetch("/api/terminal/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cwd: job.worktreePath, jobId }),
      });
      if (!res.ok) return;
      const data = await res.json();
      const session = {
        id: data.id,
        label: job.branch || jobId,
        cwd: job.worktreePath,
        jobId,
      };
      addJobTerminalSession(session);
      setJobTerminalSessionId(data.id);
    } catch (e) {
      console.error("[terminal] Failed to create job terminal:", e);
    }
  }, [job?.worktreePath, job?.branch, jobId, terminalSessions, addJobTerminalSession]);

  // Open a job-scoped SSE connection for full event streaming (no suppression
  // even when >20 active jobs). Closed automatically when navigating away.
  useSSE(jobId);

  useEffect(() => {
    if (!jobId) { setLoading(false); return; }
    const existing = useStore.getState().jobs[jobId];
    if (existing) { setLoading(false); return; }
    fetchJob(jobId)
      .then((f) => useStore.setState((s) => ({ jobs: { ...s.jobs, [f.id]: enrichJob(f as JobSummary) } })))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [jobId]);

  // Load historical transcript from the backend event store.
  // Logs are fetched directly by LogsPanel based on the active min-level.
  useEffect(() => {
    if (!jobId) return;
    fetchJobTranscript(jobId).then((transcript) => {
        useStore.setState((s) => {
          const existingTranscript = s.transcript[jobId] ?? [];
          const mergedTx = [
            ...transcript,
            ...existingTranscript.filter((e) => !transcript.some((ne) => ne.seq === e.seq)),
          ].sort((a, b) => a.seq - b.seq);
          return {
            transcript: { ...s.transcript, [jobId]: mergedTx },
          };
        });
    }).catch(() => {});
  }, [jobId]);

  // Hydrate activity timeline from the persisted event store. This ensures the
  // timeline is populated when navigating to a completed or resumed job, not
  // just during live streaming.
  useEffect(() => {
    if (!jobId) return;
    fetchJobTimeline(jobId).then((fetched) => {
      if (fetched.length === 0) return;
      useStore.setState((s) => {
        const live = s.timelines[jobId] ?? [];
        // Merge historical entries with any live entries already in the store.
        // Live entries take precedence for the same timestamp (they may carry
        // active:true state set by the progress_headline SSE handler).
        const liveByTs = new Map(live.map((e) => [e.timestamp, e]));
        const merged = fetched.map((e) => liveByTs.get(e.timestamp) ?? e);
        // Append any live entries not covered by the historical fetch.
        const fetchedTs = new Set(fetched.map((e) => e.timestamp));
        const extraLive = live.filter((e) => !fetchedTs.has(e.timestamp));
        const full = [...merged, ...extraLive].sort(
          (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
        );
        return { timelines: { ...s.timelines, [jobId]: full } };
      });
    }).catch(() => {});
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
    }).catch(() => {});
  }, [jobId]);

  // Load diff data: on mount, when job reaches terminal state, or when diff tab selected.
  const jobState = job?.state;
  useEffect(() => {
    if (!jobId) return;
    fetchJobDiff(jobId)
      .then((files) => {
        useStore.setState((s) => ({
          diffs: { ...s.diffs, [jobId]: files },
        }));
      })
      .catch(() => {});
  }, [jobId, jobState, tab]);

  const doCancelJob = useCallback(async () => {
    if (!jobId) return;
    const updated = await cancelJob(jobId);
    useStore.setState((s) => ({ jobs: { ...s.jobs, [updated.id]: updated } }));
    toast.success("Job canceled");
  }, [jobId]);

  const handleRerun = useCallback(async () => {
    if (!jobId) return;
    setActionLoading(true);
    try {
      const result = await rerunJob(jobId);
      toast.success(`Rerun: ${result.id}`);
      navigate(`/jobs/${result.id}`);
    } catch (e) { toast.error(String(e)); }
    finally { setActionLoading(false); }
  }, [jobId, navigate]);

  const handleResolve = useCallback(async (action: "merge" | "smart_merge" | "create_pr" | "discard" | "agent_merge") => {
    if (!jobId) return;
    setResolveLoading(action);
    try {
      const res = await resolveJob(jobId, action);
      if (res.prUrl) {
        toast.success("PR created", {
          description: res.prUrl,
          action: { label: "Open", onClick: () => window.open(res.prUrl!, "_blank") },
        });
      } else if (action === "agent_merge") {
        toast.success("Resolving with agent…");
      } else {
        toast.success(action === "merge" || action === "smart_merge" ? "Merged" : action === "create_pr" ? "PR created" : "Discarded");
      }
    } catch (e) { toast.error(String(e)); }
    finally { setResolveLoading(null); }
  }, [jobId]);

  if (!jobId) return null;

  if (loading) return <JobDetailSkeleton />;

  if (!job) {
    return (
      <div className="flex flex-col items-center gap-3 py-16">
        <p className="text-muted-foreground">Job not found</p>
        <Button variant="ghost" onClick={() => navigate("/")}>
          <ArrowLeft size={16} />
          Back to Dashboard
        </Button>
      </div>
    );
  }

  const canCancel = ["queued", "running", "waiting_for_approval"].includes(job.state);
  const canRetry = job.state === "failed";
  const isRunning = job.state === "running";
  const needsResolution =
    job.state === "succeeded" &&
    (job.resolution === "unresolved" || job.resolution === "conflict" || !job.resolution);
  const isResolved =
    job.state === "succeeded" &&
    !!job.resolution &&
    job.resolution !== "unresolved" &&
    job.resolution !== "conflict";
  const canArchive = (job.state === "failed" || job.state === "canceled") && !job.archivedAt;

  return (
    <div className="max-w-6xl mx-auto">
      <Button variant="ghost" size="sm" onClick={() => navigate("/")} className="mb-4">
        <ArrowLeft size={14} />
        Dashboard
      </Button>

      {/* Job header */}
      <div className="rounded-lg border border-border bg-card p-4 mb-4">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <div className="flex items-center gap-2">
            {job.title ? (
              <span className="text-lg font-bold text-foreground">{job.title}</span>
            ) : (
              <span className="text-lg font-bold text-foreground">{job.id}</span>
            )}
            <span className="text-sm text-muted-foreground font-mono">{job.id}</span>
            <span aria-live="polite"><StateBadge state={job.state} /></span>
            <SdkBadge sdk={job.sdk} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {canCancel && (
              <Button
                size="sm"
                variant="outline"
                className="text-destructive border-destructive/40 hover:bg-destructive/10"
                onClick={() => setCancelOpen(true)}
              >
                <XCircle size={14} />
                Cancel
              </Button>
            )}
            {canRetry && (
              <Button size="sm" variant="outline" loading={actionLoading} onClick={handleRerun}>
                <RotateCcw size={14} />
                Retry
              </Button>
            )}
            {needsResolution && hasChanges && (
              <>
                {job.resolution !== "conflict" && (
                  <Tooltip content="Ask the agent to merge changes onto the base branch">
                    <Button
                      size="sm"
                      variant="outline"
                      className="gap-1"
                      loading={resolveLoading === "smart_merge"}
                      disabled={resolveLoading !== null}
                      onClick={() => handleResolve("smart_merge")}
                    >
                      <GitMerge size={14} />
                      Merge
                    </Button>
                  </Tooltip>
                )}
                {job.resolution === "conflict" && (
                  <Tooltip content="Ask the agent to resolve the merge conflict">
                    <Button
                      size="sm"
                      variant="outline"
                      className="gap-1"
                      loading={resolveLoading === "agent_merge"}
                      disabled={resolveLoading !== null}
                      onClick={() => handleResolve("agent_merge")}
                    >
                      <GitMerge size={14} />
                      Resolve with Agent
                    </Button>
                  </Tooltip>
                )}
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1"
                  loading={resolveLoading === "create_pr"}
                  disabled={resolveLoading !== null}
                  onClick={() => handleResolve("create_pr")}
                >
                  <GitPullRequest size={14} />
                  Create PR
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1 text-destructive border-destructive/40 hover:bg-destructive/10"
                  onClick={() => setDiscardOpen(true)}
                >
                  <Trash2 size={14} />
                  Discard
                </Button>
              </>
            )}
            {needsResolution && !hasChanges && (
              <Button
                size="sm"
                variant="outline"
                className="gap-1"
                loading={resolveLoading === "discard"}
                disabled={resolveLoading !== null}
                onClick={() => handleResolve("discard")}
              >
                <CheckCircle2 size={14} />
                Mark Done
              </Button>
            )}
            {isResolved && !job.archivedAt && (
              <Button
                size="sm"
                variant="outline"
                className="gap-1 text-green-600 border-green-500/40 hover:bg-green-500/10"
                onClick={() => setCompleteOpen(true)}
              >
                <CheckCircle2 size={14} />
                Complete & Archive
              </Button>
            )}
            {canArchive && (
              <Button
                size="sm"
                variant="outline"
                className="gap-1"
                onClick={() => setCompleteOpen(true)}
              >
                <Archive size={14} />
                Archive
              </Button>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1.5 mb-3">
          <BookOpen size={13} className="text-muted-foreground/70 shrink-0" />
          <span className="text-sm text-muted-foreground font-mono">{job.repo.split("/").pop() ?? job.repo}</span>
        </div>

        {job.progressHeadline && (job.state === "running" || job.state === "queued") && (
          <p className="text-sm italic text-primary/70 mb-3">{job.progressHeadline}</p>
        )}

        {(!isMobile || metaExpanded) ? (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-x-6 gap-y-2 text-sm mb-3">
            {[
              ["Branch", job.branch ?? "—"],
              ["Base", job.baseRef],
              ["Worktree", job.worktreePath ? job.worktreePath.split("/").pop() ?? job.worktreePath : "—"],
              ...(job.sdk && job.sdk !== "copilot" ? [["SDK", job.sdk]] : []),
              ["Created", new Date(job.createdAt).toLocaleString()],
              ...(job.completedAt ? [["Completed", new Date(job.completedAt).toLocaleString()]] : []),
            ].map(([label, value]) => (
              <div key={label}>
                <p className="text-xs text-muted-foreground uppercase font-semibold tracking-wide">{label}</p>
                <p className="text-sm break-all">{value}</p>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex items-center gap-3 text-sm text-muted-foreground">
            <span>{job.repo}</span>
            <span>·</span>
            <span className="font-mono text-xs">{job.branch || "main"}</span>
          </div>
        )}
        {isMobile && (
          <button
            onClick={() => setMetaExpanded(!metaExpanded)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors mt-1"
          >
            <ChevronDown className={cn("h-3 w-3 transition-transform", metaExpanded && "rotate-180")} />
            {metaExpanded ? "Less" : "More details"}
          </button>
        )}

        {job.prUrl && (
          <a
            href={job.prUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
          >
            <ExternalLink size={14} />
            View Pull Request
          </a>
        )}

        <div className="rounded-md border border-border bg-background p-3 mt-3">
          <p className="text-sm whitespace-pre-wrap leading-relaxed text-foreground">{job.prompt}</p>
        </div>

        {/* Model downgrade banner */}
        {job.modelDowngraded && (
          <div className="flex items-start gap-2 mt-3 rounded-md border border-amber-500/30 bg-amber-500/10 p-3">
            <ArrowDownCircle size={16} className="text-amber-500 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-amber-500">Model downgraded</p>
              <p className="text-sm text-amber-400 mt-0.5">
                Requested <span className="font-semibold">{job.requestedModel}</span> but the SDK served <span className="font-semibold">{job.actualModel}</span>.
                The job was stopped before the agent could proceed with the wrong model.
              </p>
              <p className="text-xs text-amber-400/70 mt-1">
                You can discard this job, create a PR with any partial changes, or resume with additional instructions.
              </p>
            </div>
          </div>
        )}

        {/* Failure banner */}
        {job.state === "failed" && (
          <div className="flex items-start gap-2 mt-3 rounded-md border border-red-500/30 bg-red-500/10 p-3">
            <XCircle size={16} className="text-red-500 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-red-500">Job failed</p>
              <p className="text-sm text-red-400 mt-0.5">{job.failureReason ?? "No additional details available"}</p>
            </div>
          </div>
        )}

        {/* Success banner */}
        {job.state === "succeeded" && (() => {
          const isConflict = job.resolution === "conflict";
          const isSignOff = job.resolution === "unresolved" || !job.resolution;
          return (
            <div className={`mt-3 rounded-md border p-3 ${isConflict ? "border-amber-500/30 bg-amber-500/10" : isSignOff ? "border-blue-500/30 bg-blue-500/10" : "border-green-500/30 bg-green-500/10"}`}>
              <div className="flex items-start gap-2">
                {isConflict ? (
                  <AlertTriangle size={16} className="text-amber-500 shrink-0 mt-0.5" />
                ) : isSignOff ? (
                  <GitMerge size={16} className="text-blue-500 shrink-0 mt-0.5" />
                ) : (
                  <CheckCircle2 size={16} className="text-green-500 shrink-0 mt-0.5" />
                )}
                <div>
                  <p className={`text-sm font-medium ${isConflict ? "text-amber-500" : isSignOff ? "text-blue-500" : "text-green-500"}`}>
                    {isConflict ? "Merge conflict — user input required" : isSignOff ? "Sign off required" : "Job succeeded"}
                  </p>
                  <p className={`text-sm mt-0.5 ${isConflict ? "text-amber-400" : isSignOff ? "text-blue-400" : "text-green-400"}`}>
                    {job.resolution === "merged" && "Changes merged into base branch."}
                    {job.resolution === "pr_created" && "Pull request created."}
                    {job.resolution === "discarded" && (hasChanges ? "Changes discarded." : "Completed — no changes to merge.")}
                    {isConflict && "Merge conflict detected. Resolve with the agent, create a PR to fix manually, or discard."}
                    {isSignOff && (
                      hasChanges
                        ? "Choose how to handle the changes: auto merge onto the main worktree, create a PR, or discard."
                        : "Completed with no changes to merge."
                    )}
                  </p>
                </div>
              </div>
            </div>
          );
        })()}

        {/* Canceled banner */}
        {job.state === "canceled" && (
          <div className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/10 p-3">
            <div className="flex items-start gap-2">
              <AlertTriangle size={16} className="text-amber-500 shrink-0 mt-0.5" />
              <p className="text-sm font-medium text-amber-500">Job canceled</p>
            </div>
          </div>
        )}
      </div>

      {completeOpen && job && (
        <CompleteJobDialog job={job} open onClose={() => setCompleteOpen(false)} onArchived={() => navigate("/")} />
      )}

      <Tabs value={tab} onValueChange={(v) => {
        setTab(v);
        if (v === "terminal" && !jobTerminalSessionId) handleOpenJobTerminal();
      }} className="mb-4">
        <div className="flex items-center gap-2">
          <TabsList className="flex-1 overflow-x-auto">
            {isMobile ? (
              <>
                <TabsTrigger value="live">Live</TabsTrigger>
                <TabsTrigger value="files"><FolderTree size={13} className="mr-1.5" />Files</TabsTrigger>
                <TabsTrigger value="diff"><GitBranch size={13} className="mr-1.5" />Changes</TabsTrigger>
                {hasArtifacts && <TabsTrigger value="artifacts">Artifacts</TabsTrigger>}
              </>
            ) : (
              <>
                <TabsTrigger value="live">Live</TabsTrigger>
                <TabsTrigger value="files"><FolderTree size={13} className="mr-1.5" />Files</TabsTrigger>
                <TabsTrigger value="diff"><GitBranch size={13} className="mr-1.5" />Changes</TabsTrigger>
                {hasWorktree && <TabsTrigger value="terminal"><TerminalSquare size={13} className="mr-1.5" />Terminal</TabsTrigger>}
                {hasArtifacts && <TabsTrigger value="artifacts">Artifacts</TabsTrigger>}
              </>
            )}
          </TabsList>
          {isMobile && hasWorktree && (
            <Tooltip content="Open terminal">
              <button
                onClick={() => {
                  if (!jobTerminalSessionId) handleOpenJobTerminal();
                  useStore.setState({ terminalDrawerOpen: true });
                }}
                className="p-2.5 rounded-md hover:bg-accent text-muted-foreground"
                aria-label="Open terminal"
              >
                <TerminalSquare className="h-4 w-4" />
              </button>
            </Tooltip>
          )}
        </div>
      </Tabs>

      {tab === "live" && (
        <div ref={liveContainerRef} className="flex flex-col" style={liveHeight ? { height: liveHeight } : { height: 'calc(100vh - 13rem)' }}>
          <div className="flex-1 min-h-[24rem]">
            <TranscriptPanel jobId={jobId} interactive jobState={job.state} pausable={isRunning} prompt={job.prompt} promptTimestamp={job.createdAt} />
          </div>
          <div className="overflow-y-auto max-h-[40vh] space-y-4 mt-4 shrink-0">
            <PlanPanel jobId={jobId} />
            <ExecutionTimeline jobId={jobId} />
            <MetricsPanel jobId={jobId} isRunning={isRunning} />
          </div>
        </div>
      )}

      {tab === "files" && (
        <Suspense fallback={<div className="flex justify-center py-10"><Spinner /></div>}>
          <WorkspaceBrowser jobId={jobId} />
        </Suspense>
      )}

      {tab === "diff" && (
        <Suspense fallback={<div className="flex justify-center py-10"><Spinner /></div>}>
          <DiffViewer jobId={jobId} jobState={job.state} resolution={job.resolution} archivedAt={job.archivedAt} onAskSent={() => setTab("live")} />
        </Suspense>
      )}

      {tab === "artifacts" && (
        <Suspense fallback={<div className="flex justify-center py-10"><Spinner /></div>}>
          <ArtifactViewer jobId={jobId} />
        </Suspense>
      )}

      {tab === "terminal" && hasWorktree && (
        <div className="h-[32rem] rounded-lg overflow-hidden border border-border">
          {jobTerminalSessionId ? (
            <TerminalPanel sessionId={jobTerminalSessionId} />
          ) : (
            <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
              <Spinner />
              <span className="ml-2">Starting terminal…</span>
            </div>
          )}
        </div>
      )}

      <ConfirmDialog
        open={cancelOpen}
        onClose={() => setCancelOpen(false)}
        onConfirm={doCancelJob}
        title="Cancel Job?"
        description="This will stop the running agent. Any uncommitted work will remain in the worktree."
        confirmLabel="Cancel Job"
      />

      <ConfirmDialog
        open={discardOpen}
        onClose={() => setDiscardOpen(false)}
        onConfirm={async () => {
          await resolveJob(jobId!, "discard");
          toast.success("Discarded");
        }}
        title="Discard Changes?"
        description="All changes in the worktree will be deleted. This cannot be undone."
        confirmLabel="Discard"
      />

    </div>
  );
}
