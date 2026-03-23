import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { useStore, enrichJob } from "../store";
import type { JobSummary } from "../store";
import { fetchJobs } from "../api/client";
import { KanbanBoard } from "./KanbanBoard";
import { MobileJobList } from "./MobileJobList";
import { Button } from "./ui/button";
import { KanbanSkeleton } from "./KanbanSkeleton";

const SKELETON_DELAY_MS = 500;

export function DashboardScreen() {
  const navigate = useNavigate();
  const hasJobs = useStore((state) => Object.keys(state.jobs).length > 0);
  const [loading, setLoading] = useState(true);
  const [showSkeleton, setShowSkeleton] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setShowSkeleton(true), SKELETON_DELAY_MS);
    fetchJobs({ limit: 100, archived: false })
      .then((result) => {
        useStore.setState((state) => {
          const updated = { ...state.jobs };
          for (const job of result.items) updated[job.id] = enrichJob(job as JobSummary);
          return { jobs: updated };
        });
      })
      .catch((err) => console.error("Failed to fetch jobs", err))
      .finally(() => {
        clearTimeout(timer);
        setLoading(false);
      });
    return () => clearTimeout(timer);
  }, []);

  if (loading && (!hasJobs || showSkeleton)) return <KanbanSkeleton />;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-foreground">Jobs</h3>
        <Button size="sm" onClick={() => navigate("/jobs/new")}>
          <Plus size={16} />
          New Job
          <kbd className="ml-1 hidden sm:inline rounded border border-white/20 px-1 py-px font-mono text-[10px] leading-none opacity-70">Alt+N</kbd>
        </Button>
      </div>
      <KanbanBoard />
      <MobileJobList />
    </div>
  );
}
