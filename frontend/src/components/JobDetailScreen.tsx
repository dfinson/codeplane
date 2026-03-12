import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useTowerStore, selectJobs } from "../store";
import type { JobSummary } from "../store";
import { fetchJob, cancelJob, rerunJob } from "../api/client";
import { useSSE } from "../hooks/useSSE";
import { StateBadge } from "./StateBadge";
import { TranscriptPanel } from "./TranscriptPanel";
import { LogsPanel } from "./LogsPanel";
import { ExecutionTimeline } from "./ExecutionTimeline";

export function JobDetailScreen() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const jobs = useTowerStore(selectJobs);
  const job: JobSummary | undefined = jobId ? jobs[jobId] : undefined;
  const [loading, setLoading] = useState(!job);
  const [actionLoading, setActionLoading] = useState(false);

  // Job-scoped SSE connection for full event streaming
  useSSE(jobId);

  // Fetch job details if not in store
  useEffect(() => {
    if (!jobId || job) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    fetchJob(jobId)
      .then((fetched) => {
        if (cancelled) return;
        useTowerStore.setState((state) => ({
          jobs: { ...state.jobs, [fetched.id]: fetched },
        }));
      })
      .catch(() => {
        // Job not found — stay on page with error state
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [jobId, job]);

  const handleCancel = useCallback(async () => {
    if (!jobId) return;
    setActionLoading(true);
    try {
      const updated = await cancelJob(jobId);
      useTowerStore.setState((state) => ({
        jobs: { ...state.jobs, [updated.id]: updated },
      }));
    } catch {
      // Error already handled by ApiError
    } finally {
      setActionLoading(false);
    }
  }, [jobId]);

  const handleRerun = useCallback(async () => {
    if (!jobId) return;
    setActionLoading(true);
    try {
      const result = await rerunJob(jobId);
      navigate(`/jobs/${result.id}`);
    } catch {
      // Error already handled by ApiError
    } finally {
      setActionLoading(false);
    }
  }, [jobId, navigate]);

  if (!jobId) return null;

  if (loading) {
    return (
      <div className="job-detail">
        <div className="empty-state">
          <div className="empty-state__text">Loading job…</div>
        </div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="job-detail">
        <button className="job-detail__back" onClick={() => navigate("/")}>
          ← Back to Dashboard
        </button>
        <div className="empty-state">
          <div className="empty-state__text">Job not found</div>
        </div>
      </div>
    );
  }

  const repoName = job.repo.split("/").pop() ?? job.repo;
  const canCancel = job.state === "queued" || job.state === "running" || job.state === "waiting_for_approval";
  const canRerun = job.state === "succeeded" || job.state === "failed" || job.state === "canceled";

  return (
    <div className="job-detail">
      <button className="job-detail__back" onClick={() => navigate("/")}>
        ← Back to Dashboard
      </button>

      <div className="job-meta">
        <div className="job-meta__header">
          <div className="job-meta__title">
            <span>{job.id.slice(0, 8)}</span>
            <StateBadge state={job.state} />
          </div>
          <div className="job-meta__actions">
            {canCancel && (
              <button
                className="btn btn--danger btn--sm"
                onClick={handleCancel}
                disabled={actionLoading}
              >
                Cancel
              </button>
            )}
            {canRerun && (
              <button
                className="btn btn--sm"
                onClick={handleRerun}
                disabled={actionLoading}
              >
                Rerun
              </button>
            )}
          </div>
        </div>

        <div className="job-meta__grid">
          <div className="job-meta__field">
            <span className="job-meta__label">Repository</span>
            <span className="job-meta__value">{repoName}</span>
          </div>
          <div className="job-meta__field">
            <span className="job-meta__label">Branch</span>
            <span className="job-meta__value">{job.branch ?? "—"}</span>
          </div>
          <div className="job-meta__field">
            <span className="job-meta__label">Base Ref</span>
            <span className="job-meta__value">{job.baseRef}</span>
          </div>
          <div className="job-meta__field">
            <span className="job-meta__label">Strategy</span>
            <span className="job-meta__value">{job.strategy}</span>
          </div>
          <div className="job-meta__field">
            <span className="job-meta__label">Created</span>
            <span className="job-meta__value">
              {new Date(job.createdAt).toLocaleString()}
            </span>
          </div>
          <div className="job-meta__field">
            <span className="job-meta__label">Updated</span>
            <span className="job-meta__value">
              {new Date(job.updatedAt).toLocaleString()}
            </span>
          </div>
          {job.completedAt && (
            <div className="job-meta__field">
              <span className="job-meta__label">Completed</span>
              <span className="job-meta__value">
                {new Date(job.completedAt).toLocaleString()}
              </span>
            </div>
          )}
        </div>

        <div className="job-meta__prompt">{job.prompt}</div>
      </div>

      <div className="panels">
        <TranscriptPanel jobId={jobId} />
        <LogsPanel jobId={jobId} />
        <ExecutionTimeline jobId={jobId} />
      </div>
    </div>
  );
}
