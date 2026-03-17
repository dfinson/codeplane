import { useEffect, useRef } from "react";
import { Activity } from "lucide-react";
import { useStore, selectJobTimeline } from "../store";

export function ExecutionTimeline({ jobId }: { jobId: string }) {
  const timeline = useStore(selectJobTimeline(jobId));
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to newest entry when timeline grows
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [timeline.length]);

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border">
        <Activity size={13} className="text-muted-foreground" />
        <span className="text-sm font-semibold text-muted-foreground">Activity</span>
        {timeline.length > 0 && (
          <span className="ml-auto text-[11px] text-muted-foreground/50 tabular-nums">
            {timeline.length} event{timeline.length !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      <div className="max-h-[260px] overflow-y-auto">
        {timeline.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">No activity yet</p>
        ) : (
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
                    <span className="text-[10px] text-muted-foreground/50 font-mono">
                      {new Date(entry.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                    </span>
                    <span className={`text-xs leading-snug ${entry.active ? "text-foreground italic" : "text-muted-foreground"}`}>
                      {entry.active ? entry.headline : entry.headlinePast}
                    </span>
                  </div>
                </div>
              ))}
            </div>
            <div ref={bottomRef} />
          </div>
        )}
      </div>
    </div>
  );
}
