import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { fetchCurrentRecipeRun, fetchRecipeRun, fetchRecipeRunHistory, fetchRecipeRunSteps, resumeSelectedRecipeRun } from "../api/client";
import type { RecipeRun } from "../api/types";
import { useStore } from "../store";
import { Button } from "./ui/button";
import { RecipeStepInspector } from "./RecipeStepInspector";
import { cn } from "../lib/utils";
import { RecipePendingDecisions } from "./RecipePendingDecisions";

interface RecipeRunTarget {
  jobId: string;
  requestedRunId?: string;
  requestedStepId?: string;
  requestedAttemptId?: string;
}

export function RecipeRunPanel(props: RecipeRunTarget) {
  return <JobRecipePanel key={JSON.stringify(props)} {...props} />;
}

function JobRecipePanel({ jobId, ...target }: RecipeRunTarget) {
  const run = useStore((state) => state.recipeCurrentRuns[jobId]);
  const version = useStore((state) => state.recipeVersions[jobId] ?? 0);
  const connection = useStore((state) => state.connectionStatus);
  const jobUpdatedAt = useStore((state) => state.jobs[jobId]?.updatedAt);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let disposed = false;
    setError(null);
    fetchCurrentRecipeRun(jobId).then((response) => {
      if (disposed) return;
      useStore.setState((state) => {
        const previous = state.recipeCurrentRuns[jobId];
        const next = response.run;
        if (previous && next && (previous.runNumber > next.runNumber || (previous.id === next.id && previous.version > next.version))) return state;
        return {
          recipeCurrentRuns: { ...state.recipeCurrentRuns, [jobId]: next },
          recipeRuns: next ? { ...state.recipeRuns, [next.id]: next } : state.recipeRuns,
        };
      });
    }).catch((cause: unknown) => { if (!disposed) setError(String(cause)); });
    return () => { disposed = true; };
  }, [jobId, version, connection, jobUpdatedAt, refresh]);
  if (error) return <div role="alert" className="mx-3 mt-2 rounded-lg border border-destructive/40 p-3 text-sm">
    <p>Recipe identity could not be read: {error}</p><Button variant="outline" size="sm" onClick={() => setRefresh((v) => v + 1)}>Retry recipe read</Button>
  </div>;
  if (run === null) return null;
  if (!run) return <p role="status" className="mx-3 mt-2 text-xs text-muted-foreground">Reading pinned recipe...</p>;
  return <section aria-label="Job recipe" className="min-w-0">
    <RecipePendingDecisions key={jobId} jobId={jobId} />
    <RecipeRunBrowser jobId={jobId} current={run} {...target} />
  </section>;
}

