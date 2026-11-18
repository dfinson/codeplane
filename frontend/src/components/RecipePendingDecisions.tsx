import { useEffect, useState } from "react";
import { fetchJobPendingDecisions } from "../api/client";
import { useStore } from "../store";
import { InlineApprovalCard } from "./CuratedFeed";
import { Button } from "./ui/button";

export function RecipePendingDecisions({ jobId }: { jobId: string }) {
  const [after, setAfter] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const version = useStore((state) => state.projectBoardVersion);
  const connection = useStore((state) => state.connectionStatus);
  const key = JSON.stringify([jobId, after]);
  const page = useStore((state) => state.jobPendingDecisionPages[key]);
  useEffect(() => {
    let disposed = false;
    setError(null);
    fetchJobPendingDecisions(jobId, after).then((value) => {
      if (!disposed) useStore.setState((state) => ({ jobPendingDecisionPages: { ...state.jobPendingDecisionPages, [key]: value } }));
    }).catch((cause: unknown) => { if (!disposed) setError(String(cause)); });
    return () => { disposed = true; };
  }, [jobId, after, key, refresh, version, connection]);
  if (!error && page?.items.length === 0 && !after) return null;
  return <section aria-label="Pending Job decisions" className="space-y-3 rounded-lg border border-border bg-card p-3">
    <h3 className="text-sm font-semibold">Pending Job decisions</h3>
    <p className="text-xs text-muted-foreground">Permissions are separate from step completion and delivery. Unscoped decisions remain Job-wide blockers.</p>
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    {page?.items.map((item) => <div key={item.approval.id} className="space-y-2">
      {item.stepInstanceId && item.recipeRunId && <a className="text-xs text-primary underline" href={`/jobs/${encodeURIComponent(jobId)}?${new URLSearchParams({ run: item.recipeRunId, step: item.stepInstanceId, ...(item.attemptId ? { attempt: item.attemptId } : {}) })}`}>Open exact decision target</a>}
      {item.humanGate ? <p className="text-sm">Human gate: inspect its exact inputs and proposed next steps before deciding.</p> :
        <InlineApprovalCard approval={{ ...item.approval, notes: item.approval.notes ?? null }} />}
    </div>)}
    <div className="flex flex-wrap gap-2">
      <Button size="sm" variant="outline" onClick={() => setRefresh((value) => value + 1)}>Refresh decisions</Button>
      {after && <Button size="sm" variant="outline" onClick={() => setAfter("")}>First decisions</Button>}
      {page?.nextCursor && <Button size="sm" variant="outline" onClick={() => setAfter(page.nextCursor!)}>More decisions</Button>}
    </div>
  </section>;
}
