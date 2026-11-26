import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, LayoutGrid, Plus } from "lucide-react";
import { fetchProjectBoard, fetchProjectBoardApprovals, fetchProjectBoardDeliveries, fetchProjectBoardPage } from "../api/client";
import type { ProjectBoard as Board, ProjectBoardColumn, ProjectBoardItem, ProjectBoardPage } from "../api/types";
import { useStore } from "../store";
import { Button } from "./ui/button";
import { KanbanSkeleton } from "./KanbanSkeleton";
import { TaskLinkInspector } from "./TaskLinkInspector";
import { TrackerOperation } from "./TrackerSyncPanel";

const columns: Array<{ id: ProjectBoardColumn; title: string }> = [
  { id: "ready_blocked", title: "Ready / blocked" },
  { id: "in_progress", title: "In Progress" },
  { id: "needs_decision", title: "Needs a decision" },
  { id: "failed", title: "Failed" },
];

export function RepoBoard() {
  const { projectId, taskLinkId } = useParams<{ projectId: string; taskLinkId?: string }>();
  return projectId ? <ProjectBoard key={projectId} projectId={projectId} taskId={taskLinkId} /> :
    <p role="alert">No Project was selected.</p>;
}

function ProjectBoard({ projectId, taskId }: { projectId: string; taskId?: string }) {
  const board = useStore((state) => state.projectBoards[projectId]);
  const version = useStore((state) => state.projectBoardVersion);
  const connection = useStore((state) => state.connectionStatus);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    const timer = setTimeout(() => {
      fetchProjectBoard(projectId, controller.signal).then((value) => {
        if (controller.signal.aborted) return;
        if (value.project.id !== projectId) throw new Error("Board response belongs to a different Project.");
        useStore.setState((state) => ({ projectBoards: { ...state.projectBoards, [projectId]: value } }));
      }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(String(cause)); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 150);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [projectId, version, connection, refresh]);
  const params = new URLSearchParams({ projectId });
  if (board?.project.repoPaths.length === 1) params.set("repo", board.project.repoPaths[0]!);
  if (!board && loading) return <KanbanSkeleton />;
  return <div className="space-y-4">
    <header className="flex flex-wrap items-center gap-3">
      <Link to={`/projects/id/${encodeURIComponent(projectId)}`} aria-label="Back to overview" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent"><ArrowLeft size={18} /></Link>
      <div className="min-w-0 flex-1"><h1 className="flex items-center gap-2 text-lg font-semibold"><LayoutGrid size={16} />Board</h1><p className="text-sm text-muted-foreground">{board?.project.name}</p></div>
      <Button size="sm" variant="outline" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>Refresh Board</Button>
      <Button asChild size="sm"><Link to={`/jobs/new?${params}`}><Plus size={14} />New Job</Link></Button>
      <Button asChild size="sm" variant="outline"><Link to="/recipes">Recipes</Link></Button>
    </header>
    {error && <p role="alert" className="rounded-lg border border-destructive/40 p-3 text-sm">Unable to refresh Project Board: {error}. Retained data may be stale.</p>}
    {board && <>
      <div className="rounded-lg border border-border bg-card p-3 text-sm" aria-label="Persisted Project totals">
        <p>{board.totals.taskCount} tasks / {board.totals.linkedJobCount} linked Jobs / {board.totals.standaloneJobCount} standalone Jobs / {board.totals.reviewJobCount} Jobs in review / {board.totals.pendingApprovalCount} pending approvals</p>
        <p className="mt-1 text-xs text-muted-foreground">Full persisted Project scope, not the loaded pages. A linked Job is included in its task card, not counted as another work item. Recipe steps are detail inside that Job.</p>
      </div>
      {taskId && <div><TaskLinkInspector projectId={projectId} taskId={taskId} /><Button asChild size="sm" variant="ghost"><Link to={`/projects/id/${encodeURIComponent(projectId)}/board`}>Close task inspector</Link></Button></div>}
      <div className="grid items-start gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {columns.map((column) => <BoardColumn key={column.id} projectId={projectId} column={column} board={board} version={version} />)}
      </div>
      <ProjectAttention projectId={projectId} board={board} version={version} />
    </>}
  </div>;
}

function WorkCard({ item, projectId }: { item: ProjectBoardItem; projectId: string }) {
  const job = item.job;
  const label = item.tasks[0]?.storyNodeId ?? item.tasks[0]?.trackerTicketRef ?? job?.title ?? job?.id ?? item.id;
  const jobUrl = job ? `/jobs/${encodeURIComponent(job.id)}` : null;
  return <article className="space-y-2 rounded-lg border border-border bg-card p-3 text-sm" aria-label={`Work item: ${label}`}>
    <h3 className="break-words font-semibold">{label}</h3>
    {item.tasks.map((task) => <div key={task.id} className="flex flex-wrap items-center justify-between gap-2">
      <span className="text-xs text-muted-foreground">Task {task.storyNodeId ?? task.trackerTicketRef ?? task.id}: {task.state}</span>
      <Link className="text-xs text-primary underline" to={`/projects/id/${encodeURIComponent(projectId)}/board/task/${encodeURIComponent(task.id)}`}>Inspect task</Link>
    </div>)}
    {item.taskCount > item.tasks.length && <p className="text-xs text-muted-foreground">{item.taskCount} linked tasks, showing {item.tasks.length} identifiers.</p>}
    {job && <div className="space-y-1">
      <p className="text-xs">Job: {job.state}{job.resolution ? ` / delivery: ${job.resolution}` : ""}{job.archivedAt ? " / archived" : ""}</p>
      <p className="break-all text-xs text-muted-foreground">{job.repo} {job.branch ? `/ ${job.branch}` : ""}</p>
      {job.failureReason && <p className="text-xs text-destructive">{job.failureReason}</p>}
    </div>}
    {item.hasFailure && <p className="text-xs text-destructive">Recorded failure: inspect the task and Job outcomes.</p>}
    {item.pendingApprovalCount > 0 && <p className="text-xs">{item.pendingApprovalCount} pending approval{item.pendingApprovalCount === 1 ? "" : "s"}. Completion or archival does not resolve these decisions.</p>}
    {item.deliveryAttentionCount > 0 && <p className="text-xs">{item.deliveryAttentionCount} tracker operation{item.deliveryAttentionCount === 1 ? "" : "s"} need attention. Approval is not confirmed delivery.</p>}
    {item.recipe && <div className="space-y-1 border-t border-border pt-2">
      <p className="text-xs font-medium">{item.recipe.name} / revision {item.recipe.revision} / {item.recipe.state}</p>
      {item.recipe.reason && <p className="text-xs text-muted-foreground">{item.recipe.reason}</p>}
      {item.recipe.steps.map((step) => <p key={step.id} className="text-xs"><Link className="text-primary underline" to={`${jobUrl}?${new URLSearchParams({ run: item.recipe!.runId, step: step.id })}`}>{step.title}</Link>: {step.state}{step.reason ? ` / ${step.reason}` : ""}</p>)}
      {item.recipe.remainingStepCount > 0 && <p className="text-xs text-muted-foreground">{item.recipe.remainingStepCount} more step states in the run inspector.</p>}
    </div>}
    {jobUrl && <Button asChild size="sm" variant="outline"><Link to={jobUrl}>Open Job{item.recipe ? " and run" : ""}</Link></Button>}
    {!job && <p className="text-xs text-muted-foreground">Inspect the complete prompt and current eligibility before Start.</p>}
  </article>;
}

function BoardColumn({ projectId, column, board, version }: {
  projectId: string; column: { id: ProjectBoardColumn; title: string }; board: Board; version: number;
}) {
  const [after, setAfter] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const key = JSON.stringify([projectId, column.id, after]);
  const cached = useStore((state) => state.projectBoardPages[key]);
  const page: ProjectBoardPage | undefined = after ? cached : board.columns[column.id];
  useEffect(() => {
    if (!after) return;
    const controller = new AbortController();
    setError(null);
    setLoading(true);
    fetchProjectBoardPage(projectId, column.id, after, controller.signal).then((value) => {
      if (!controller.signal.aborted) useStore.setState((state) => ({ projectBoardPages: { ...state.projectBoardPages, [key]: value } }));
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(String(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, column.id, after, key, version, refresh]);
  return <section aria-label={column.title} className="space-y-3 rounded-lg border border-border bg-muted/20 p-3">
    <h2 className="flex items-center justify-between gap-2 text-sm font-semibold">{column.title}<span className="rounded-full bg-muted px-2 py-0.5 text-xs">{board.totals.workCounts[column.id] ?? 0}</span></h2>
    {page?.items.map((item) => <WorkCard key={item.id} projectId={projectId} item={item} />)}
    {page?.items.length === 0 && <p className="text-xs text-muted-foreground">No work on this page.</p>}
    {loading && <p role="status" className="text-xs text-muted-foreground">Reading this column...</p>}
    {error && <div role="alert"><p className="text-xs text-destructive">{error}</p><Button size="sm" variant="outline" onClick={() => setRefresh((value) => value + 1)}>Retry page</Button></div>}
    <div className="flex flex-wrap gap-2">
      {after && <Button size="sm" variant="outline" onClick={() => setAfter("")}>First page</Button>}
      {page?.nextCursor && <Button size="sm" variant="outline" disabled={loading} onClick={() => setAfter(page.nextCursor!)}>More work</Button>}
    </div>
  </section>;
}

function ProjectAttention({ projectId, board, version }: { projectId: string; board: Board; version: number }) {
  const [approvalAfter, setApprovalAfter] = useState("");
  const [deliveryAfter, setDeliveryAfter] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [deliveryError, setDeliveryError] = useState<string | null>(null);
  const approvalKey = JSON.stringify([projectId, approvalAfter]);
  const deliveryKey = JSON.stringify([projectId, deliveryAfter]);
  const approvals = useStore((state) => state.projectApprovalPages[approvalKey]);
  const deliveries = useStore((state) => state.projectDeliveryPages[deliveryKey]);
  useEffect(() => {
    const controller = new AbortController();
    setApprovalError(null);
    fetchProjectBoardApprovals(projectId, approvalAfter, controller.signal).then((value) => {
      if (!controller.signal.aborted) useStore.setState((state) => ({ projectApprovalPages: { ...state.projectApprovalPages, [approvalKey]: value } }));
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setApprovalError(String(cause)); });
    return () => controller.abort();
  }, [projectId, approvalAfter, approvalKey, version, refresh]);
  useEffect(() => {
    const controller = new AbortController();
    setDeliveryError(null);
    fetchProjectBoardDeliveries(projectId, deliveryAfter, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      useStore.getState().hydrateTrackerOperations(value.items);
      useStore.setState((state) => ({ projectDeliveryPages: { ...state.projectDeliveryPages, [deliveryKey]: value } }));
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setDeliveryError(String(cause)); });
    return () => controller.abort();
  }, [projectId, deliveryAfter, deliveryKey, version, refresh]);
  return <section className="space-y-4 rounded-lg border border-border bg-card p-4" aria-label="Project attention queue">
    <header className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">Decisions and tracker delivery</h2><Button size="sm" variant="outline" onClick={() => setRefresh((value) => value + 1)}>Refresh attention queue</Button></header>
    <p className="text-xs text-muted-foreground">{board.totals.pendingApprovalCount} pending approvals / {board.totals.deliveryAttentionCount} tracker operations needing attention. These are decision and delivery records, not additional task cards.</p>
    {approvalError && <p role="alert" className="text-sm text-destructive">Approval read failed: {approvalError}</p>}
    {approvals?.items.map((approval) => <article key={approval.id} className="space-y-2 rounded-md border border-border p-3 text-sm">
      <h3 className="font-medium">{approval.description}</h3>
      <p className="break-all text-xs text-muted-foreground">Approval {approval.id} / Job {approval.jobId}</p>
      <details><summary className="cursor-pointer text-xs">Exact proposal</summary><pre className="mt-2 whitespace-pre-wrap break-words text-xs">{approval.proposedAction ?? "No proposal retained"}</pre></details>
      <Link className="text-primary underline" to={`/jobs/${encodeURIComponent(approval.jobId)}`}>Inspect decision in Job</Link>
    </article>)}
    <div className="flex gap-2">
      {approvalAfter && <Button size="sm" variant="outline" onClick={() => setApprovalAfter("")}>First approvals</Button>}
      {approvals?.nextCursor && <Button size="sm" variant="outline" onClick={() => setApprovalAfter(approvals.nextCursor!)}>More approvals</Button>}
    </div>
    {deliveryError && <p role="alert" className="text-sm text-destructive">Delivery read failed: {deliveryError}</p>}
    {deliveries?.items.map((operation) => <TrackerOperation key={operation.id} operation={operation} />)}
    <div className="flex gap-2">
      {deliveryAfter && <Button size="sm" variant="outline" onClick={() => setDeliveryAfter("")}>First deliveries</Button>}
      {deliveries?.nextCursor && <Button size="sm" variant="outline" onClick={() => setDeliveryAfter(deliveries.nextCursor!)}>More deliveries</Button>}
    </div>
  </section>;
}
