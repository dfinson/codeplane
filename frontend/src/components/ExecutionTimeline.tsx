import { useMemo } from "react";
import { useStore, selectJobLogs } from "../store";

function isTimelineEvent(msg: string, level: string): boolean {
  if (level === "error") return true;
  const lower = msg.toLowerCase();
  return ["state", "started", "completed", "created", "failed", "succeeded", "canceled", "approval", "progress:", "merged"].some(
    (kw) => lower.includes(kw),
  );
}

function dotColor(level: string, msg: string): string {
  if (level === "error") return "bg-red-500";
  const lower = msg.toLowerCase();
  if (lower.includes("succeeded") || lower.includes("completed")) return "bg-green-500";
  if (lower.includes("running") || lower.includes("started") || lower.includes("progress:")) return "bg-blue-500";
  if (lower.includes("failed") || lower.includes("canceled")) return "bg-red-500";
  return "bg-border";
}

export function ExecutionTimeline({ jobId }: { jobId: string }) {
  const logs = useStore(selectJobLogs(jobId));
  const events = useMemo(
    () => logs.filter((l) => isTimelineEvent(l.message, l.level)),
    [logs],
  );

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <div className="flex items-center px-4 py-2.5 border-b border-border">
        <span className="text-sm font-semibold text-muted-foreground">Timeline</span>
      </div>
      <div className="max-h-[300px] overflow-y-auto">
        {events.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">No timeline events yet</p>
        ) : (
          <div className="p-4 space-y-1">
            {events.map((e, i) => (
              <div key={i} className="flex items-start gap-3 py-1 text-xs">
                <div className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${dotColor(e.level, e.message)}`} />
                <span className="text-muted-foreground font-mono shrink-0">
                  {new Date(e.timestamp).toLocaleTimeString()}
                </span>
                <span className="text-foreground/80">{e.message}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
