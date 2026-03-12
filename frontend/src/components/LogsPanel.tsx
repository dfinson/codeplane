import { useState, useMemo, useRef, useEffect } from "react";
import { useTowerStore, selectJobLogs } from "../store";

const LEVELS = ["debug", "info", "warn", "error"] as const;
type Level = (typeof LEVELS)[number];

export function LogsPanel({ jobId }: { jobId: string }) {
  const logs = useTowerStore(selectJobLogs(jobId));
  const [enabledLevels, setEnabledLevels] = useState<Set<Level>>(
    new Set(["info", "warn", "error"]),
  );
  const bottomRef = useRef<HTMLDivElement>(null);

  const filteredLogs = useMemo(
    () => logs.filter((l) => enabledLevels.has(l.level as Level)),
    [logs, enabledLevels],
  );

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [filteredLogs.length]);

  function toggleLevel(level: Level) {
    setEnabledLevels((prev) => {
      const next = new Set(prev);
      if (next.has(level)) {
        next.delete(level);
      } else {
        next.add(level);
      }
      return next;
    });
  }

  return (
    <div className="panel">
      <div className="panel__header">
        <span>Logs</span>
        <div className="log-filters">
          {LEVELS.map((level) => (
            <button
              key={level}
              className={`log-filter log-filter--${level} ${enabledLevels.has(level) ? "log-filter--active" : ""}`}
              onClick={() => toggleLevel(level)}
            >
              {level}
            </button>
          ))}
        </div>
      </div>
      <div className="panel__body">
        {filteredLogs.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state__text">No log entries</div>
          </div>
        ) : (
          filteredLogs.map((line) => (
            <div key={`${line.jobId}-${line.seq}`} className="log-line">
              <span className="log-line__time">
                {new Date(line.timestamp).toLocaleTimeString()}
              </span>
              <span className={`log-line__level log-line__level--${line.level}`}>
                {line.level}
              </span>
              <span className="log-line__msg">{line.message}</span>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
