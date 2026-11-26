import { useLayoutEffect, useRef, useState } from "react";
import { decideRecipeAttempt } from "../api/client";
import type { RecipeAttemptInspection, RecipeStepInspection, RecipeGateDecisionRequest } from "../api/types";
import { useStore } from "../store";
import { Button } from "./ui/button";

export function RecipeGateDecision({ jobId, runId, step, inspection }: {
  jobId: string; runId: string; step: RecipeStepInspection["step"]; inspection: RecipeAttemptInspection;
}) {
  const [outputs, setOutputs] = useState(Object.keys(step.definition.outputs ?? {}).length ? "" : "{}");
  const [notes, setNotes] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const active = useRef(true);
  const reserved = useRef(false);
  useLayoutEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const gate = inspection.gate;
  const runner = inspection.attempt.runner;
  if (!gate || runner.kind !== "human") return null;
  async function decide(decision: "approved" | "rejected") {
    if (!gate || !confirmed || reserved.current) return;
    reserved.current = true;
    setBusy(true);
    try {
      const values: unknown = JSON.parse(outputs);
      if (!values || typeof values !== "object" || Array.isArray(values)) throw new Error("Decision outputs must be a JSON object.");
      const body: RecipeGateDecisionRequest = {
        expectedVersion: step.version, approvalId: gate.binding.approvalId, inputChecksum: gate.binding.inputChecksum,
        decision, outputs: values as RecipeGateDecisionRequest["outputs"], notes: notes || null,
      };
      const receipt = await decideRecipeAttempt(jobId, runId, step.id, inspection.attempt.id, body);
      if (!active.current) return;
      setNotice(`${receipt.status}: ${receipt.reason}`);
      setConfirmed(false);
      useStore.setState((state) => ({ recipeVersions: { ...state.recipeVersions, [jobId]: (state.recipeVersions[jobId] ?? 0) + 1 } }));
    } catch (error: unknown) {
      if (active.current) setNotice(`${String(error)}. Inputs are retained. Refresh the exact proposal before another decision.`);
    } finally {
      reserved.current = false;
      if (active.current) setBusy(false);
    }
  }
  return <section aria-label="Exact human gate decision" className="space-y-3 rounded-lg border border-primary/30 p-3">
    <h4 className="font-medium">Decision authority: operator</h4>
    <p className="text-sm">{gate.binding.proposedAction}</p>
    <pre className="whitespace-pre-wrap break-words text-xs">{JSON.stringify(gate.binding.nextSteps, null, 2)}</pre>
    <p className="break-all text-xs text-muted-foreground">Proposal inputs {gate.binding.inputChecksum} / approval {gate.binding.approvalId}</p>
    <p className="text-xs">Required output names and types: {Object.entries(step.definition.outputs ?? {}).map(([name, output]) => `${name}: ${output.type}`).join(", ") || "none"}</p>
    <label className="block text-xs">Decision outputs (JSON)<textarea aria-label="Decision outputs" value={outputs} disabled={busy} onChange={(event) => setOutputs(event.target.value)} className="mt-1 w-full rounded-md border bg-background p-2" /></label>
    <label className="block text-xs">Decision notes<textarea aria-label="Decision notes" value={notes} disabled={busy} onChange={(event) => setNotes(event.target.value)} className="mt-1 w-full rounded-md border bg-background p-2" /></label>
    <label className="flex items-start gap-2 text-xs"><input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} />I reviewed this exact input snapshot, proposed action and next steps.</label>
    <div className="flex gap-2">
      {runner.decisions?.includes("approved") && <Button disabled={!confirmed || busy} size="sm" onClick={() => void decide("approved")}>Approve exact proposal</Button>}
      {runner.decisions?.includes("rejected") && <Button disabled={!confirmed || busy} size="sm" variant="outline" onClick={() => void decide("rejected")}>Reject exact proposal</Button>}
    </div>
    <p className="text-xs text-muted-foreground">Rejection is not a retry or blanket grant. This decision applies only to the shown proposal.</p>
    {notice && <p role="status" className="text-sm">{notice}</p>}
  </section>;
}