function RecipeRunBrowser({ jobId, current, requestedRunId, requestedStepId, requestedAttemptId }: RecipeRunTarget & { current: RecipeRun }) {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(requestedRunId ?? null);
  const [historyPage, setHistoryPage] = useState<{ after: number; highWater?: number }>({ after: 0 });
  const [refresh, setRefresh] = useState(0);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const runId = selectedRunId ?? current.id;
  const selected = useStore((state) => state.recipeRuns[runId]);
  const displayed = selected ?? (runId === current.id ? current : undefined);
  const historyKey = JSON.stringify([jobId, historyPage.after, historyPage.highWater]);
  const history = useStore((state) => state.recipeRunHistoryPages[historyKey]);
  useEffect(() => {
    let disposed = false;
    setHistoryError(null);
    fetchRecipeRunHistory(jobId, { ...historyPage, limit: 30 }).then((value) => {
      if (!disposed) useStore.setState((state) => ({ recipeRunHistoryPages: { ...state.recipeRunHistoryPages, [historyKey]: value } }));
    }).catch((cause: unknown) => { if (!disposed) setHistoryError(String(cause)); });
    return () => { disposed = true; };
  }, [jobId, historyKey, historyPage, refresh]);
  useEffect(() => {
    let disposed = false;
    setRunError(null);
    fetchRecipeRun(jobId, runId).then((value) => {
      if (!disposed) useStore.setState((state) => {
        if ((state.recipeRuns[runId]?.version ?? 0) > value.version) return state;
        return { recipeRuns: { ...state.recipeRuns, [runId]: value } };
      });
    }).catch((cause: unknown) => { if (!disposed) setRunError(String(cause)); });
    return () => { disposed = true; };
  }, [jobId, runId, refresh]);
  return <div className="space-y-4">
    <header className="space-y-2">
      {displayed ? <>
        <h3 className="text-lg font-semibold">{displayed.snapshot.definition.name}</h3>
        <p className="text-xs text-muted-foreground">Pinned run {displayed.runNumber} / revision {displayed.snapshot.revision}</p>
        <details className="rounded-lg border border-border bg-card p-3">
          <summary className="cursor-pointer text-sm font-medium">View pinned definition</summary>
          <pre className="mt-3 whitespace-pre-wrap break-words text-xs">{JSON.stringify(displayed.snapshot, null, 2)}</pre>
        </details>
      </> : <p role="status" className="text-sm text-muted-foreground">{runError ? "Selected run unavailable." : "Reading selected historical run..."}</p>}
    </header>
    <div className="flex flex-wrap items-end gap-3">
      <label className="min-w-0 flex-1 text-xs text-muted-foreground">Run history
        <select aria-label="Recipe run history" className="mt-1 block w-full rounded-md border border-input bg-background p-2 text-sm text-foreground" value={selectedRunId ?? ""} onChange={(event) => setSelectedRunId(event.target.value || null)}>
          <option value="">Current run {current.runNumber}</option>
          {selectedRunId && !history?.items.some((item) => item.id === selectedRunId) && <option value={selectedRunId}>Selected run {selected?.runNumber ?? selectedRunId}</option>}
          {history?.items.map((item) => <option key={item.id} value={item.id}>Run {item.runNumber} / revision {item.snapshot.revision} / {item.state}</option>)}
        </select>
      </label>
      {historyPage.after > 0 && <Button size="sm" variant="outline" onClick={() => setHistoryPage({ after: 0, highWater: historyPage.highWater })}>First history page</Button>}
      {history?.nextCursor != null && <Button size="sm" variant="outline" onClick={() => setHistoryPage({ after: history.nextCursor!, highWater: history.highWater })}>More history</Button>}
      <Button size="sm" variant="outline" onClick={() => { setHistoryPage({ after: 0 }); setRefresh((value) => value + 1); }}>Refresh history</Button>
    </div>
    {historyError && <p role="alert" className="text-sm text-destructive">History read failed: {historyError}</p>}
    {runError && <p role="alert" className="text-sm text-destructive">Selected run read failed: {runError}</p>}
    {selected ? <>
      <p className="break-all text-xs text-muted-foreground">{selected.id} / snapshot {selected.snapshotChecksum}</p>
      {selected.id !== current.id && <p className="rounded-md border border-border bg-card p-3 text-sm">Historical run. Current instructions, approvals and completion are not inherited from this record.</p>}
      {selected.instruction && <p className="whitespace-pre-wrap text-sm">{selected.instruction}</p>}
      {selected.id === current.id && selected.resolvedAt && <RecipeContinuation key={`continue:${selected.id}`} jobId={jobId} run={selected} />}
      <RunSteps key={`steps:${selected.id}`} jobId={jobId} run={selected} initialStepId={(!requestedRunId || selected.id === requestedRunId) ? requestedStepId : undefined} initialAttemptId={(!requestedRunId || selected.id === requestedRunId) ? requestedAttemptId : undefined} />
    </> : null}
  </div>;
}

