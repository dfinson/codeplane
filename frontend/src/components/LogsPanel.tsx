import { useState, useRef, useEffect, useCallback } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useStore, selectJobLogs } from "../store";
import { fetchJobLogs } from "../api/client";
import { cn } from "../lib/utils";
import { Tooltip } from "./ui/tooltip";

const LEVELS = ["debug", "info", "warn", "error"] as const;
type Level = typeof LEVELS[number];

/** Priority order — higher = more severe */
const LEVEL_PRIORITY: Record<Level, number> = { debug: 0, info: 1, warn: 2, error: 3 };

const LEVEL_CLASSES: Record<string, string> = {
  debug: "text-muted-foreground",
  info: "text-blue-400",
  warn: "text-yellow-400",
  error: "text-red-400",
};

const LEVEL_DOT: Record<string, string> = {
  debug: "bg-muted-foreground",
  info: "bg-blue-400",
  warn: "bg-yellow-400",
  error: "bg-red-400",
};

export function LogsPanel({ jobId }: { jobId: string }) {
  const allLogs = useStore(selectJobLogs(jobId));
  /** Minimum severity level shown — also drives the historical fetch */
  const [minLevel, setMinLevel] = useState<Level>("info");
  const [collapsed, setCollapsed] = useState(true);
  const viewportRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

  // Filter live SSE entries by the same min-level rule used for historical fetch.
  const logs = allLogs.filter(
    (l) => LEVEL_PRIORITY[l.level as Level] >= LEVEL_PRIORITY[minLevel],
  );

  // Fetch historical log lines at the selected minimum level and merge into
  // the store.  Re-runs whenever the level or jobId changes.
  useEffect(() => {
    fetchJobLogs(jobId, minLevel).then((fetched) => {
      useStore.setState((s) => {
        const existing = s.logs[jobId] ?? [];
        // Merge: prefer fetched rows; keep any live SSE rows not yet in fetched
        const fetchedSeqs = new Set(fetched.map((l) => l.seq));
        const merged = [
          ...fetched,
          ...existing.filter((l) => !fetchedSeqs.has(l.seq)),
        ].sort((a, b) => a.seq - b.seq);
        return { logs: { ...s.logs, [jobId]: merged } };
      });
    }).catch(() => {});
  }, [jobId, minLevel]);

  useEffect(() => {
    if (stickRef.current && viewportRef.current) {
      viewportRef.current.scrollTo({ top: viewportRef.current.scrollHeight });
    }
  }, [logs.length]);

  const virtualizer = useVirtualizer({
    count: logs.length,
    getScrollElement: () => viewportRef.current,
    estimateSize: () => 28,
    overscan: 20,
    enabled: !collapsed,
  });

  useEffect(() => {
    if (stickRef.current && logs.length > 0 && !collapsed) {
      virtualizer.scrollToIndex(logs.length - 1, { align: "end" });
    }
  }, [logs.length, virtualizer, collapsed]);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  const handleLevelClick = useCallback((level: Level) => {
    setMinLevel(level);
    // Reset stick-to-bottom when changing level
    stickRef.current = true;
  }, []);

  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-border bg-card">
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="flex items-center justify-between px-4 py-2.5 border-b border-border w-full text-left hover:bg-accent/30 transition-colors"
      >
        <span className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
          {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
          Logs
          <span className="tabular-nums text-xs text-muted-foreground/60">({logs.length})</span>
        </span>
        {/* Minimum-level selector — radio style, each button activates that level+ */}
        <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
            {LEVELS.map((level) => {
            const active = LEVEL_PRIORITY[level] === LEVEL_PRIORITY[minLevel];
            const dimmed = LEVEL_PRIORITY[level] < LEVEL_PRIORITY[minLevel];
            return (
              <Tooltip key={level} content={`Show ${level} and above`}>
                <button
                  type="button"
                  onClick={() => handleLevelClick(level)}
                  aria-label={`Filter by ${level}`}
                  aria-pressed={active}
                  className={cn(
                    "flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border transition-colors",
                    active
                      ? "border-transparent bg-muted text-foreground ring-1 ring-ring"
                      : dimmed
                      ? "border-transparent text-muted-foreground/40"
                      : "border-border text-muted-foreground hover:text-foreground",
                  )}
                >
                  <span className={cn("w-1.5 h-1.5 rounded-full", dimmed ? "bg-muted-foreground/30" : LEVEL_DOT[level])} />
                  {level}
                </button>
              </Tooltip>
            );
          })}
        </div>
      </button>

      {!collapsed && (
        <div
          ref={viewportRef}
          className="h-64 min-h-0 overflow-y-auto overscroll-contain font-mono"
          style={{ contain: "strict" }}
          onScroll={handleScroll}
        >
          {logs.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">No logs</p>
          ) : (
            <div style={{ height: `${virtualizer.getTotalSize()}px`, width: "100%", position: "relative" }}>
              {virtualizer.getVirtualItems().map((virtualRow) => {
                const l = logs[virtualRow.index];
                if (!l) return null;
                return (
                  <div
                    key={virtualRow.key}
                    data-index={virtualRow.index}
                    ref={virtualizer.measureElement}
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      width: "100%",
                      transform: `translateY(${virtualRow.start}px)`,
                    }}
                  >
                    <div className="flex items-start gap-2 text-xs py-0.5 hover:bg-accent/30 px-2 rounded">
                      <span className="text-muted-foreground shrink-0 tabular-nums">
                        {new Date(l.timestamp).toLocaleTimeString()}
                      </span>
                      <span className={cn("uppercase font-semibold w-10 shrink-0", LEVEL_CLASSES[l.level])}>
                        {l.level}
                      </span>
                      <span className="text-foreground/80 break-words min-w-0">{l.message}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
