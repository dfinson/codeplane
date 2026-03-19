import { useEffect, useRef, useState } from "react";
import { Activity, ChevronDown, ChevronRight } from "lucide-react";
import { useStore, selectJobTimeline } from "../store";

export function ExecutionTimeline({ jobId }: { jobId: string }) {
  const timeline = useStore(selectJobTimeline(jobId));
  const bottomRef = useRef<HTMLDivElement>(null);
  const [collapsed, setCollapsed] = useState(false);

  // Auto-scroll to newest entry when timeline grows (only when expanded)
  useEffect(() => {
    if (!collapsed) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [timeline.length, collapsed]);

  // Nothing to show
  if (timeline.length === 0) return null;

  // Find the current (active) or most recent entry
  const current = [...timeline].reverse().find((e) => e.active) ?? timeline[timeline.length - 1];

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <button
        className="flex items-center gap-2 px-4 py-2.5 w-full text-left border-b border-border hover:bg-accent/50 transition-colors"
        onClick={() => setCollapsed((c) => !c)}
      >
        {collapsed ? (
          <ChevronRight size={13} className="text-muted-foreground shrink-0" />
        ) : (
          <ChevronDown size={13} className="text-muted-foreground shrink-0" />
        )}
        <Activity size={13} className="text-muted-foreground shrink-0" />
        <span className="text-sm font-semibold text-muted-foreground">Activity</span>
        {collapsed && current && (
          <span className="text-xs text-muted-foreground/70 truncate ml-1">
            — {current.active ? current.headline : current.headlinePast}
          </span>
        )}
        <span className="ml-auto text-xs text-muted-foreground/50 tabular-nums shrink-0">
          {timeline.length} milestone{timeline.length !== 1 ? "s" : ""}
        </span>
      </button>

      {!collapsed && (
        <div className="max-h-[260px] overflow-y-auto">
          <div className="px-4 py-3 relative pl-10">
            {/* Vertical rail */}
            <div className="absolute left-[24px] top-3 bottom-3 w-px bg-border" />
            <div className="space-y-4">
              {timeline.map((entry, i) => (
                <div key={i} className="relative">
                  {/* Dot on the rail */}
                  <div
                    className={`absolute -left-[22px] top-[3px] w-2.5 h-2.5 rounded-full border-2 shrink-0 ${
                      entry.active
                        ? "border-blue-400 bg-blue-400/30 ring-2 ring-blue-400/20"
                        : "border-border bg-background"
                    }`}
                  />
                  <div className="flex flex-col gap-0.5">
                    <span className="text-xs text-muted-foreground/50 font-mono">
                      {new Date(entry.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                    </span>
                    <span className={`text-xs leading-snug font-medium ${entry.active ? "text-foreground" : "text-muted-foreground"}`}>
                      {entry.active ? entry.headline : entry.headlinePast}
                    </span>
                    {entry.summary && (
                      <span className="text-xs leading-relaxed text-muted-foreground/70">
                        {entry.summary}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
            <div ref={bottomRef} />
          </div>
        </div>
      )}
    </div>
  );
}
