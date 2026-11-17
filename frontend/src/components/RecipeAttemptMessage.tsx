import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { fetchRecipeAttemptControl, sendRecipeAttemptMessage, stopRecipeAttempt, resumeRecipeAttempt, reconcileRecipeAttempt } from "../api/client";
import { useStore } from "../store";
import { Button } from "./ui/button";

export function RecipeAttemptMessage(props: { jobId: string; runId: string; stepId: string; attemptId: string }) {
  return <SelectedAttemptMessage key={JSON.stringify(props)} {...props} />;
}

function SelectedAttemptMessage({ jobId, runId, stepId, attemptId }: {
  jobId: string; runId: string; stepId: string; attemptId: string;
}) {
  const key = JSON.stringify([jobId, runId, stepId, attemptId]);
  const control = useStore((state) => state.recipeAttemptControls[key]);
  const version = useStore((state) => state.recipeVersions[jobId] ?? 0);
  const connection = useStore((state) => state.connectionStatus);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [stopConfirmed, setStopConfirmed] = useState(false);
  const [effectsQuiescent, setEffectsQuiescent] = useState(false);
  const [reconcileEvidence, setReconcileEvidence] = useState("");
  const [codeCommit, setCodeCommit] = useState("");
  const attempt = useStore((state) => state.recipeAttemptInspections[key]?.attempt);
  const reserved = useRef(false);
  const active = useRef(true);
  const draftVersion = useRef(0);
  useLayoutEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setError(null);
    fetchRecipeAttemptControl(jobId, runId, stepId, attemptId).then((value) => {
      if (disposed) return;
      if (value.jobId !== jobId || value.runId !== runId || value.stepInstanceId !== stepId || value.attemptId !== attemptId) {
        throw new Error("Control response belongs to a different attempt.");
      }
      useStore.setState((state) => ({ recipeAttemptControls: { ...state.recipeAttemptControls, [key]: value } }));
    }).catch((cause: unknown) => { if (!disposed) setError(String(cause)); })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [jobId, runId, stepId, attemptId, key, version, connection, refresh]);

  async function send() {
    if (!control?.canMessage || !control.executionToken || !draft.trim() || loading || error || uncertain || reserved.current) return;
    reserved.current = true;
    setSending(true);
    setNotice(null);
    const sentVersion = draftVersion.current;
    try {
      const receipt = await sendRecipeAttemptMessage(jobId, runId, stepId, attemptId, control.executionToken, draft);
      if (!active.current) return;
      if (receipt.status === "accepted") {
        setNotice(`Admitted to this execution. Execution is unconfirmed. Receipt ${receipt.messageEventId ?? "unavailable"}.`);
        if (draftVersion.current === sentVersion) setDraft("");
      } else {
        setNotice(`${receipt.status}: ${receipt.reason ?? "Instruction was not confirmed."}`);
        if (receipt.status === "uncertain") setUncertain(true);
      }
      setRefresh((value) => value + 1);
    } catch (cause: unknown) {
      if (active.current) {
        setUncertain(true);
        setNotice(`Delivery is uncertain: ${String(cause)}. Do not retry this instruction without inspecting the execution.`);
      }
    } finally {
      reserved.current = false;
      if (active.current) setSending(false);
    }
  }

  async function operate(action: "stop" | "resume" | "reconcile") {
    if (!control || reserved.current || loading || error) return;
    reserved.current = true;
    setSending(true);
    try {
      const result = action === "stop" && control.stop?.expectedToken
        ? await stopRecipeAttempt(jobId, runId, stepId, attemptId, control.stop.expectedToken)
        : action === "resume" && control.resume?.expectedVersion != null
          ? await resumeRecipeAttempt(jobId, runId, stepId, attemptId, control.resume.expectedVersion)
          : action === "reconcile" && control.reconcile?.expectedVersion != null && attempt && effectsQuiescent
            ? await reconcileRecipeAttempt(jobId, runId, stepId, attemptId, {
              expectedVersion: control.reconcile.expectedVersion, evidence: reconcileEvidence,
              effectsQuiescent: true, code: { workspace: attempt.workspace.path, commit: codeCommit },
            })
            : null;
      if (!active.current) return;
      if (result) setNotice(`${result.status}: ${result.reason}`);
      setRefresh((value) => value + 1);
      useStore.setState((state) => ({ recipeVersions: { ...state.recipeVersions, [jobId]: (state.recipeVersions[jobId] ?? 0) + 1 } }));
    } catch (cause: unknown) {
      if (active.current) setNotice(`Control outcome unconfirmed: ${String(cause)}. Refresh and inspect before another action.`);
    } finally {
      reserved.current = false;
      if (active.current) setSending(false);
    }
  }

  return <section aria-label="Selected attempt message" className="space-y-2 border-t border-border pt-3">
    <h4 className="text-sm font-medium">Message selected attempt</h4>
    <p className="text-xs text-muted-foreground">Attempt {attemptId}{control?.nativeSessionId ? ` / session ${control.nativeSessionId}` : ""}. This never resumes a Job or clears a gate.</p>
    {loading && <p role="status" className="text-xs text-muted-foreground">Checking the actual execution...</p>}
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    {control?.messageDisabledReason && <p className="text-xs text-muted-foreground">{control.messageDisabledReason}</p>}
    {control?.canMessage && <textarea aria-label="Instruction for selected attempt" className="min-h-24 w-full rounded-md border border-input bg-background p-2 text-sm" value={draft} onChange={(event) => {
      draftVersion.current += 1;
      setDraft(event.target.value);
    }} />}
    {notice && <p role="status" className="whitespace-pre-wrap text-sm">{notice}</p>}
    <div className="flex flex-wrap gap-2">
      {control?.canMessage && <Button size="sm" disabled={sending || loading || error !== null || uncertain || !draft.trim()} onClick={() => void send()}>Send to this attempt</Button>}
      <Button size="sm" variant="outline" disabled={loading || sending} onClick={() => setRefresh((value) => value + 1)}>Refresh execution identity</Button>
    </div>
    {uncertain && <p className="text-xs text-muted-foreground">Resending is disabled for this uncertain delivery. Refresh is read-only and does not retry the message.</p>}
    {control?.stop?.enabled && <div className="space-y-2">
      <label className="flex items-start gap-2 text-xs"><input type="checkbox" checked={stopConfirmed} onChange={(event) => setStopConfirmed(event.target.checked)} />Stop only this attempt. Tool effects may remain uncertain and need reconciliation.</label>
      <Button size="sm" variant="outline" disabled={!stopConfirmed || sending || loading} onClick={() => void operate("stop")}>Stop selected attempt</Button>
    </div>}
    {control?.resume?.enabled && <Button size="sm" variant="outline" disabled={sending || loading} onClick={() => void operate("resume")}>Resume unfinished attempt</Button>}
    {!control?.resume?.enabled && control?.resume?.disabledReason && <p className="text-xs text-muted-foreground">{control.resume.disabledReason}</p>}
    {control?.reconcile?.enabled && <details className="space-y-2 rounded-md border border-border p-3">
      <summary className="cursor-pointer text-sm">Reconcile stopped effects</summary>
      <p className="text-xs">This records stopped-effect evidence and releases ownership. It does not mark checks successful, retry work, or copy approval.</p>
      <label className="block text-xs">Evidence<textarea aria-label="Reconciliation evidence" className="mt-1 w-full rounded-md border bg-background p-2" value={reconcileEvidence} onChange={(event) => setReconcileEvidence(event.target.value)} /></label>
      <label className="block text-xs">Verified current code commit<input aria-label="Reconciliation code commit" className="mt-1 w-full rounded-md border bg-background p-2" value={codeCommit} onChange={(event) => setCodeCommit(event.target.value)} /></label>
      <label className="flex items-start gap-2 text-xs"><input type="checkbox" checked={effectsQuiescent} onChange={(event) => setEffectsQuiescent(event.target.checked)} />I inspected this attempt's local and external effects, confirmed no owned execution is still acting, and reconciled the current code.</label>
      <Button size="sm" disabled={sending || loading || !effectsQuiescent || !reconcileEvidence.trim() || !/^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$/.test(codeCommit)} onClick={() => void operate("reconcile")}>Record stopped-effect reconciliation</Button>
    </details>}
  </section>;
}
