import { useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useTowerStore, selectJobs } from "../store";
import type { JobSummary } from "../store";
import { JobCard } from "./JobCard";

export function RepositoryDetailView() {
  const { repoPath } = useParams<{ repoPath: string }>();
  const navigate = useNavigate();
  const jobs = useTowerStore(selectJobs);
  const decodedPath = useMemo(
    () => (repoPath ? decodeURIComponent(repoPath) : ""),
    [repoPath],
  );

  const repoJobs: JobSummary[] = Object.values(jobs)
    .filter((j) => j.repo === decodedPath)
    .sort(
      (a, b) =>
        new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
    );

  const activeJobs = repoJobs.filter((j) =>
    ["queued", "running", "waiting_for_approval"].includes(j.state),
  );
  const recentJobs = repoJobs.filter(
    (j) => !["queued", "running", "waiting_for_approval"].includes(j.state),
  ).slice(0, 20);

  const repoName = decodedPath.split("/").pop() ?? decodedPath;

  if (!decodedPath) {
    return (
      <div className="empty-state">
        <div className="empty-state__text">Repository not found</div>
      </div>
    );
  }

  return (
    <div className="repo-detail">
      <button className="job-detail__back" onClick={() => navigate("/")}>
        ← Back to Dashboard
      </button>

      <div className="repo-detail__header">
        <div className="repo-detail__name">{repoName}</div>
        <div className="repo-detail__path">{decodedPath}</div>
      </div>

      {/* MCP/Tool Config Table — placeholder for Phase 5 */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="panel__header">
          <span>Tool / MCP Configuration</span>
        </div>
        <div className="panel__body">
          <div className="empty-state">
            <div className="empty-state__text">
              MCP configuration will be available in a future update
            </div>
          </div>
        </div>
      </div>

      {/* Active Jobs */}
      <div className="panel" style={{ marginBottom: 16, maxHeight: "none" }}>
        <div className="panel__header">
          <span>Active Jobs</span>
          <span style={{ fontSize: 11 }}>{activeJobs.length}</span>
        </div>
        <div className="panel__body" style={{ padding: 8 }}>
          {activeJobs.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state__text">No active jobs</div>
            </div>
          ) : (
            activeJobs.map((job) => <JobCard key={job.id} job={job} />)
          )}
        </div>
      </div>

      {/* Recent Jobs */}
      <div className="panel" style={{ maxHeight: "none" }}>
        <div className="panel__header">
          <span>Recent Jobs</span>
          <span style={{ fontSize: 11 }}>{recentJobs.length}</span>
        </div>
        <div className="panel__body" style={{ padding: 8 }}>
          {recentJobs.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state__text">No recent jobs</div>
            </div>
          ) : (
            recentJobs.map((job) => <JobCard key={job.id} job={job} />)
          )}
        </div>
      </div>
    </div>
  );
}
