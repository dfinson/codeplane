import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError, fetchCurrentRecipeRun, fetchRecipeRunSteps, fetchReviewHistory, prepareReview, recordReview } from "../api/client";
import type { RecordReviewRequest } from "../api/types";
import { useStore } from "../store";
import { Button } from "./ui/button";

export function RecipeReviewPanel({ jobId }: { jobId: string }) {
  return <JobReviewPanel key={jobId} jobId={jobId} />;
}

function JobReviewPanel({ jobId }: { jobId: string }) {
  const run = useStore((state) => state.recipeCurrentRuns[jobId]);
  const runId = run?.id;
  const job = useStore((state) => state.jobs[jobId]);
  const snapshot = useStore((state) => state.reviewPreparations[jobId]);
  const [selectedSnapshot, setSelectedSnapshot] = useState<string | null>(null);
  const [stepAfter, setStepAfter] = useState("");
  const [selectedAttempts, setSelectedAttempts] = useState<string[]>([]);
  const [eventIds, setEventIds] = useState("");
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [historyCursor, setHistoryCursor] = useState<{ after: number; highWater?: number }>({ after: 0 });
  const reserved = useRef(false);
  const active = useRef(true);
  const pendingRequest = useRef<RecordReviewRequest | null>(null);
  const stepKey = JSON.stringify([jobId, run?.id, stepAfter]);
  const steps = useStore((state) => state.recipeStepPages[stepKey]);
  const historyKey = JSON.stringify([jobId, historyCursor]);
  const history = useStore((state) => state.reviewHistoryPages[historyKey]);
  const selected = snapshot?.snapshotArtifactId === selectedSnapshot ? snapshot : null;
  useLayoutEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  useEffect(() => {
    let disposed = false;
    fetchCurrentRecipeRun(jobId).then((value) => {
      if (!disposed) useStore.setState((state) => ({ recipeCurrentRuns: { ...state.recipeCurrentRuns, [jobId]: value.run } }));
    }).catch((cause: unknown) => { if (!disposed) setError(String(cause)); });
    return () => { disposed = true; };
  }, [jobId, refresh]);
  useEffect(() => {
    if (!runId) return;
    let disposed = false;
    fetchRecipeRunSteps(jobId, runId, stepAfter).then((value) => {
      if (!disposed) useStore.setState((state) => ({ recipeStepPages: { ...state.recipeStepPages, [stepKey]: value } }));
    }).catch((cause: unknown) => { if (!disposed) setError(String(cause)); });
    return () => { disposed = true; };
  }, [jobId, runId, stepAfter, stepKey, refresh]);
  useEffect(() => {
    let disposed = false;
    setHistoryError(null);
    fetchReviewHistory(jobId, historyCursor.after, historyCursor.highWater).then((value) => {
      if (!disposed) useStore.setState((state) => ({ reviewHistoryPages: { ...state.reviewHistoryPages, [historyKey]: value } }));
    }).catch((cause: unknown) => { if (!disposed) setHistoryError(String(cause)); });
    return () => { disposed = true; };
  }, [jobId, historyCursor, historyKey, refresh]);

  async function capture() {
    if (run === undefined || reserved.current) return;
    reserved.current = true;
    setBusy(true);
    setError(null);
    setSelectedSnapshot(null);
    try {
      const prepared = await prepareReview(jobId, {
        expectedRunId: run?.id ?? null,
        attemptIds: selectedAttempts,
        eventIds: eventIds.split(/[\s,]+/).filter(Boolean),
      });
      if (!active.current) return;
      useStore.setState((state) => ({ reviewPreparations: { ...state.reviewPreparations, [jobId]: prepared } }));
      setSelectedSnapshot(prepared.snapshotArtifactId);
      pendingRequest.current = null;
      setNotice("Selected code and evidence retained. Rationale has not been recorded.");
    } catch (cause: unknown) {
      if (active.current) setError(String(cause));
    } finally {
      reserved.current = false;
      if (active.current) setBusy(false);
    }
  }

  async function record() {
    if (!selected || !rationale.trim() || reserved.current) return;
    reserved.current = true;
    setBusy(true);
    setError(null);
    pendingRequest.current ??= {
      requestKey: crypto.randomUUID(), snapshotArtifactId: selected.snapshotArtifactId,
      snapshotChecksum: selected.snapshotChecksum, rationale,
    };
    try {
      const retained = await recordReview(jobId, pendingRequest.current);
      if (!active.current) return;
      setNotice(`Review rationale recorded as ${retained.eventId}. Job state and delivery are unchanged.`);
      pendingRequest.current = null;
      setRationale("");
      setSelectedSnapshot(null);
      setHistoryCursor({ after: 0 });
      setRefresh((value) => value + 1);
    } catch (cause: unknown) {
      if (!active.current) return;
      if (cause instanceof ApiError && cause.status === 409) {
        pendingRequest.current = null;
        setSelectedSnapshot(null);
        setNotice(null);
        setError(`${String(cause)} Retain fresh evidence before recording your preserved rationale.`);
      } else {
        setError(`${String(cause)}. The same request identity is retained for safe receipt retry.`);
      }
    } finally {
      reserved.current = false;
      if (active.current) setBusy(false);
    }
  }

  return <aside aria-label="Evidence for this decision" className="space-y-4 overflow-auto rounded-lg border border-border bg-card p-4">
    <h3 className="font-semibold">Evidence for this decision</h3>
    <p className="text-xs text-muted-foreground">Review records retain rationale only. They neither approve execution, merge code, create a PR nor update a tracker.</p>
    <p className="text-sm">Delivery: {job?.resolution ?? "Unresolved"}. Merge and PR remain separate actions.</p>
    {run ? <p className="text-xs">{run.snapshot.definition.name} / revision {run.snapshot.revision} / run {run.runNumber}: {run.state}</p> :
      <p className="text-xs text-muted-foreground">{run === undefined ? "Reading Job identity..." : "Legacy Job: no recipe identity is invented."}</p>}
    <fieldset disabled={busy || pendingRequest.current !== null} className="space-y-2">
      <legend className="text-sm font-medium">Select retained attempt evidence (up to 20)</legend>
      {steps?.items.map((step) => <label key={step.id} className="flex items-start gap-2 text-xs">
        <input type="checkbox" disabled={!step.currentAttemptId || (selectedAttempts.length >= 20 && !selectedAttempts.includes(step.currentAttemptId))}
          checked={Boolean(step.currentAttemptId && selectedAttempts.includes(step.currentAttemptId))}
          onChange={(event) => {
            const attemptId = step.currentAttemptId;
            if (!attemptId) return;
            setSelectedSnapshot(null);
            setSelectedAttempts((values) => event.target.checked ? [...values, attemptId] : values.filter((id) => id !== attemptId));
          }} />
        <span>{step.definition.title} / {step.definition.runner.kind} / {step.state}
          <a className="ml-1 text-primary underline" href={`/jobs/${encodeURIComponent(jobId)}?${new URLSearchParams({ run: run!.id, step: step.id, ...(step.currentAttemptId ? { attempt: step.currentAttemptId } : {}) })}`}>Open exact attempt</a>
        </span>
      </label>)}
      <p className="text-xs text-muted-foreground">{selectedAttempts.length} selected. Unselected outcomes and unsaved historical Story annotations are not included.</p>
      <div className="flex flex-wrap gap-2">
        {stepAfter && <Button variant="outline" size="sm" onClick={() => setStepAfter("")}>First steps</Button>}
        {steps?.nextCursor && <Button variant="outline" size="sm" onClick={() => setStepAfter(steps.nextCursor!)}>More steps</Button>}
      </div>
      <label className="block text-xs">Retained event IDs, optional (up to 100)
        <textarea aria-label="Review event IDs" value={eventIds} className="mt-1 min-h-14 w-full rounded-md border border-input bg-background p-2" onChange={(event) => { setEventIds(event.target.value); setSelectedSnapshot(null); }} />
      </label>
      <Button size="sm" variant="outline" disabled={run === undefined} onClick={() => void capture()}>Retain selected review evidence</Button>
    </fieldset>
    {selected && <div className="space-y-3">
      <p className="break-all text-xs">Code: {selected.evidence.code.commit}</p>
      <p className="text-xs text-muted-foreground">Retained event range ends at storage cursor {selected.evidence.eventHighWater}. Storage order is not cross-agent causality. Exact JSON and artifact copies follow normal artifact retention; this record retains their checksums and selected structured evidence.</p>
      {selected.evidence.attempts.map(({ step, attempt }) => <section key={attempt.id} className="space-y-1 rounded-md border border-border p-3 text-xs">
        <h4 className="font-medium">{step.definition.title} / attempt {attempt.number}</h4>
        <p>{attempt.runner.kind === "agent" ? "Generation / verification agent turn" : attempt.runner.kind === "command" ? "Recorded command" : "Recorded decision / block"}: {attempt.result?.outcome ?? "No result"}</p>
        <p>Recorded result: {attempt.result?.reason ?? "No additional outcome reason"}</p>
        {attempt.result?.evidence.kind === "command" && <><pre className="whitespace-pre-wrap break-words">{JSON.stringify(attempt.result.evidence.argv)}</pre><p>Exit: {attempt.result.evidence.exitStatus ?? "Unknown"}</p><p>{attempt.result.evidence.uncertain ? "Effects uncertain. Not a passing check." : "Retained command evidence."}</p></>}
        {attempt.result?.evidence.kind === "agent" && <p>Agent completion is not command-test success.</p>}
      </section>)}
      {selected.evidence.artifacts.map((artifact) => <a key={artifact.retained.artifactId} className="block break-all text-xs text-primary underline" target="_blank" rel="noreferrer" href={`/api/artifacts/${encodeURIComponent(artifact.retained.artifactId)}`}>Open the raw retained result {artifact.retained.artifactId} / {artifact.retained.checksum}</a>)}
      <a className="block text-xs text-primary underline" target="_blank" rel="noreferrer" href={`/api/artifacts/${encodeURIComponent(selected.snapshotArtifactId)}`}>Open exact retained events, inputs, outputs and result snapshot</a>
    </div>}
    <label className="block text-xs">Review rationale
      <textarea aria-label="Review rationale" placeholder="Record why you accept or reject this revision..." disabled={busy || pendingRequest.current !== null} value={rationale} onChange={(event) => setRationale(event.target.value)} className="mt-1 min-h-28 w-full rounded-md border border-input bg-background p-2 text-sm" />
    </label>
    <Button size="sm" disabled={busy || !selected || !rationale.trim()} onClick={() => void record()}>{pendingRequest.current ? "Retry same review receipt" : "Record review rationale"}</Button>
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    {notice && <p role="status" className="text-xs">{notice}</p>}
    <div className="space-y-2 border-t border-border pt-3">
      <h4 className="text-sm font-medium">Recorded rationale history</h4>
      {historyError && <p role="alert" className="text-xs text-destructive">{historyError}</p>}
      {history?.items.map((record) => <details key={record.eventId} className="rounded-md border border-border p-2 text-xs">
        <summary className="cursor-pointer">Review {record.recordedAt}</summary>
        <p className="mt-2 whitespace-pre-wrap">{record.rationale}</p>
        <p className="mt-2 break-all text-muted-foreground">{record.eventId} / code {record.evidence.code.commit}</p>
        <p className="text-muted-foreground">Snapshot {record.snapshotChecksum}. A historical rationale is not current approval or delivery.</p>
      </details>)}
      {history?.items.length === 0 && <p className="text-xs text-muted-foreground">No review rationale recorded in this range.</p>}
      <div className="flex flex-wrap gap-2">
        {history?.nextCursor != null && <Button size="sm" variant="outline" onClick={() => setHistoryCursor({ after: history.nextCursor!, highWater: history.highWater })}>More rationale</Button>}
        <Button size="sm" variant="outline" disabled={busy} onClick={() => { setHistoryCursor({ after: 0 }); setRefresh((value) => value + 1); }}>Refresh review reads</Button>
      </div>
    </div>
  </aside>;
}
