import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { ArrowLeft, Download, LayoutGrid, Plus } from "lucide-react";
import { toast } from "sonner";
import { useShallow } from "zustand/react/shallow";
import {
  useStore,
  enrichJob,
  selectActiveJobsForProject,
  selectSignoffJobsForProject,
  selectAttentionJobsForProject,
} from "../store";
import type { JobSummary } from "../store";
import { fetchJobs, fetchProject, fetchProjectTaskLinks, ingestProjectTasks, startTaskLink } from "../api/client";
import type { ProjectResponse, TaskLinkResponse } from "../api/types";
import { KanbanColumn } from "./KanbanColumn";
import { KanbanSkeleton } from "./KanbanSkeleton";
import { TaskLinkCard, type TaskLinkDependencyView } from "./TaskLinkCard";
import { Button } from "./ui/button";
import { KANBAN_COLUMNS } from "../constants/kanban";

function lifecycleSignature(jobs: Record<string, JobSummary>, repoPaths: string[]): string {
  const memberRepos = new Set(repoPaths);
  return Object.values(jobs)
    .filter((job) => memberRepos.has(job.repo))
    .map((job) => `${job.id}:${job.state}`)
    .sort()
    .join("|");
}

function taskLabel(taskLink: TaskLinkResponse): string {
  return taskLink.storyNodeId ?? taskLink.trackerTicketRef ?? taskLink.id;
}

function taskCardId(taskLinkId: string): string {
  return `task-link-card-${taskLinkId}`;
}

function taskDependencyKey(taskLink: TaskLinkResponse): string | null {
  if (taskLink.storyNodeId) return `${taskLink.repoPath}::${taskLink.storyNodeId}`;
  if (taskLink.trackerTicketRef) return `${taskLink.repoPath}::${taskLink.trackerTicketRef}`;
  return null;
}

