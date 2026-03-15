import { useState, useEffect, useRef } from "react";
import { BarChart3, ChevronDown, ChevronRight, Clock, MessageSquare } from "lucide-react";
import { fetchJobTelemetry } from "../api/client";
import { Badge } from "./ui/badge";
import { Progress } from "./ui/progress";
import { Spinner } from "./ui/spinner";

interface TelemetryData {
  available: boolean;
  model?: string;
  durationMs?: number;
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
  contextWindowSize?: number;
  currentContextTokens?: number;
  contextUtilization?: number;
  approvalCount?: number;
  agentMessages?: number;
  operatorMessages?: number;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const totalSeconds = Math.floor(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

const POLL_INTERVAL_MS = 5_000;

export function TelemetryPanel({ jobId, isRunning = false }: { jobId: string; isRunning?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<TelemetryData | null>(null);
  const [loading, setLoading] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!expanded) return;
    if (!data) {
      setLoading(true);
      fetchJobTelemetry(jobId)
        .then(setData)
        .catch(() => setData({ available: false }))
        .finally(() => setLoading(false));
    }
  }, [expanded, data, jobId]);

  // Poll while expanded and job is running
  useEffect(() => {
    if (expanded && isRunning) {
      const poll = () => {
        fetchJobTelemetry(jobId)
          .then(setData)
          .catch(() => setData({ available: false }));
      };
      intervalRef.current = setInterval(poll, POLL_INTERVAL_MS);
      return () => {
        if (intervalRef.current) clearInterval(intervalRef.current);
      };
    }
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, [expanded, isRunning, jobId]);

  const utilization = data?.contextUtilization ?? 0;

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-2.5 flex items-center gap-2 hover:bg-accent transition-colors text-left"
      >
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <BarChart3 size={14} />
        <span className="text-sm font-semibold text-muted-foreground">Telemetry</span>
        {data?.available && data.totalTokens ? (
          <Badge variant="secondary" className="ml-auto">
            {formatTokens(data.totalTokens)} tokens
          </Badge>
        ) : null}
      </button>

      {expanded && (
        <div className="px-4 pb-4 border-t border-border">
          {loading ? (
            <div className="flex justify-center py-4"><Spinner size="sm" /></div>
          ) : !data?.available ? (
            <p className="text-sm text-muted-foreground py-3">No telemetry data available</p>
          ) : (
            <div className="flex flex-col gap-3 mt-3">
              {/* Summary row: model, duration, messages */}
              <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
                {data.model && (
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs text-muted-foreground">Model</span>
                    <Badge variant="secondary">{data.model}</Badge>
                  </div>
                )}
                {(data.durationMs ?? 0) > 0 && (
                  <div className="flex items-center gap-1.5">
                    <Clock size={12} className="text-muted-foreground" />
                    <span className="text-xs text-muted-foreground">Duration</span>
                    <span className="text-sm font-semibold">{formatDuration(data.durationMs!)}</span>
                  </div>
                )}
                <div className="flex items-center gap-1.5">
                  <MessageSquare size={12} className="text-muted-foreground" />
                  <span className="text-sm">{data.agentMessages ?? 0} agent / {data.operatorMessages ?? 0} operator</span>
                </div>
              </div>

              {/* Token usage */}
              <div className="grid grid-cols-3 gap-3 text-center">
                <div>
                  <p className="text-lg font-bold">{formatTokens(data.promptTokens ?? 0)}</p>
                  <p className="text-xs text-muted-foreground">Prompt</p>
                </div>
                <div>
                  <p className="text-lg font-bold">{formatTokens(data.completionTokens ?? 0)}</p>
                  <p className="text-xs text-muted-foreground">Completion</p>
                </div>
                <div>
                  <p className="text-lg font-bold">{formatTokens(data.totalTokens ?? 0)}</p>
                  <p className="text-xs text-muted-foreground">Total</p>
                </div>
              </div>

              {/* Context window utilization */}
              {data.contextWindowSize ? (
                <div>
                  <div className="flex justify-between mb-1">
                    <span className="text-xs text-muted-foreground">Context window</span>
                    <span className="text-xs text-muted-foreground">
                      {formatTokens(data.currentContextTokens ?? 0)} / {formatTokens(data.contextWindowSize)}
                    </span>
                  </div>
                  <Progress
                    value={Math.min(100, utilization * 100)}
                    color={utilization > 0.8 ? "red" : "blue"}
                  />
                </div>
              ) : null}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
