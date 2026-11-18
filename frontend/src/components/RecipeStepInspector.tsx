import { useEffect, useRef, useState, type ReactNode } from "react";
import { fetchRecipeAttempt, fetchRecipeStep, fetchRecipeConsumers } from "../api/client";
import type { RecipeAttemptInspection } from "../api/types";
import { useStore } from "../store";
import { Button } from "./ui/button";
import { Spinner } from "./ui/spinner";
import { ViewTabBar } from "./ViewTabBar";
import { RecipeAttemptMessage } from "./RecipeAttemptMessage";
import { RecipeGateDecision } from "./RecipeGateDecision";

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return <div className="grid min-w-0 gap-2 py-[11px] sm:grid-cols-[155px_minmax(0,1fr)] sm:gap-[18px]"><dt className="text-[13px] leading-relaxed text-muted-foreground">{label}</dt><dd className="min-w-0 break-words text-[13px] leading-relaxed">{children}</dd></div>;
}

function Value({ value }: { value: unknown }) {
  return <pre className="whitespace-pre-wrap break-words rounded-md bg-muted/40 p-3 text-xs">{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>;
}

function AttemptEvidence({ inspection }: { inspection: RecipeAttemptInspection }) {
  const { attempt, gate } = inspection;
  const result = attempt.result;
  const evidence = result?.evidence;
  if (!result || !evidence) return <p className="text-sm text-muted-foreground">No completed outcome has been recorded for this attempt.</p>;
  return <div className="space-y-4">
    <dl className="divide-y divide-border">
      <Fact label="Recorded outcome">{result.outcome}</Fact>
      <Fact label="Completed at">{result.completedAt}</Fact>
      {result.reason && <Fact label="Reason">{result.reason}</Fact>}
    </dl>
    {"uncertain" in evidence && evidence.uncertain && <p role="status" className="rounded-lg border border-warning/40 p-3 text-sm">Effects are uncertain. This result is not a passing check or permission to retry.</p>}
    {evidence.kind === "command" && <>
      <dl className="divide-y divide-border">
        <Fact label="Process started">{evidence.started == null ? "Unknown" : evidence.started ? "Yes" : "No"}</Fact>
        <Fact label="Exit status">{evidence.exitStatus ?? "Not recorded"}</Fact>
        <Fact label="Start code">{evidence.startCodeRef}</Fact>
        <Fact label="End code">{evidence.endCodeRef ?? "Not recorded"}</Fact>
      </dl>
      <h4 className="text-sm font-medium">Exact argv</h4><Value value={evidence.argv} />
      <h4 className="text-sm font-medium">Standard output</h4>
      <Value value={evidence.stdoutPreview ?? evidence.stdout ?? "No retained output"} />
      {evidence.stdoutArtifact && <dl><Fact label="Immutable stdout artifact">{evidence.stdoutArtifact.artifactId} / {evidence.stdoutArtifact.checksum} ({evidence.stdoutArtifact.sizeBytes} bytes{evidence.stdoutArtifact.complete === false ? ", incomplete" : ""})</Fact></dl>}
      <h4 className="text-sm font-medium">Standard error</h4>
      <Value value={evidence.stderrPreview ?? evidence.stderr ?? "No retained error output"} />
      {evidence.stderrArtifact && <dl><Fact label="Immutable stderr artifact">{evidence.stderrArtifact.artifactId} / {evidence.stderrArtifact.checksum} ({evidence.stderrArtifact.sizeBytes} bytes{evidence.stderrArtifact.complete === false ? ", incomplete" : ""})</Fact></dl>}
    </>}
    {evidence.kind === "agent" && <>
      <p className="text-xs text-muted-foreground">Agent-turn completion is not proof of test success or operator acceptance.</p>
      <dl className="divide-y divide-border">
        <Fact label="Observed models">{evidence.actualModels?.join(", ") || "Not reported"}</Fact>
        <Fact label="Actual native sessions">{evidence.sessionIds.join(", ") || "None recorded"}</Fact>
      </dl>
      <Value value={evidence.text ?? "No retained agent response"} />
    </>}
    {evidence.kind === "human" && <dl className="divide-y divide-border">
      <Fact label="Decision">{evidence.decision ?? "Not recorded"}</Fact>
      <Fact label="Approval">{evidence.approvalId ?? "Not bound"}</Fact>
      <Fact label="Exact input checksum">{evidence.inputChecksum}</Fact>
      {gate?.notes && <Fact label="Decision notes">{gate.notes}</Fact>}
    </dl>}
    {evidence.kind === "block" && <dl className="divide-y divide-border">
      <Fact label="Pinned block revision">{evidence.revisionId}</Fact>
      <Fact label="Child attempts">{evidence.childAttemptIds.join(", ") || "None"}</Fact>
    </dl>}
    <h4 className="text-sm font-medium">Declared output receipts</h4>
    {attempt.outputs.length === 0 && <p className="text-sm text-muted-foreground">No output receipts.</p>}
    {attempt.outputs.map((output) => <section key={output.id} className="space-y-2 rounded-lg border border-border p-3">
      <h5 className="text-sm font-medium">{output.name} <span className="text-muted-foreground">({output.type})</span></h5>
      <Value value={output.value} />
      <p className="break-all text-xs text-muted-foreground">Receipt {output.id} / {output.checksum}</p>
    </section>)}
  </div>;
}

export interface RecipeStepInspectorProps {
  jobId: string;
  runId: string;
  stepId: string;
  initialAttemptId?: string;
}

export function RecipeStepInspector(props: RecipeStepInspectorProps) {
  return <SelectedRecipeStepInspector key={JSON.stringify([props.jobId, props.runId, props.stepId])} {...props} />;
}

function ActualConsumers({ jobId, runId, stepId, inspection }: RecipeStepInspectorProps & { inspection: RecipeAttemptInspection }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const page = inspection.consumedBy;
  async function load(after = "") {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const value = await fetchRecipeConsumers(jobId, runId, stepId, inspection.attempt.id, after);
      if (!active.current) return;
      const key = JSON.stringify([jobId, runId, stepId, inspection.attempt.id]);
      useStore.setState((state) => {
        const current = state.recipeAttemptInspections[key];
        return current ? { recipeAttemptInspections: { ...state.recipeAttemptInspections, [key]: { ...current, consumedBy: value } } } : state;
      });
    } catch (cause: unknown) {
      if (active.current) setError(String(cause));
    } finally { if (active.current) setBusy(false); }
  }
  return <section className="space-y-2" aria-label="Actual consuming attempts">
    <h4 className="text-sm font-medium">Actual consuming attempts</h4>
    <p className="text-xs text-muted-foreground">These immutable admitted input receipts name this exact producing attempt. Admission does not prove consumer completion.</p>
    {page?.items.map((consumer) => <div key={`${consumer.attemptId}:${consumer.inputName}`} className="rounded-md border border-border p-3 text-xs">
      <a className="text-primary underline" href={`/jobs/${encodeURIComponent(jobId)}?${new URLSearchParams({ run: runId, step: consumer.stepInstanceId, attempt: consumer.attemptId })}`}>{consumer.stepPath} / attempt {consumer.attemptNumber}</a>
      <p>Input {consumer.inputName} / outcome {consumer.consumerOutcome ?? "Unfinished"} / producer outcome {consumer.producerOutcome ?? "Unrecorded"}</p>
      <p className="break-all text-muted-foreground">Receipt {consumer.receiptId ?? "No receipt recorded"} / {consumer.checksum ?? "No checksum recorded"}</p>
    </div>)}
    {page?.items.length === 0 && <p className="text-xs text-muted-foreground">No admitted input receipts consume this attempt's outputs.</p>}
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={busy} onClick={() => void load()}>First consumers</Button>
      {page?.nextCursor && <Button size="sm" variant="outline" disabled={busy} onClick={() => void load(page.nextCursor!)}>More consumers</Button>}</div>
  </section>;
}

function SelectedRecipeStepInspector({ jobId, runId, stepId, initialAttemptId }: RecipeStepInspectorProps) {
  const key = JSON.stringify([jobId, runId, stepId]);
  const inspection = useStore((state) => state.recipeStepInspections[key]);
  const version = useStore((state) => state.recipeVersions[jobId] ?? 0);
  const connection = useStore((state) => state.connectionStatus);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedAttemptId, setSelectedAttemptId] = useState<string | null>(initialAttemptId ?? null);
  const [tab, setTab] = useState("overview");
  const attemptId = selectedAttemptId ?? inspection?.step.currentAttemptId ?? null;
  const attemptKey = JSON.stringify([jobId, runId, stepId, attemptId]);
  const attempt = useStore((state) => state.recipeAttemptInspections[attemptKey]);
  const [attemptError, setAttemptError] = useState<string | null>(null);
  const [attemptLoading, setAttemptLoading] = useState(false);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setError(null);
    fetchRecipeStep(jobId, runId, stepId).then((value) => {
      if (disposed) return;
      useStore.setState((state) => {
        const previous = state.recipeStepInspections[key];
        if (previous && previous.step.version > value.step.version) return state;
        return { recipeStepInspections: { ...state.recipeStepInspections, [key]: value } };
      });
    }).catch((cause: unknown) => { if (!disposed) setError(String(cause)); })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [jobId, runId, stepId, key, version, connection, refresh]);

  useEffect(() => {
    if (!attemptId) return;
    let disposed = false;
    setAttemptLoading(true);
    setAttemptError(null);
    fetchRecipeAttempt(jobId, runId, stepId, attemptId).then((value) => {
      if (disposed) return;
      useStore.setState((state) => {
        const previous = state.recipeAttemptInspections[attemptKey]?.attempt;
        if (previous?.result && !value.attempt.result) return state;
        if (previous && previous.executions.length > value.attempt.executions.length) return state;
        return { recipeAttemptInspections: { ...state.recipeAttemptInspections, [attemptKey]: value } };
      });
    }).catch((cause: unknown) => { if (!disposed) setAttemptError(String(cause)); })
      .finally(() => { if (!disposed) setAttemptLoading(false); });
    return () => { disposed = true; };
  }, [jobId, runId, stepId, attemptId, attemptKey, version, connection, refresh]);

  if (!inspection && loading) return <div className="p-6" aria-label="Loading selected recipe step"><Spinner /></div>;
  if (!inspection) return <div role="alert" className="space-y-3 p-4"><p>{error ?? "The selected recipe step is unavailable."}</p><Button variant="outline" onClick={() => setRefresh((v) => v + 1)}>Retry read</Button></div>;
  const { step, attempts } = inspection;
  const definition = step.definition;
  const historicalAttempt = Boolean(attemptId && attemptId !== step.currentAttemptId);
  const runner = attempt?.attempt.runner ?? definition.runner;

  return <section aria-label="Recipe step inspector" className="min-w-0 space-y-4 rounded-lg border border-border bg-card px-[22px] py-[18px]">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div><p className="text-xs text-muted-foreground">{definition.runner.kind} step / {step.stepPath}</p><h3 className="text-lg font-semibold">{definition.title}</h3><p className="break-all text-xs text-muted-foreground">Run {runId}</p></div>
      <Button size="sm" variant="outline" disabled={loading || attemptLoading} onClick={() => setRefresh((v) => v + 1)}>Refresh read</Button>
    </header>
    {error && <p role="alert" className="text-sm text-destructive">{error}. Retained data may be stale.</p>}
    <dl className="divide-y divide-border">
      <Fact label="Step state">{step.state.replace(/_/g, " ")}</Fact>
      <Fact label="Readiness or outcome reason">{step.reason ?? "No additional reason recorded"}</Fact>
    </dl>
    <label className="block text-xs text-muted-foreground">Selected attempt
      <select aria-label="Selected attempt" className="mt-1 block w-full rounded-md border border-input bg-background p-2 text-sm text-foreground" value={attemptId ?? ""} onChange={(event) => setSelectedAttemptId(event.target.value || null)}>
        {!attemptId && <option value="">Not started</option>}
        {attempts.map((item) => <option key={item.id} value={item.id}>Attempt {item.number} - {item.outcome ?? "unfinished"}{item.id === step.currentAttemptId ? " (current)" : ""}</option>)}
      </select>
    </label>
    {historicalAttempt && <p className="rounded-md bg-muted/50 p-3 text-sm">Historical attempt. Its inputs, outputs and decisions do not authorize current work.</p>}
    <ViewTabBar activeTab={tab} onTabChange={setTab} variant="inline" mobileBehavior="visible" ariaLabel="Step inspector views" items={[
      { id: "overview", label: "Overview" }, { id: "inputs", label: "Inputs" }, { id: "output", label: "Output" }, { id: "history", label: "History" },
    ]} />
    {tab === "overview" && <div className="space-y-4">
      <h4 className="text-sm font-medium">Pinned instructions</h4><Value value={definition.instructions || "No instructions for this runner"} />
      <dl className="divide-y divide-border">
        <Fact label="Runner">{runner.kind}</Fact>
        <Fact label="Capabilities">{(attempt?.attempt.capabilities ?? definition.capabilities)?.join(", ") || "None"}</Fact>
        <Fact label="Workspace policy">{definition.workspace?.mode ?? "snapshot"}</Fact>
        <Fact label="Attempt bound">{definition.retry?.maxAttempts ?? 1} total</Fact>
        {attempt && <><Fact label="Actual workspace">{attempt.attempt.workspace.path}</Fact><Fact label="Pinned input code">{attempt.attempt.workspace.codeRef}</Fact></>}
        {runner.kind === "agent" && <><Fact label="Execution provider">{runner.provider ?? "Launch default"}</Fact><Fact label="Requested model">{runner.model ?? "Provider default (not a served-model claim)"}</Fact><Fact label="Conversation policy">{runner.session ?? "new"}</Fact></>}
        {runner.kind === "block" && <Fact label="Pinned block">{runner.revisionId}</Fact>}
        {runner.kind === "human" && <Fact label="Proposed action">{runner.proposedAction}</Fact>}
      </dl>
      {runner.kind === "command" && <><h4 className="text-sm font-medium">Command arguments</h4><Value value={runner.argv} /></>}
      <h4 className="text-sm font-medium">Explicit outcome routes</h4>
      <Value value={definition.routes} />
      <h4 className="text-sm font-medium">Required predecessor outcomes and inputs</h4>
      {(inspection.predecessors ?? []).map((predecessor) => <div key={predecessor.stepPath} className="rounded-md border border-border p-3 text-sm">
        <p>{predecessor.title}: {predecessor.state ?? "Not instantiated"}</p>
        <p className="text-xs text-muted-foreground">Required outcomes: {predecessor.requiredOutcomes?.join(", ") || "Input binding only"} / received as: {predecessor.inputNames?.join(", ") || "Dependency only"}</p>
        {predecessor.stepId && <a className="text-xs text-primary underline" href={`/jobs/${encodeURIComponent(jobId)}?${new URLSearchParams({ run: runId, step: predecessor.stepId, ...(predecessor.attemptId ? { attempt: predecessor.attemptId } : {}) })}`}>Inspect producer and evidence</a>}
      </div>)}
      {(inspection.predecessors ?? []).length === 0 && <p className="text-xs text-muted-foreground">No predecessor bindings.</p>}
      {attempt?.gate && <dl className="divide-y divide-border"><Fact label="Bound approval">{attempt.gate.binding.approvalId}</Fact><Fact label="Recorded decision">{attempt.gate.resolution ?? "Pending"}</Fact><Fact label="Exact decision inputs">{attempt.gate.binding.inputChecksum}</Fact></dl>}
    </div>}
    {tab === "inputs" && <div className="space-y-3">
      {!attemptId && <><p className="text-sm text-muted-foreground">Not started. These are published bindings, not execution receipts.</p><Value value={definition.inputs} /></>}
      {attempt && Object.entries(attempt.attempt.inputs).map(([name, input]) => <section key={name} className="space-y-2 rounded-lg border border-border p-3">
        <h4 className="text-sm font-medium">{name} ({input.type})</h4>
        <Value value={input.present ? input.value : "Missing input"} />
        {input.degradedReason && <p className="text-sm">{input.degradedReason}</p>}
        <dl className="divide-y divide-border"><Fact label="Producing attempt">{input.producingAttemptId ?? "No producing attempt"}</Fact><Fact label="Producer outcome">{input.producerOutcome ?? "Not applicable"}</Fact><Fact label="Receipt">{input.receiptId ?? "Pinned parameter or literal"}</Fact><Fact label="Checksum">{input.checksum ?? "Not recorded"}</Fact></dl>
      </section>)}
      {attempt && Object.keys(attempt.attempt.inputs).length === 0 && <p className="text-sm text-muted-foreground">No declared inputs.</p>}
    </div>}
    {tab === "output" && (attempt ? <AttemptEvidence inspection={attempt} /> : <p className="text-sm text-muted-foreground">No selected attempt evidence.</p>)}
    {tab === "output" && attempt && <ActualConsumers key={attempt.attempt.id} jobId={jobId} runId={runId} stepId={stepId} inspection={attempt} />}
    {tab === "output" && <div className="space-y-2">
      <h4 className="text-sm font-medium">Declared consumers</h4>
      <p className="text-xs text-muted-foreground">These are pinned input bindings. A consumer's state alone does not prove it received the selected historical attempt's output.</p>
      {(inspection.consumers ?? []).map((consumer) => <div key={consumer.stepPath} className="rounded-md border border-border p-3 text-sm">
        <p>{consumer.title}: {consumer.state ?? "Not instantiated"} / inputs: {consumer.inputNames?.join(", ")}</p>
        {consumer.stepId && <a className="text-xs text-primary underline" href={`/jobs/${encodeURIComponent(jobId)}?${new URLSearchParams({ run: runId, step: consumer.stepId, ...(consumer.attemptId ? { attempt: consumer.attemptId } : {}) })}`}>Inspect exact received inputs</a>}
      </div>)}
      {(inspection.consumers ?? []).length === 0 && <p className="text-xs text-muted-foreground">No sibling step declares an input from this step.</p>}
    </div>}
    {tab === "history" && <div className="space-y-3">
      <p className="text-sm text-muted-foreground">Several native sessions or turns may belong to one attempt. They are not additional recipe steps.</p>
      {attempt?.attempt.executions.map((execution) => <dl key={execution.executionToken} className="divide-y divide-border rounded-lg border border-border p-3">
        <Fact label="Actual native session">{execution.sessionId}</Fact><Fact label="Provider">{execution.provider}</Fact><Fact label="Recorded model">{execution.model ?? "Not reported"}</Fact><Fact label="Started at">{execution.startedAt}</Fact>
      </dl>)}
      {(!attempt || attempt.attempt.executions.length === 0) && <p className="text-sm text-muted-foreground">No agent sessions recorded. Command, human and block coordination do not create fictional sessions.</p>}
    </div>}
    {attemptLoading && <p role="status" className="text-xs text-muted-foreground">Refreshing selected attempt...</p>}
    {attempt && step.currentAttemptId === attempt.attempt.id && step.state === "needs_decision" && attempt.gate && !attempt.gate.resolution && <RecipeGateDecision key={attempt.attempt.id} jobId={jobId} runId={runId} step={step} inspection={attempt} />}
    {attemptId && <RecipeAttemptMessage jobId={jobId} runId={runId} stepId={stepId} attemptId={attemptId} />}
    {attemptError && <div role="alert" className="space-y-2"><p className="text-sm text-destructive">{attemptError}. Retained evidence may be stale.</p><Button size="sm" variant="outline" onClick={() => setRefresh((v) => v + 1)}>Retry evidence read</Button></div>}
  </section>;
}
