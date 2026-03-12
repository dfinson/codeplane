import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useTowerStore } from "../store";
import { fetchJobs } from "../api/client";
import { KanbanBoard } from "./KanbanBoard";
import { MobileJobList } from "./MobileJobList";

export function DashboardScreen() {
  const navigate = useNavigate();

  // Seed store with existing jobs on mount
  useEffect(() => {
    let cancelled = false;
    fetchJobs({ limit: 100 })
      .then((result) => {
        if (cancelled) return;
        useTowerStore.setState((state) => {
          const updated = { ...state.jobs };
          for (const job of result.items) {
            updated[job.id] = job;
          }
          return { jobs: updated };
        });
      })
      .catch(() => {
        // API unavailable — rely on SSE snapshot
      });
    return () => { cancelled = true; };
  }, []);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600 }}>Jobs</h2>
        <button className="btn btn--primary" onClick={() => navigate("/jobs/new")}>
          + New Job
        </button>
      </div>
      <KanbanBoard />
      <MobileJobList />
    </div>
  );
}