function humanizeTaskLabel(label: string): string {
  return label
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function humanizeTaskState(state: TaskLinkResponse["state"]): string {
  if (state === "running" || state === "starting") return "in progress";
  return state.replace(/_/g, " ");
}

function formatTaskDependency(taskLink: TaskLinkResponse): string {
  return `${humanizeTaskLabel(taskLabel(taskLink))} (${humanizeTaskState(taskLink.state)})`;
}

function buildNewJobUrl(projectId: string | undefined, project: ProjectResponse | null): string {
  const params = new URLSearchParams();
  const singleRepoPath = project?.repoPaths.length === 1 ? project.repoPaths[0] : undefined;
  if (projectId) params.set("projectId", projectId);
  if (singleRepoPath) params.set("repo", singleRepoPath);
  const query = params.toString();
  return query ? `/jobs/new?${query}` : "/jobs/new";
}

/**
 * Project-scoped Kanban board (Story 2.3 / CAP-1). Child route of the
 * project-identity-keyed `/projects/id/:projectId` shell — `projectId` is
 * read from the URL, not client-only state, so a refresh or shared link
 * resolves to the same scoped board even after a Project's member repos change.
 *
 * Aggregates jobs across ALL of the Project's member repos (not just the
 * first), via the project-scoped selectors — a genuinely multi-repo Project's
 * board shows every member repo's jobs on one board.
 *
 * Story 4.4 / CAP-10: also fetches the owning Project's TaskLinks (a second,
 * independent call — `GET /settings/projects/:id/task-links`, AD-11) and renders
 * them in a separate Tasks section before the Job-only columns.
 */
export function RepoBoard() {
  const { projectId, taskLinkId } = useParams<{ projectId: string; taskLinkId?: string }>();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [taskLinks, setTaskLinks] = useState<TaskLinkResponse[]>([]);
  const [ingesting, setIngesting] = useState(false);
  const [startingId, setStartingId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const currentProject = project?.id === projectId ? project : null;
  const repoPaths = currentProject?.repoPaths ?? [];
  const hasJobs = useStore((state) => Object.keys(state.jobs).length > 0);
  const jobLifecycleSignature = useStore((state) => lifecycleSignature(state.jobs, repoPaths));
  const reconciledLifecycleSignature = useRef<string | null>(null);

  const activeJobs = useStore(useShallow(selectActiveJobsForProject(projectId ?? "")));
  const signoffJobs = useStore(useShallow(selectSignoffJobsForProject(projectId ?? "")));
  const attentionJobs = useStore(useShallow(selectAttentionJobsForProject(projectId ?? "")));

  const highlightedTask = useMemo(
    () => (taskLinkId ? taskLinks.find((taskLink) => taskLink.id === taskLinkId) ?? null : null),
    [taskLinkId, taskLinks],
  );
  const missingDeepLinkedTask = Boolean(taskLinkId) && !loading && !highlightedTask;
  const dependencyIndex = useMemo(() => {
    const index = new Map<string, TaskLinkResponse>();
    for (const taskLink of taskLinks) {
      const key = taskDependencyKey(taskLink);
      if (key) index.set(key, taskLink);
    }
    return index;
  }, [taskLinks]);
  const newJobUrl = buildNewJobUrl(projectId, currentProject);

  useEffect(() => {
    if (!highlightedTask) return;
    const card = document.getElementById(taskCardId(highlightedTask.id));
    card?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [highlightedTask]);

  useEffect(() => {
    let cancelled = false;
    setProject(null);
    setTaskLinks([]);
    setLoadError(null);
    setLoading(true);
    reconciledLifecycleSignature.current = null;

    if (!projectId) {
      setLoadError("No Project was selected.");
      setLoading(false);
      return () => { cancelled = true; };
    }

    const fetchEveryJob = async () => {
      const items: JobSummary[] = [];
      const seenCursors = new Set<string>();
      let cursor: string | undefined;
      do {
        const params: { limit: number; archived: boolean; cursor?: string } = {
          limit: 100,
          archived: false,
        };
        if (cursor) params.cursor = cursor;
        const page = await fetchJobs(params);
        items.push(...(page.items as JobSummary[]));
        const nextCursor = page.hasMore ? page.cursor ?? undefined : undefined;
        if (nextCursor && seenCursors.has(nextCursor)) {
          throw new Error("Job pagination returned a repeated cursor.");
        }
        if (nextCursor) seenCursors.add(nextCursor);
        cursor = nextCursor;
      } while (cursor);
      return items;
    };

    Promise.all([
      fetchProject(projectId),
      fetchEveryJob(),
      fetchProjectTaskLinks(projectId),
    ])
      .then(([nextProject, jobs, taskLinkResponse]) => {
        if (cancelled) return;
        setProject(nextProject);
        setTaskLinks(taskLinkResponse.items);
        useStore.setState((state) => {
          const updated = { ...state.jobs };
          for (const job of jobs) updated[job.id] = enrichJob(job);
          reconciledLifecycleSignature.current = lifecycleSignature(updated, nextProject.repoPaths);
          return { jobs: updated };
        });
      })
      .catch((err) => {
        if (cancelled) return;
        setProject(null);
        setTaskLinks([]);
        setLoadError(err instanceof Error ? err.message : "Failed to load the Project board.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [projectId]);

  useEffect(() => {
    if (!projectId || project?.id !== projectId) return;
    if (reconciledLifecycleSignature.current === jobLifecycleSignature) return;
    let cancelled = false;
    reconciledLifecycleSignature.current = jobLifecycleSignature;

    fetchProjectTaskLinks(projectId)
      .then((response) => {
        if (!cancelled) setTaskLinks(response.items);
      })
      .catch(() => {
        // Keep the last authoritative snapshot; a later lifecycle event retries.
      });

    return () => { cancelled = true; };
  }, [jobLifecycleSignature, project?.id, projectId]);

  if ((loading && !hasJobs) || (!loadError && project !== null && !currentProject)) {
    return <KanbanSkeleton />;
  }
  if (loadError) {
    return (
      <div role="alert" className="rounded-lg border border-red-500/40 bg-card p-8 text-center">
        <h1 className="text-lg font-semibold">Unable to load Project board</h1>
        <p className="mt-2 text-sm text-muted-foreground">{loadError}</p>
      </div>
    );
  }

  const handleIngest = async () => {
    if (!projectId) return;
    setIngesting(true);
    try {
      const result = await ingestProjectTasks(projectId);
      setTaskLinks(result.items);
      toast.success(`Ingested ${result.items.length} tasks.`);
    } catch (error) {
      toast.error(String(error));
    } finally {
      setIngesting(false);
    }
  };

  const handleStart = async (taskLink: TaskLinkResponse) => {
    if (!projectId) return;
    setStartingId(taskLink.id);
    try {
      const updated = await startTaskLink(projectId, taskLink.id);
      setTaskLinks((current) => current.map((item) => item.id === updated.id ? updated : item));
      toast.success(`Started ${updated.storyNodeId ?? updated.trackerTicketRef ?? "task"}.`);
    } catch (error) {
      toast.error(String(error));
    } finally {
      setStartingId(null);
    }
  };

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <Link
          to={`/projects/id/${encodeURIComponent(projectId ?? "")}`}
          className="p-1.5 rounded-md hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
          aria-label="Back to overview"
        >
          <ArrowLeft size={18} />
        </Link>
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold flex items-center gap-2">
            <LayoutGrid size={16} className="text-muted-foreground" />
            Board
          </h1>
          <p className="text-sm text-muted-foreground truncate">{currentProject?.name ?? ""}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button asChild size="sm">
            <Link to={newJobUrl}>
              <Plus size={14} />
              New Job
            </Link>
          </Button>
          <Button variant="outline" size="sm" disabled={ingesting} onClick={() => void handleIngest()}>
            <Download size={14} className={ingesting ? "animate-pulse" : ""} />
            {ingesting ? "Ingesting…" : "Ingest tasks"}
          </Button>
        </div>
      </div>

      {highlightedTask && (
        <div className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-primary/30 bg-primary/5 px-4 py-3 text-sm">
          <p>
            <span>Viewing task</span>{" "}
            <span className="font-medium">{taskLabel(highlightedTask)}</span>.
          </p>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate(`/projects/id/${encodeURIComponent(projectId ?? "")}/board`)}
          >
            Clear
          </Button>
        </div>
      )}

      {missingDeepLinkedTask && (
        <div role="alert" className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-300">
          Task no longer exists in this project.
        </div>
      )}

      <section aria-labelledby="project-tasks-heading" className="mb-6 space-y-3">
        <div className="flex items-center gap-2">
          <h2 id="project-tasks-heading" className="text-sm font-semibold">Tasks</h2>
          <span aria-label={`${taskLinks.length} tasks`} className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground">
            {taskLinks.length}
          </span>
        </div>
        {taskLinks.length === 0 && (
          <div className="mb-4 rounded-lg border border-border bg-card px-4 py-4">
            <h2 className="text-sm font-semibold">No tracker tasks yet.</h2>
            <p className="mt-1 text-sm text-muted-foreground">To see tasks here:</p>
            <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
              <li>Connect a tracker credential.</li>
              <li>Add a tracker link in Project settings.</li>
              <li>Click Ingest tasks.</li>
            </ol>
            <Button asChild variant="outline" size="sm" className="mt-3">
              <Link to={`/projects/id/${encodeURIComponent(projectId ?? "")}/settings`}>Open Project settings</Link>
            </Button>
          </div>
        )}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {taskLinks.map((taskLink) => (
            <TaskLinkCard
              key={taskLink.id}
              taskLink={taskLink}
              dependencies={taskLink.dependsOn.map((dependency) => {
                const matchedTask = dependencyIndex.get(dependency);
                return matchedTask
                  ? { id: dependency, label: formatTaskDependency(matchedTask), resolved: true } satisfies TaskLinkDependencyView
                  : { id: dependency, label: dependency, resolved: false } satisfies TaskLinkDependencyView;
              })}
              highlighted={taskLink.id === highlightedTask?.id}
              starting={startingId === taskLink.id}
              onStart={(link) => void handleStart(link)}
            />
          ))}
        </div>
      </section>

      <section aria-labelledby="project-jobs-heading" className="space-y-3">
        <h2 id="project-jobs-heading" className="text-sm font-semibold">Jobs</h2>
        <div className="grid grid-cols-3 gap-3 h-[calc(100dvh-200px)] max-lg:grid-cols-2 max-sm:grid-cols-1">
          <KanbanColumn title={KANBAN_COLUMNS.IN_PROGRESS} jobs={activeJobs} />
          <KanbanColumn title={KANBAN_COLUMNS.AWAITING_INPUT} jobs={signoffJobs} />
          <KanbanColumn title={KANBAN_COLUMNS.FAILED} jobs={attentionJobs} />
        </div>
      </section>
    </div>
  );
}
