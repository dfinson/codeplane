import { useState, useEffect, useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useShallow } from "zustand/react/shallow";
import { ArrowLeft, Search } from "lucide-react";
import { useStore, selectArchivedJobs, enrichJob } from "../store";
import type { JobSummary } from "../store";
import { fetchJobs } from "../api/client";
import { StateBadge } from "./StateBadge";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Spinner } from "./ui/spinner";

const RESOLUTION_FILTERS = [
  { value: "all", label: "All" },
  { value: "merged", label: "Merged" },
  { value: "pr_created", label: "PR created" },
  { value: "discarded", label: "Discarded" },
  { value: "failed", label: "Failed" },
  { value: "canceled", label: "Canceled" },
] as const;

export function HistoryScreen() {
  const navigate = useNavigate();
  const archivedJobs = useStore(useShallow(selectArchivedJobs));
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);

  // Load archived jobs on mount
  useEffect(() => {
    setLoading(true);
    fetchJobs({ state: "review,completed,failed,canceled", limit: 100, archived: true } as Parameters<typeof fetchJobs>[0])
      .then((result) => {
        useStore.setState((state) => {
          const updated = { ...state.jobs };
          for (const job of result.items) updated[job.id] = enrichJob(job as JobSummary);
          return { jobs: updated };
        });
        setCursor(result.cursor);
        setHasMore(result.hasMore);
      })
      .catch((err) => console.error("Failed to fetch job history", err))
      .finally(() => setLoading(false));
  }, []);

  const loadMore = useCallback(async () => {
    if (!cursor) return;
    try {
      const result = await fetchJobs({ state: "succeeded,failed,canceled", limit: 50, cursor, archived: true } as Parameters<typeof fetchJobs>[0]);
      useStore.setState((state) => {
        const updated = { ...state.jobs };
        for (const job of result.items) updated[job.id] = enrichJob(job as JobSummary);
        return { jobs: updated };
      });
      setCursor(result.cursor);
      setHasMore(result.hasMore);
    } catch { /* user can retry */ }
  }, [cursor]);

  const filtered = useMemo(() => {
    let jobs = archivedJobs;
    if (search) {
      const q = search.toLowerCase();
      jobs = jobs.filter(
        (j) =>
          (j.title ?? "").toLowerCase().includes(q) ||
          j.prompt.toLowerCase().includes(q) ||
          j.id.toLowerCase().includes(q) ||
          j.repo.toLowerCase().includes(q),
      );
    }
    if (filter !== "all") {
      jobs = jobs.filter((j) => {
        if (filter === "failed") return j.state === "failed";
        if (filter === "canceled") return j.state === "canceled";
        return j.resolution === filter;
      });
    }
    return jobs;
  }, [archivedJobs, search, filter]);

  return (
    <div className="max-w-4xl mx-auto">
      <Button variant="ghost" size="sm" onClick={() => navigate("/")} className="mb-4">
        <ArrowLeft size={14} />
        Dashboard
      </Button>

      <h3 className="text-lg font-semibold text-foreground mb-4">Job History</h3>

      {/* Filters */}
      <div className="flex flex-wrap gap-2 mb-4">
        <div className="relative flex-1 min-w-[200px]">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search by title, prompt, repo..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 h-8 text-sm"
            aria-label="Search jobs"
          />
        </div>
        <div className="flex rounded-lg bg-muted p-0.5 gap-0.5">
          {RESOLUTION_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              aria-pressed={filter === f.value}
              className={`rounded-md px-2 py-1 text-xs font-medium transition-colors ${
                filter === f.value
                  ? "bg-background text-foreground shadow"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner />
        </div>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-muted-foreground text-center py-12">
          No archived jobs{search ? " matching your search" : ""}
        </p>
      ) : (
        <div className="space-y-2">
          {filtered.map((job) => (
            <HistoryRow key={job.id} job={job} onNavigate={() => navigate(`/jobs/${job.id}`)} />
          ))}
          {hasMore && (
            <Button variant="ghost" className="w-full" onClick={loadMore}>
              Load more
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function HistoryRow({ job, onNavigate }: { job: JobSummary; onNavigate: () => void }) {
  const repoName = job.repo.split("/").pop() ?? job.repo;
  const resolutionColor: Record<string, string> = {
    merged: "text-green-500",
    pr_created: "text-blue-500",
    discarded: "text-muted-foreground",
  };

  return (
    <div
      className="flex items-center gap-3 rounded-lg border border-border bg-card p-3 cursor-pointer hover:border-primary/60 hover:bg-accent transition-colors"
      onClick={onNavigate}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-sm font-semibold truncate">{job.title ?? job.id}</span>
          <StateBadge state={job.state} />
          {job.resolution && (
            <span className={`text-xs font-medium ${resolutionColor[job.resolution] ?? "text-muted-foreground"}`}>
              {job.resolution}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span>{repoName}</span>
          <span className="font-mono">{job.id}</span>
          {job.completedAt && <span>{new Date(job.completedAt).toLocaleDateString()}</span>}
        </div>
        <p className="text-xs text-foreground/60 truncate mt-0.5">{job.prompt}</p>
      </div>
    </div>
  );
}
