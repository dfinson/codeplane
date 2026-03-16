import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { useStore, enrichJob } from "../store";
import type { JobSummary } from "../store";
import { fetchJobs } from "../api/client";
import { KanbanBoard } from "./KanbanBoard";
import { MobileJobList } from "./MobileJobList";
import { Button } from "./ui/button";

export function DashboardScreen() {
  const navigate = useNavigate();

  useEffect(() => {
    fetchJobs({ limit: 100, archived: false })
      .then((result) => {
        useStore.setState((state) => {
          const updated = { ...state.jobs };
          for (const job of result.items) updated[job.id] = enrichJob(job as JobSummary);
          return { jobs: updated };
        });
      })
      .catch(() => {});
  }, []);

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-foreground">Jobs</h3>
        <Button size="sm" onClick={() => navigate("/jobs/new")}>
          <Plus size={16} />
          New Job
        </Button>
      </div>
      <KanbanBoard />
      <MobileJobList />
    </div>
  );
}
