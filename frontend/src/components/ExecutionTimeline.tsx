import { useMemo } from "react";
import { useTowerStore, selectJobLogs } from "../store";

interface TimelineEvent {
  timestamp: string;
  text: string;
  variant: "active" | "success" | "error" | "default";
}

export function ExecutionTimeline({ jobId }: { jobId: string }) {
  const logs = useTowerStore(selectJobLogs(jobId));

  const events = useMemo<TimelineEvent[]>(() => {
    // Derive timeline from meaningful log events — state changes, errors, etc.
    const result: TimelineEvent[] = [];
    for (const log of logs) {
      if (log.level === "error") {
        result.push({
          timestamp: log.timestamp,
          text: log.message,
          variant: "error",
        });
      } else if (
        log.message.toLowerCase().includes("state") ||
        log.message.toLowerCase().includes("started") ||
        log.message.toLowerCase().includes("completed") ||
        log.message.toLowerCase().includes("created")
      ) {
        result.push({
          timestamp: log.timestamp,
          text: log.message,
          variant: log.message.toLowerCase().includes("completed")
            ? "success"
            : "active",
        });
      }
    }
    return result;
  }, [logs]);

  return (
    <div className="panel panel--full">
      <div className="panel__header">
        <span>Execution Timeline</span>
        <span style={{ fontSize: 11 }}>{events.length} events</span>
      </div>
      <div className="panel__body">
        {events.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state__text">No timeline events yet</div>
          </div>
        ) : (
          <div className="timeline">
            {events.map((ev, i) => (
              <div key={`${ev.timestamp}-${i}`} className="timeline-event">
                <div className={`timeline-event__dot timeline-event__dot--${ev.variant}`} />
                <span className="timeline-event__time">
                  {new Date(ev.timestamp).toLocaleTimeString()}
                </span>
                <span className="timeline-event__text">{ev.text}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
