import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import {
  createManualTaskLink,
  fetchCredentials,
  fetchProjects,
  fetchTrackerLinks,
  refreshTrackerLink,
  reconcileTrackerOperation,
  retryTrackerOperation,
} from "../api/client";
import type { Credential } from "../api/client";
import type {
  ProjectResponse,
  TrackerLinkResponse,
  TrackerSummaryResponse,
  TrackerOperationResponse,
  ReconcileTrackerOperationRequest,
} from "../api/types";
import {
  useStore,
  selectTrackerOperations,
  selectTrackerOperationsForProject,
  selectTrackerOperationLoad,
} from "../store";
import { Button } from "./ui/button";
import { Spinner } from "./ui/spinner";

interface ProjectLinks {
  project: ProjectResponse;
  links: TrackerLinkResponse[];
}

export function TrackerSyncPanel() {
  const operations = useStore(selectTrackerOperations);
  const loadOperations = useStore((state) => state.loadTrackerOperations);
  const [groups, setGroups] = useState<ProjectLinks[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState<string | null>(null);
  const [assignment, setAssignment] = useState<{
    projectId: string;
    trackerLinkId: string;
    ticketRef: string;
    repoPath: string;
  } | null>(null);
  const [assignmentPrompt, setAssignmentPrompt] = useState("");
  const [writeCompletionToTracker, setWriteCompletionToTracker] = useState(false);
  const [assigning, setAssigning] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [projectsResponse, credentialsResponse] = await Promise.all([
        fetchProjects(),
        fetchCredentials(),
      ]);
      for (const project of projectsResponse.items) void loadOperations(project.id);
      const links = await Promise.all(
        projectsResponse.items.map(async (project) => ({
          project,
          links: (await fetchTrackerLinks(project.id)).trackerLinks ?? [],
        })),
      );
      setGroups(links);
      setCredentials(credentialsResponse.credentials);
    } catch (error) {
      toast.error(String(error));
    } finally {
      setLoading(false);
    }
  }, [loadOperations]);

  useEffect(() => {
    void load();
  }, [load]);

  const credentialById = useMemo(
    () => new Map(credentials.map((credential) => [credential.id, credential])),
    [credentials],
  );

  const updateSummary = (
    projectId: string,
    linkId: string,
    summary: TrackerSummaryResponse,
  ) => {
    setGroups((current) =>
      current.map((group) =>
        group.project.id !== projectId
          ? group
          : {
              ...group,
              links: group.links.map((link) =>
                link.id === linkId ? { ...link, summary } : link,
              ),
            },
      ),
    );
  };

  const handleRefresh = async (projectId: string, link: TrackerLinkResponse) => {
    setRefreshing(link.id);
    try {
      const summary = await refreshTrackerLink(projectId, link.id);
      updateSummary(projectId, link.id, summary);
      toast.success(`Refreshed ${link.externalRef}.`);
    } catch (error) {
      toast.error(String(error));
      await load();
    } finally {
      setRefreshing(null);
    }
  };

  const historicalProjectRefs = [...new Set(Object.values(operations)
    .map((operation) => operation.projectRef))]
    .filter((projectRef) => !groups.some((group) => group.project.id === projectRef));

  const handleAssign = async () => {
    if (!assignment || !assignment.repoPath || !assignmentPrompt.trim()) return;
    setAssigning(true);
    try {
      await createManualTaskLink(assignment.projectId, {
        repoPath: assignment.repoPath,
        trackerLinkId: assignment.trackerLinkId,
        trackerTicketRef: assignment.ticketRef,
        promptOverride: assignmentPrompt.trim(),
        outputRoutes: writeCompletionToTracker ? ["tracker_write"] : [],
      });
      toast.success(`Assigned ${assignment.ticketRef} as a task.`);
      setAssignment(null);
      setAssignmentPrompt("");
      setWriteCompletionToTracker(false);
    } catch (error) {
      toast.error(String(error));
    } finally {
      setAssigning(false);
    }
  };

  return (
    <div className="rounded-lg border border-border bg-card p-5 space-y-4">
      <div>
        <p className="text-sm font-semibold">Synced tracker state</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          Ticket state is fetched by polling or with a manual refresh. CodePlane does not expose tracker webhooks.
        </p>
      </div>

      {loading ? (
        <div className="flex justify-center py-4"><Spinner /></div>
      ) : (
        <div className="space-y-4">
          {groups.every((group) => group.links.length === 0) && (
            <p className="text-xs text-muted-foreground">No tracker links attached yet.</p>
          )}
          {groups.map(({ project, links }) => (
            <section key={project.id} className="space-y-2">
              <h4 className="text-sm font-medium">{project.name}</h4>
              {links.map((link) => {
                const credential = credentialById.get(link.credentialId);
                const summary = link.summary;
                return (
                  <div key={link.id} className="rounded-md border border-border p-3 space-y-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-xs font-medium">
                          {credential?.label ?? "Unknown credential"}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          <span>{credential?.provider.replace("_", " ") ?? "unknown"}</span>
                          {" · "}
                          <span>{link.externalRef}</span>
                        </p>
                        <p className="text-[11px] text-muted-foreground">
                          {summary?.lastSyncedAt
                            ? `Last synced ${new Date(summary.lastSyncedAt).toLocaleString()}`
                            : "Never synced"}
                        </p>
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        aria-label={`Refresh ${link.externalRef}`}
                        disabled={refreshing === link.id}
                        onClick={() => void handleRefresh(project.id, link)}
                      >
                        <RefreshCw className={`h-3.5 w-3.5 ${refreshing === link.id ? "animate-spin" : ""}`} />
                        Refresh
                      </Button>
                    </div>

                    {summary?.lastError && (
                      <p role="alert" className="text-xs text-red-400">{summary.lastError}</p>
                    )}

                    {!summary || !summary.tickets?.length ? (
                      <p className="text-xs text-muted-foreground">No tickets fetched.</p>
                    ) : (
                      <div className="space-y-1">
                        {summary.tickets.map((ticket) => {
                          const content = (
                            <>
                              <span className="font-medium">{ticket.title}</span>
                              <span className="text-muted-foreground">
                                {credential?.provider === "github" && "Project Status: "}
                                {ticket.status}
                              </span>
                            </>
                          );
                          return (
                            <div key={ticket.id} className="rounded px-2 py-1 text-xs hover:bg-accent/40">
                              <div className="flex items-center justify-between gap-3">
                                {ticket.url ? (
                                  <a href={ticket.url} target="_blank" rel="noreferrer" className="flex flex-1 justify-between gap-3 hover:underline">
                                    {content}
                                  </a>
                                ) : (
                                  <span className="flex flex-1 justify-between gap-3">{content}</span>
                                )}
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  aria-label={`Assign task for ${ticket.id}`}
                                  onClick={() => {
                                    setAssignment({
                                      projectId: project.id,
                                      trackerLinkId: link.id,
                                      ticketRef: ticket.id,
                                      repoPath: "",
                                    });
                                    setAssignmentPrompt(ticket.title);
                                  }}
                                >
                                  <Link2 className="h-3.5 w-3.5" />
                                  Assign task
                                </Button>
                              </div>
                              {assignment?.trackerLinkId === link.id && assignment.ticketRef === ticket.id && (
                                <div className="mt-2 space-y-2 rounded border border-border bg-background p-2">
                                  <label className="block">
                                    <span className="text-[11px] text-muted-foreground">Repository</span>
                                    <select
                                      aria-label="Task repository"
                                      className="mt-1 w-full rounded border border-border bg-background px-2 py-1"
                                      value={assignment.repoPath}
                                      onChange={(event) => setAssignment({ ...assignment, repoPath: event.target.value })}
                                    >
                                      <option value="">Select repository…</option>
                                      {project.repoPaths.map((repoPath) => <option key={repoPath} value={repoPath}>{repoPath}</option>)}
                                    </select>
                                  </label>
                                  <label className="flex items-center gap-2 text-[11px] text-muted-foreground">
                                    <input
                                      type="checkbox"
                                      checked={writeCompletionToTracker}
                                      onChange={(event) => setWriteCompletionToTracker(event.target.checked)}
                                    />
                                    Request an approved tracker comment when this task completes
                                  </label>
                                  <label className="block">
                                    <span className="text-[11px] text-muted-foreground">Task prompt</span>
                                    <textarea
                                      aria-label="Task prompt"
                                      className="mt-1 min-h-16 w-full rounded border border-border bg-background px-2 py-1"
                                      value={assignmentPrompt}
                                      onChange={(event) => setAssignmentPrompt(event.target.value)}
                                    />
                                  </label>
                                  <div className="flex justify-end gap-2">
                                    <Button variant="ghost" size="sm" onClick={() => setAssignment(null)}>Cancel</Button>
                                    <Button size="sm" disabled={assigning || !assignment.repoPath || !assignmentPrompt.trim()} onClick={() => void handleAssign()}>
                                      {assigning ? "Assigning…" : "Create TaskLink"}
                                    </Button>
                                  </div>
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
              <ProjectOperations projectRef={project.id} />
            </section>
          ))}
          {historicalProjectRefs.map((projectRef) => (
            <section key={projectRef} className="space-y-2">
              <h4 className="text-sm font-medium">Project reference: {projectRef} (unavailable)</h4>
              <ProjectOperations projectRef={projectRef} />
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

const operationStateLabels: Record<TrackerOperationResponse["state"], string> = {
  awaiting_approval: "Pending approval",
  approved: "Approved: not yet sent",
  dispatching: "Dispatch in progress: delivery not confirmed",
  applied: "Applied",
  rejected: "Rejected: not sent",
  failed: "Failed: not applied",
  uncertain: "Delivery uncertain",
  not_applied: "Confirmed not applied",
};

function ProjectOperations({ projectRef }: { projectRef: string }) {
  const selector = useMemo(() => selectTrackerOperationsForProject(projectRef), [projectRef]);
  const operations = useStore(selector);
  const load = useStore(selectTrackerOperationLoad(projectRef));
  const reload = useStore((state) => state.loadTrackerOperations);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h5 className="text-sm font-medium">Tracker operations</h5>
        <Button
          variant="outline"
          size="sm"
          aria-label={`Reload operations for ${projectRef}`}
          disabled={load?.loading}
          onClick={() => void reload(projectRef)}
        >
          Reload operations
        </Button>
      </div>
      {load?.loading && <p role="status" className="text-xs text-muted-foreground">Loading persisted operations…</p>}
      {load?.error && (
        <p role="alert" className="text-xs">
          Could not refresh operations: {load.error}. Previously loaded evidence is retained. Use Reload operations to try again.
        </p>
      )}
      {!load?.loading && !load?.error && load?.loaded && operations.length === 0 && (
        <p className="text-xs text-muted-foreground">No tracker operations recorded.</p>
      )}
      <div className="space-y-2">
        {operations.map((operation) => <TrackerOperation key={operation.id} operation={operation} />)}
      </div>
    </div>
  );
}

function TrackerOperation({ operation }: { operation: TrackerOperationResponse }) {
  const [evidence, setEvidence] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const statusRef = useRef<HTMLParagraphElement>(null);
  const rowRef = useRef<HTMLElement>(null);
  const previousState = useRef(operation.state);
  const inFlight = useRef(false);
  const retryRequestId = useRef<string | null>(null);
  const hydrate = useStore((state) => state.hydrateTrackerOperations);
  const reload = useStore((state) => state.loadTrackerOperations);
  const stateLabel = operationStateLabels[operation.state];
  const hasVersion = typeof operation.version === "number" && Number.isSafeInteger(operation.version) && operation.version >= 0;
  const targetLabel = operation.targetKind === "github_issue_state"
    ? "Issue state"
    : operation.targetKind === "jira_workflow_transition"
      ? "Jira workflow transition"
      : operation.targetKind === "azure_devops_work_item_state"
        ? "Azure DevOps work item state"
        : "Comment";
  const statusId = `operation-${operation.id}-status`;
  const evidenceId = `operation-${operation.id}-evidence`;
  const errorId = `operation-${operation.id}-error`;

  useEffect(() => {
    if (previousState.current === "uncertain" && operation.state !== "uncertain" &&
        rowRef.current?.contains(document.activeElement)) {
      statusRef.current?.focus();
    }
    previousState.current = operation.state;
  }, [operation.state]);

  const reconcile = async (resolution: ReconcileTrackerOperationRequest["resolution"]) => {
    if (inFlight.current || !evidence.trim() || operation.state !== "uncertain" ||
        !hasVersion || typeof operation.version !== "number") return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await reconcileTrackerOperation(operation.projectRef, operation.id, {
        expectedVersion: operation.version,
        resolution,
        evidence,
      });
      hydrate([updated]);
      setNotice("Reconciliation recorded. The current delivery state is shown above.");
    } catch (reason) {
      setError(`${reason instanceof Error ? reason.message : String(reason)}. Your evidence is retained. Review the refreshed operation before trying again.`);
      await reload(operation.projectRef);
    } finally {
      inFlight.current = false;
      setBusy(false);
      statusRef.current?.focus();
    }
  };

  const retry = async () => {
    if (inFlight.current || (operation.state !== "failed" && operation.state !== "not_applied") ||
        !hasVersion || typeof operation.version !== "number") return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      // Reuse the caller identity after a lost response; never create another
      // logical retry merely because this POST's acknowledgement was lost.
      retryRequestId.current ??= crypto.randomUUID();
      const created = await retryTrackerOperation(operation.projectRef, operation.id, {
        expectedVersion: operation.version,
        requestId: retryRequestId.current,
      });
      hydrate([created]);
      setNotice(`Retry operation ${created.id} recorded. Fresh explicit approval is required; this is not confirmation of delivery.`);
    } catch (reason) {
      setError(`${reason instanceof Error ? reason.message : String(reason)}. No delivery is assumed. Review the refreshed operations before trying again.`);
      await reload(operation.projectRef);
    } finally {
      inFlight.current = false;
      setBusy(false);
      statusRef.current?.focus();
    }
  };

  return (
    <article ref={rowRef} aria-labelledby={`operation-${operation.id}-title`} className="rounded-md border border-border p-3 space-y-3">
      <h6 id={`operation-${operation.id}-title`} className="text-xs font-semibold break-words">
        {operation.ticketRef}: {targetLabel}
        {operation.action === "transition" && `: ${operation.normalizedValue ?? operation.value}`}
      </h6>
      <p id={statusId} ref={statusRef} role="status" tabIndex={-1} aria-live="polite"
        className="text-xs font-medium rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">
        {stateLabel}
        {notice && <span className="block mt-1 font-normal">{notice}</span>}
      </p>
      <dl className="text-xs space-y-1 break-words">
        <div><dt className="inline text-muted-foreground">Operation: </dt><dd className="inline">{operation.id} (version {operation.version ?? "unavailable"})</dd></div>
        <div><dt className="inline text-muted-foreground">Provider: </dt><dd className="inline">{operation.provider}</dd></div>
        <div><dt className="inline text-muted-foreground">Target: </dt><dd className="inline">{operation.baseUrl} / {operation.externalRef} / {operation.ticketRef}</dd></div>
        <div><dt className="inline text-muted-foreground">TrackerLink: </dt><dd className="inline">{operation.trackerLinkRef}{!operation.trackerLinkId && " (detached or deleted)"}</dd></div>
        <div><dt className="inline text-muted-foreground">Job: </dt><dd className="inline">
          {operation.jobId
            ? <a href={`/jobs/${encodeURIComponent(operation.jobId)}`} className="underline rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">{operation.jobRef}</a>
            : `${operation.jobRef} (deleted)`}
        </dd></div>
        {operation.taskLinkRef && <div><dt className="inline text-muted-foreground">Task: </dt><dd className="inline">{operation.taskLinkRef}{!operation.taskLinkId && " (deleted)"}</dd></div>}
        {!operation.projectId && <div><dt className="inline text-muted-foreground">Project: </dt><dd className="inline">{operation.projectRef} (deleted)</dd></div>}
        <div><dt className="inline text-muted-foreground">Approval: </dt><dd className="inline">{operation.approvalRef ?? "Not yet provisioned"}{operation.approvalRef && !operation.approvalId && " (deleted)"}</dd></div>
        {operation.retryOfId && <div><dt className="inline text-muted-foreground">Retry of: </dt><dd className="inline">{operation.retryOfId}</dd></div>}
        <div><dt className="inline text-muted-foreground">Updated: </dt><dd className="inline"><time dateTime={operation.updatedAt}>{new Date(operation.updatedAt).toLocaleString()}</time></dd></div>
      </dl>
      {operation.targetKind === "github_issue_state" && (
        <p className="text-xs text-muted-foreground">Issue state is separate from the synced GitHub Project Status. This operation does not change Project Status.</p>
      )}
      <details>
        <summary className="text-xs cursor-pointer rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">
          {operation.action === "comment" ? "Exact comment body" : "Original requested transition"}
        </summary>
        <pre className="mt-1 whitespace-pre-wrap break-words text-xs font-sans">{operation.value}</pre>
      </details>
      {operation.state === "awaiting_approval" && (
        <p className="text-xs text-muted-foreground">Waiting for explicit approval. Review the approval in the related Job. Approval is not proof of delivery.</p>
      )}
      {operation.errorCode && <p className="text-xs">Reason code: {operation.errorCode}</p>}
      {operation.errorDetail && <p className="text-xs whitespace-pre-wrap break-words">Reason: {operation.errorDetail}</p>}
      {operation.providerReceipt && (
        <details open={operation.state === "applied"}>
          <summary className="text-xs cursor-pointer">Provider receipt</summary>
          <dl className="mt-1 text-xs break-words space-y-1">
            {operation.providerReceipt.id && <div><dt className="inline text-muted-foreground">Receipt ID: </dt><dd className="inline">{operation.providerReceipt.id}</dd></div>}
            {operation.providerReceipt.url && <div><dt className="inline text-muted-foreground">Provider URL: </dt><dd className="inline">{operation.providerReceipt.url}</dd></div>}
            {operation.providerReceipt.httpStatus != null && <div><dt className="inline text-muted-foreground">HTTP response status: </dt><dd className="inline">{operation.providerReceipt.httpStatus}</dd></div>}
          </dl>
        </details>
      )}
      {operation.state === "applied" && !operation.providerReceipt && (
        <p className="text-xs text-muted-foreground">
          {operation.reconciliation ? "Applied through recorded reconciliation evidence below." : "Applied outcome recorded; no provider receipt was returned."}
        </p>
      )}
      {operation.reconciliation && (
        <details open>
          <summary className="text-xs cursor-pointer">Recorded reconciliation</summary>
          <dl className="mt-1 text-xs space-y-1">
            <div><dt className="inline text-muted-foreground">Resolution: </dt><dd className="inline">{operation.reconciliation.resolution === "applied" ? "Applied" : "Not applied"}</dd></div>
            <div><dt className="inline text-muted-foreground">Recorded at: </dt><dd className="inline"><time dateTime={operation.reconciliation.recordedAt}>{new Date(operation.reconciliation.recordedAt).toLocaleString()}</time></dd></div>
            <div><dt className="inline text-muted-foreground">Attribution: </dt><dd className="inline">Shared operator context (no individual identity recorded)</dd></div>
            <div><dt className="text-muted-foreground">Evidence:</dt><dd className="whitespace-pre-wrap break-words">{operation.reconciliation.evidence}</dd></div>
          </dl>
        </details>
      )}
      {operation.approvalSnapshot && (
        <details>
          <summary className="text-xs cursor-pointer">Retained approval evidence</summary>
          <pre className="mt-1 whitespace-pre-wrap break-words text-xs">{JSON.stringify(operation.approvalSnapshot, null, 2)}</pre>
        </details>
      )}
      {operation.state === "uncertain" && (
        <fieldset disabled={busy || !hasVersion} className="space-y-2">
          <legend className="text-xs font-medium">Reconcile uncertain delivery</legend>
          <p id={`${evidenceId}-help`} className="text-xs text-muted-foreground">
            Check the provider's comment, history, or receipt for this exact request. Record a URL, receipt ID, or a verification note that establishes delivery or non-delivery. A missing response alone is not proof of non-delivery. If you cannot establish either outcome, leave this operation uncertain. No retry is available until non-delivery is confirmed.
          </p>
          <label htmlFor={evidenceId} className="block text-xs font-medium">Reconciliation evidence</label>
          <textarea id={evidenceId} value={evidence} onChange={(event) => setEvidence(event.target.value)}
            aria-describedby={`${evidenceId}-help${error ? ` ${errorId}` : ""}`}
            aria-invalid={Boolean(error)} required maxLength={4000}
            className="w-full min-h-20 rounded border border-border bg-background px-2 py-1 text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring" />
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" disabled={busy || !hasVersion || !evidence.trim()} onClick={() => void reconcile("applied")}>Record applied</Button>
            <Button type="button" size="sm" variant="outline" disabled={busy || !hasVersion || !evidence.trim()} onClick={() => void reconcile("not_applied")}>Record not applied</Button>
          </div>
        </fieldset>
      )}
      {(operation.state === "failed" || operation.state === "not_applied") && (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">Retry creates a linked operation with the unchanged target and exact content. It requires fresh explicit approval before dispatch.</p>
          <Button type="button" size="sm" variant="outline" disabled={busy || !hasVersion} onClick={() => void retry()}>Request retry with fresh approval</Button>
        </div>
      )}
      {busy && <p role="status" className="text-xs">Recording decision…</p>}
      {!hasVersion && <p role="alert" className="text-xs">Operation version is unavailable. Reload operations before making a decision.</p>}
      {error && <p id={errorId} role="alert" className="text-xs">{error}</p>}
    </article>
  );
}
