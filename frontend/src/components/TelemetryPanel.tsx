import { useState, useEffect, useRef } from "react";
import { BarChart3, ChevronDown, ChevronRight, Cpu, Clock, Wrench, MessageSquare } from "lucide-react";
import { fetchJobTelemetry } from "../api/client";
import { Badge } from "./ui/badge";
import { Progress } from "./ui/progress";
import { Spinner } from "./ui/spinner";

interface TelemetryData {
  available: boolean;
  model?: string;
  durationMs?: number;
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
  contextWindowSize?: number;
  currentContextTokens?: number;
  toolCallCount?: number;
  totalToolDurationMs?: number;
  toolCalls?: { name: string; durationMs: number; success: boolean }[];
  approvalCount?: number;
  agentMessages?: number;
  operatorMessages?: number;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

export function TelemetryPanel({ jobId }: { jobId: string }) {
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<TelemetryData | null>(null);
  const [loading, setLoading] = useState(false);

  const dataRef = useRef(data);
  dataRef.current = data;

  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;
    const load = () => {
      setLoading((prev) => !dataRef.current && prev === false ? true : prev);
      fetchJobTelemetry(jobId)
        .then((d) => { if (!cancelled) setData(d); })
        .catch(() => { if (!cancelled) setData({ available: false }); })
        .finally(() => { if (!cancelled) setLoading(false); });
    };
    load();
    const interval = setInterval(load, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [expanded, jobId]);

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
            <div className="flex flex-col gap-4 mt-3">
              {/* Token usage */}
              <div>
                <div className="flex items-center gap-1.5 mb-2">
                  <Cpu size={14} className="text-blue-400" />
                  <span className="text-xs font-semibold text-muted-foreground">Token Usage</span>
                </div>
                <div className="grid grid-cols-3 gap-3 text-center">
                  <div>
                    <p className="text-lg font-bold">{formatTokens(data.inputTokens ?? 0)}</p>
                    <p className="text-xs text-muted-foreground">Input</p>
                  </div>
                  <div>
                    <p className="text-lg font-bold">{formatTokens(data.outputTokens ?? 0)}</p>
                    <p className="text-xs text-muted-foreground">Output</p>
                  </div>
                  <div>
                    <p className="text-lg font-bold">{formatTokens(data.totalTokens ?? 0)}</p>
                    <p className="text-xs text-muted-foreground">Total</p>
                  </div>
                </div>
                {data.contextWindowSize ? (
                  <div className="mt-2">
                    <div className="flex justify-between mb-1">
                      <span className="text-xs text-muted-foreground">Context window</span>
                      <span className="text-xs text-muted-foreground">
                        {formatTokens(data.currentContextTokens ?? data.totalTokens ?? 0)} / {formatTokens(data.contextWindowSize)}
                      </span>
                    </div>
                    <Progress
                      value={Math.min(100, ((data.currentContextTokens ?? data.totalTokens ?? 0) / data.contextWindowSize) * 100)}
                      color={((data.currentContextTokens ?? data.totalTokens ?? 0) / data.contextWindowSize) > 0.8 ? "red" : "blue"}
                    />
                  </div>
                ) : null}
              </div>

              {/* Model & Duration */}
              <div className="flex items-start gap-6">
                {data.model && (
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Model</p>
                    <Badge variant="secondary">{data.model}</Badge>
                  </div>
                )}
                {data.durationMs ? (
                  <div>
                    <div className="flex items-center gap-1 mb-1">
                      <Clock size={12} className="text-muted-foreground" />
                      <span className="text-xs text-muted-foreground">Duration</span>
                    </div>
                    <p className="text-sm font-semibold">{formatDuration(data.durationMs)}</p>
                  </div>
                ) : null}
                <div>
                  <div className="flex items-center gap-1 mb-1">
                    <MessageSquare size={12} className="text-muted-foreground" />
                    <span className="text-xs text-muted-foreground">Messages</span>
                  </div>
                  <p className="text-sm">{data.agentMessages ?? 0} agent / {data.operatorMessages ?? 0} operator</p>
                </div>
              </div>

              {/* Tool calls */}
              {(data.toolCallCount ?? 0) > 0 && (
                <div>
                  <div className="flex items-center gap-1.5 mb-2">
                    <Wrench size={14} className="text-yellow-400" />
                    <span className="text-xs font-semibold text-muted-foreground">
                      Tool Calls ({data.toolCallCount})
                    </span>
                    <span className="text-xs text-muted-foreground ml-auto">
                      {formatDuration(data.totalToolDurationMs ?? 0)} total
                    </span>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    {(data.toolCalls ?? []).slice(-10).map((tc, i) => (
                      <div key={i} className="flex items-center justify-between px-2 py-1 rounded text-xs bg-background">
                        <div className="flex items-center gap-2">
                          <div className={`w-1.5 h-1.5 rounded-full ${tc.success ? "bg-green-500" : "bg-red-500"}`} />
                          <code className="font-mono">{tc.name}</code>
                        </div>
                        <span className="text-muted-foreground">{formatDuration(tc.durationMs)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
