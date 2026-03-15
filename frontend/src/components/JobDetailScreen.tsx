import { useEffect, useState, useCallback, lazy, Suspense } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, RotateCcw, XCircle, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { useTowerStore, selectJobs } from "../store";
import type { JobSummary } from "../store";
import { fetchJob, cancelJob, rerunJob } from "../api/client";
import { useSSE } from "../hooks/useSSE";
import { StateBadge } from "./StateBadge";
import { TranscriptPanel } from "./TranscriptPanel";
import { LogsPanel } from "./LogsPanel";
import { ExecutionTimeline } from "./ExecutionTimeline";
import { ApprovalBanner } from "./ApprovalBanner";
import { TelemetryPanel } from "./TelemetryPanel";
import { Button } from "./ui/button";
import { Spinner } from "./ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "./ui/tabs";

const DiffViewer = lazy(() => import("./DiffViewer"));
const WorkspaceBrowser = lazy(() => import("./WorkspaceBrowser"));
const ArtifactViewer = lazy(() => import("./ArtifactViewer"));

export function JobDetailScreen() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const jobs = useTowerStore(selectJobs);
  const job: JobSummary | undefined = jobId ? jobs[jobId] : undefined;
  const [loading, setLoading] = useState(!job);
  const [actionLoading, setActionLoading] = useState(false);
  const [tab, setTab] = useState("live");

  useSSE(jobId);

  useEffect(() => {
    if (!jobId) { setLoading(false); return; }
    const existing = useTowerStore.getState().jobs[jobId];
    if (existing) { setLoading(false); return; }
    fetchJob(jobId)
      .then((f) => useTowerStore.setState((s) => ({ jobs: { ...s.jobs, [f.id]: f } })))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [jobId]);

  const handleCancel = useCallback(async () => {
    if (!jobId) return;
    setActionLoading(true);
    try {
      const updated = await cancelJob(jobId);
      useTowerStore.setState((s) => ({ jobs: { ...s.jobs, [updated.id]: updated } }));
      toast.success("Job canceled");
    } catch (e) { toast.error(String(e)); }
    finally { setActionLoading(false); }
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

  if (!jobId) return null;

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Spinner size="lg" />
      </div>
    );
  }

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

  const repoName = job.repo.split("/").pop() ?? job.repo;
  const canCancel = ["queued", "running", "waiting_for_approval"].includes(job.state);
  const canRerun = ["succeeded", "failed", "canceled"].includes(job.state);
  const isInteractive = ["running", "waiting_for_approval"].includes(job.state);

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
            <span className="text-lg font-bold text-foreground">{job.title ?? job.id}</span>
            <span className="text-sm text-muted-foreground font-mono">{job.id}</span>
            <StateBadge state={job.state} />
          </div>
          <div className="flex items-center gap-2">
            {canCancel && (
              <Button
                size="sm"
                variant="outline"
                className="text-destructive border-destructive/40 hover:bg-destructive/10"
                loading={actionLoading}
                onClick={handleCancel}
              >
                <XCircle size={14} />
                Cancel
              </Button>
            )}
            {canRerun && (
              <Button size="sm" variant="outline" loading={actionLoading} onClick={handleRerun}>
                <RotateCcw size={14} />
                Rerun
              </Button>
            )}
          </div>
        </div>

        <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-x-6 gap-y-2 text-sm mb-3">
          {[
            ["Repo", repoName],
            ["Branch", job.branch ?? "—"],
            ["Base", job.baseRef],
            ["Strategy", job.strategy],
            ["Created", new Date(job.createdAt).toLocaleString()],
            ...(job.completedAt ? [["Completed", new Date(job.completedAt).toLocaleString()]] : []),
          ].map(([label, value]) => (
            <div key={label}>
              <p className="text-xs text-muted-foreground uppercase font-semibold tracking-wide">{label}</p>
              <p className="text-sm break-all">{value}</p>
            </div>
          ))}
        </div>

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
      </div>

      <Tabs value={tab} onValueChange={setTab} className="mb-4">
        <TabsList className="overflow-x-auto">
          <TabsTrigger value="live">Live</TabsTrigger>
          <TabsTrigger value="diff">Diff</TabsTrigger>
          <TabsTrigger value="workspace">Workspace</TabsTrigger>
          <TabsTrigger value="artifacts">Artifacts</TabsTrigger>
        </TabsList>
      </Tabs>

      {tab === "live" && (
        <div className="flex flex-col gap-4">
          <ApprovalBanner jobId={jobId} />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4" style={{ minHeight: 400 }}>
            <TranscriptPanel jobId={jobId} interactive={isInteractive} />
            <LogsPanel jobId={jobId} />
          </div>
          <ExecutionTimeline jobId={jobId} />
          <TelemetryPanel jobId={jobId} />
        </div>
      )}

      {tab === "diff" && (
        <Suspense fallback={<div className="flex justify-center py-10"><Spinner /></div>}>
          <DiffViewer jobId={jobId} />
        </Suspense>
      )}

      {tab === "workspace" && (
        <Suspense fallback={<div className="flex justify-center py-10"><Spinner /></div>}>
          <WorkspaceBrowser jobId={jobId} />
        </Suspense>
      )}

      {tab === "artifacts" && (
        <Suspense fallback={<div className="flex justify-center py-10"><Spinner /></div>}>
          <ArtifactViewer jobId={jobId} />
        </Suspense>
      )}
    </div>
  );
}