function RecipeContinuation({ jobId, run }: { jobId: string; run: RecipeRun }) {
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ key: string; instruction: string } | null>(null);
  const active = useRef(true);
  const reserved = useRef(false);
  useLayoutEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  async function resume() {
    if (!instruction.trim() || reserved.current) return;
    reserved.current = true;
    setBusy(true);
    setError(null);
    pending.current ??= { key: crypto.randomUUID(), instruction };
    try {
      const receipt = await resumeSelectedRecipeRun(jobId, run.id, pending.current.key, pending.current.instruction);
      if (!active.current) return;
      if (receipt.recipeRun.jobId !== jobId || receipt.recipeRun.continuationOfId !== run.id) throw new Error("Resume returned a different run identity.");
      useStore.setState((state) => ({
        recipeCurrentRuns: { ...state.recipeCurrentRuns, [jobId]: receipt.recipeRun },
        recipeRuns: { ...state.recipeRuns, [receipt.recipeRun.id]: receipt.recipeRun },
        recipeVersions: { ...state.recipeVersions, [jobId]: (state.recipeVersions[jobId] ?? 0) + 1 },
      }));
    } catch (cause: unknown) {
      if (active.current) setError(`${String(cause)}. The same Resume request is retained; no new run is assumed.`);
    } finally {
      reserved.current = false;
      if (active.current) setBusy(false);
    }
  }
  return <section className="space-y-2 rounded-lg border border-border bg-card p-3" aria-label="Explicit recipe continuation">
    <p className="text-sm font-medium">Continue this Job with a new pinned run</p>
    <p className="text-xs text-muted-foreground">Retains this recipe revision and current code. Prior attempts, approvals and completion remain historical. This is separate from messaging a running attempt.</p>
    <textarea aria-label="Recipe continuation instruction" value={instruction} disabled={busy || pending.current !== null} onChange={(event) => setInstruction(event.target.value)} className="min-h-20 w-full rounded-md border border-input bg-background p-2 text-sm" />
    <Button size="sm" disabled={busy || !instruction.trim()} onClick={() => void resume()}>{pending.current ? "Retry same Resume receipt" : "Resume Job with new run"}</Button>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
  </section>;
}

function RunSteps({ jobId, run, initialStepId, initialAttemptId }: { jobId: string; run: RecipeRun; initialStepId?: string; initialAttemptId?: string }) {
  const [after, setAfter] = useState("");
  const [selectedStepId, setSelectedStepId] = useState<string | null>(initialStepId ?? null);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const key = JSON.stringify([jobId, run.id, after]);
  const page = useStore((state) => state.recipeStepPages[key]);
  const version = useStore((state) => state.recipeVersions[jobId] ?? 0);
  const connection = useStore((state) => state.connectionStatus);
  const stepId = selectedStepId ?? page?.items[0]?.id;
  useEffect(() => {
    let disposed = false;
    setError(null);
    fetchRecipeRunSteps(jobId, run.id, after).then((value) => {
      if (!disposed) useStore.setState((state) => ({ recipeStepPages: { ...state.recipeStepPages, [key]: value } }));
    }).catch((cause: unknown) => { if (!disposed) setError(String(cause)); });
    return () => { disposed = true; };
  }, [jobId, run.id, after, key, version, connection, refresh]);
  return <div className="grid items-start gap-4 lg:grid-cols-[270px_minmax(0,1fr)]">
    <aside aria-label="Recipe steps" className="space-y-2">
      <p className="text-xs text-muted-foreground">{run.resolvedAt ? "Retained steps" : "Live step state"} / up to 30 per page</p>
      {page?.items.map((step) => <button key={step.id} type="button" aria-current={step.id === stepId ? "step" : undefined} onClick={() => setSelectedStepId(step.id)} className={cn("w-full rounded-lg border p-3 text-left text-sm", step.id === stepId ? "border-primary/40 bg-primary/10" : "border-border bg-card")}>
        <span className="block font-medium">{step.definition.title}</span><span className="block text-xs text-muted-foreground">{step.definition.runner.kind} / {step.state.replace(/_/g, " ")}</span><span className="block break-all text-xs text-muted-foreground">{step.stepPath}</span>
        <span className="mt-1 block text-xs text-muted-foreground">{step.definition.dependsOn?.length ? `Depends on ${step.definition.dependsOn.map((item) => item.stepId).join(", ")}` : "Entry step"} / {Object.keys(step.definition.inputs ?? {}).length} inputs</span>
      </button>)}
      {page?.items.length === 0 && <p className="text-sm text-muted-foreground">No step instances on this page.</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="flex flex-wrap gap-2">
        {after && <Button size="sm" variant="outline" onClick={() => { setAfter(""); setSelectedStepId(null); }}>First steps</Button>}
        {page?.nextCursor && <Button size="sm" variant="outline" onClick={() => { setAfter(page.nextCursor!); setSelectedStepId(null); }}>More steps</Button>}
        <Button size="sm" variant="outline" onClick={() => setRefresh((value) => value + 1)}>Refresh steps</Button>
      </div>
    </aside>
    {stepId && <RecipeStepInspector jobId={jobId} runId={run.id} stepId={stepId} initialAttemptId={stepId === initialStepId ? initialAttemptId : undefined} />}
  </div>;
}
