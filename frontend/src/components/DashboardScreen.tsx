import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, AlertTriangle } from "lucide-react";
import { useStore, enrichJob } from "../store";
import type { JobSummary } from "../store";
import { fetchJobs, fetchScorecard } from "../api/client";
import { KanbanBoard } from "./KanbanBoard";
import { MobileJobList } from "./MobileJobList";
import { Button } from "./ui/button";
import { KanbanSkeleton } from "./KanbanSkeleton";

function QuotaWarningBanner() {
  const [quotaPct, setQuotaPct] = useState<number | null>(null);

  useEffect(() => {
    fetchScorecard(7)
      .then((sc) => {
        if (!sc.quotaJson) return;
        try {
          const q = JSON.parse(sc.quotaJson);
          const snapshots = Array.isArray(q) ? q : q?.snapshots ?? [q];
          const latest = snapshots[snapshots.length - 1];
          if (latest && typeof latest.percentage_used === "number") {
            setQuotaPct(latest.percentage_used);
          } else if (latest && latest.used != null && latest.total != null && latest.total > 0) {
            setQuotaPct((latest.used / latest.total) * 100);
          }
        } catch { /* ignore */ }
      })
      .catch(() => {});
  }, []);

  if (quotaPct === null || quotaPct <= 80) return null;

  return (
    <div className="rounded-lg border border-yellow-500/40 bg-yellow-500/10 px-4 py-2.5 mb-4 flex items-center gap-2 text-sm">
      <AlertTriangle size={16} className="text-yellow-400 shrink-0" />
      <span className="text-yellow-200">
        Copilot quota is {quotaPct.toFixed(0)}% used — new jobs may be throttled or denied.
      </span>
    </div>
  );
}

export function DashboardScreen() {
  const navigate = useNavigate();
  const hasJobs = useStore((state) => Object.keys(state.jobs).length > 0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
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
        setLoading(false);
      });
  }, []);

  if (loading && !hasJobs) return <KanbanSkeleton />;

  return (
    <div>
      <QuotaWarningBanner />
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-foreground">Jobs</h3>
        <Button size="sm" className="hidden sm:inline-flex" onClick={() => navigate("/jobs/new")}>
          <Plus size={16} />
          New Job
          <kbd className="ml-1 rounded border border-white/20 px-1 py-px font-mono text-[10px] leading-none opacity-70">Alt+N</kbd>
        </Button>
      </div>
      <KanbanBoard />
      <MobileJobList />
      {/* Mobile FAB — thumb-zone primary action, hidden on tablet/desktop */}
      <Button
        size="icon"
        className="sm:hidden fixed bottom-6 right-6 z-50 h-14 w-14 rounded-full shadow-lg"
        onClick={() => navigate("/jobs/new")}
        aria-label="New Job"
      >
        <Plus size={22} />
      </Button>
    </div>
  );
}
