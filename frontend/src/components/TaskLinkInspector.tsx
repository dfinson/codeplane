import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { fetchTaskLinkInspection, startTaskLink } from "../api/client";
import { useStore } from "../store";
import { Button } from "./ui/button";

export function TaskLinkInspector({ projectId, taskId }: { projectId: string; taskId: string }) {
  return <SelectedTaskInspector key={JSON.stringify([projectId, taskId])} projectId={projectId} taskId={taskId} />;
}

function SelectedTaskInspector({ projectId, taskId }: { projectId: string; taskId: string }) {
  const key = JSON.stringify([projectId, taskId]);
  const inspection = useStore((state) => state.taskLinkInspections[key]);
  const connection = useStore((state) => state.connectionStatus);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const active = useRef(true);
  const reserved = useRef(false);
  const navigate = useNavigate();
  useLayoutEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setError(null);
    fetchTaskLinkInspection(projectId, taskId).then((value) => {
      if (!disposed) useStore.setState((state) => ({ taskLinkInspections: { ...state.taskLinkInspections, [key]: value } }));
    }).catch((cause: unknown) => { if (!disposed) setError(String(cause)); })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [projectId, taskId, key, connection, refresh]);

  async function start() {
    if (!inspection?.canStart || loading || error || reserved.current) return;
    reserved.current = true;
    setStarting(true);
    try {
      const task = await startTaskLink(projectId, taskId);
      if (active.current && task.jobId) navigate(`/jobs/${encodeURIComponent(task.jobId)}`);
      else if (active.current) throw new Error("Start returned no Job identity.");
    } catch (cause: unknown) {
      if (active.current) setError(String(cause));
    } finally {
      reserved.current = false;
      if (active.current) setStarting(false);
    }
  }

  return <section aria-label="Task inspector" className="mb-4 space-y-4 rounded-lg border border-primary/30 bg-card p-4">
    <header className="flex flex-wrap items-center justify-between gap-3">
      <h2 className="font-semibold">Task inspector</h2>
      <Button variant="outline" size="sm" disabled={loading || starting} onClick={() => setRefresh((value) => value + 1)}>Refresh eligibility</Button>
    </header>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {loading && <p role="status" className="text-sm text-muted-foreground">Reading exact task and Start eligibility...</p>}
    {inspection && <>
      <p className="text-sm font-medium">{inspection.task.storyNodeId ?? inspection.task.trackerTicketRef ?? taskId}</p>
      {inspection.promptSource === "historical_reference" && <p className="rounded-md border border-border p-3 text-sm">Only a historical task reference is stored. This is the complete launch instruction, not a reconstructed source definition. The agent must locate the original requirements or report them missing.</p>}
      <div><h3 className="mb-2 text-sm font-medium">Complete launch prompt</h3><pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/40 p-3 text-sm">{inspection.prompt}</pre></div>
      <div className="space-y-2">
        <h3 className="text-sm font-medium">Predecessor and delivery conditions</h3>
        <p className="text-xs text-muted-foreground">Dependencies require a completed Job with merged or PR-created delivery. An agent finishing a turn is not that delivery, and a tracker update remains a separate outcome.</p>
        {inspection.dependencies.length === 0 && <p className="text-sm text-muted-foreground">No predecessor conditions.</p>}
        {inspection.dependencies.map((dependency) => <div key={dependency.key} className="rounded-md border border-border p-3 text-sm">
          <p className="break-all font-medium">{dependency.key}</p>
          <p>{dependency.satisfied ? "Satisfied" : "Not satisfied"} / {dependency.jobState ?? "No Job"} / {dependency.resolution ?? "No delivery resolution"}</p>
          {dependency.jobId && <Link className="text-primary underline" to={`/jobs/${encodeURIComponent(dependency.jobId)}`}>Open predecessor Job</Link>}
        </div>)}
      </div>
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div><dt className="text-xs text-muted-foreground">Execution provider / requested model</dt><dd>{inspection.sdk} / {inspection.model ?? "Provider default, not a served-model claim"}</dd></div>
        <div><dt className="text-xs text-muted-foreground">Preset / mode</dt><dd>{inspection.preset} / {inspection.mode}</dd></div>
        <div><dt className="text-xs text-muted-foreground">Recipe revision</dt><dd>{inspection.recipeRevisionId ?? "No recipe revision selected for this Task's Start path"}</dd></div>
        <div><dt className="text-xs text-muted-foreground">Chain gate</dt><dd>{inspection.requiresChainApproval ? "Approval required" : "No attached-chain gate"}</dd></div>
      </dl>
      {inspection.pendingApprovalIds.length > 0 && <p className="break-words text-sm">Pending approval identities: {inspection.pendingApprovalIds.join(", ")}</p>}
      {inspection.disabledReason && <p className="text-sm">{inspection.disabledReason}</p>}
      <div className="flex flex-wrap gap-2">
        {inspection.task.jobId ? <Button asChild variant="outline"><Link to={`/jobs/${encodeURIComponent(inspection.task.jobId)}`}>Open linked Job</Link></Button> :
          <Button disabled={!inspection.canStart || loading || starting || error !== null} onClick={() => void start()}>{starting ? "Starting task..." : "Start inspected task"}</Button>}
      </div>
    </>}
  </section>;
}
