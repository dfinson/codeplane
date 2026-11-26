import { useEffect, useState, useCallback } from "react";
import { Link, Navigate, Outlet, useLocation, useNavigate, useParams } from "react-router-dom";
import { FolderGit2, ChevronLeft, ChevronRight } from "lucide-react";
import { toast } from "sonner";
import { fetchProjects } from "../api/client";
import type { ProjectResponse } from "../api/types";
import { cn } from "../lib/utils";
import { Spinner } from "./ui/spinner";
import { Button } from "./ui/button";
import { matchesNameFilter } from "../lib/nameFilter";
import { ProjectsOverview } from "./ProjectsOverview";
import { ViewTabBar, type ViewTabBarItem } from "./ViewTabBar";
import { pathBasename } from "../lib/paths";
import { useIsMobile } from "../hooks/useIsMobile";

export interface RepoLayoutOutletContext {
  onProjectUpdated: (project: ProjectResponse) => void;
  onProjectCreated: (project: ProjectResponse) => void;
}

export function RepoLayout() {
  const { projectId, repoPath } = useParams<{ projectId: string; repoPath?: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const [projects, setProjects] = useState<ProjectResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const isNarrow = useIsMobile(640);
  const [collapsed, setCollapsed] = useState(isNarrow);
  const [filterQuery, setFilterQuery] = useState("");
  const [loadFailed, setLoadFailed] = useState(false);
  const [selectedRepoPath, setSelectedRepoPath] = useState<string | undefined>(undefined);
  const [expandedProjectRepoListId, setExpandedProjectRepoListId] = useState<string | null>(null);

  useEffect(() => {
    if (isNarrow) setCollapsed(true);
  }, [isNarrow]);

  const loadProjects = useCallback(async () => {
    try {
      const res = await fetchProjects();
      setProjects(res.items);
      setLoadFailed(false);
    } catch {
      setLoadFailed(true);
      toast.error("Failed to load Projects");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadProjects(); }, [loadProjects]);

  const onProjectUpdated = useCallback((updated: ProjectResponse) => {
    setProjects((current) => current.map((project) => project.id === updated.id ? updated : project));
  }, []);

  const onProjectCreated = useCallback((created: ProjectResponse) => {
    setProjects((current) => current.some((project) => project.id === created.id)
      ? current.map((project) => project.id === created.id ? created : project)
      : [...current, created]);
  }, []);

  const filteredProjects = projects.filter((project) => matchesNameFilter(project.name, filterQuery));
  const activeProject = projects.find((project) => project.id === projectId);
  const projectUrl = projectId ? `/projects/id/${encodeURIComponent(projectId)}` : "";
  const projectTabs: ViewTabBarItem[] = [
    { id: "overview", label: "Overview" },
    { id: "board", label: "Board" },
    { id: "chats", label: "Chats" },
    { id: "settings", label: "Settings" },
  ];
  const displayedRepoPath = selectedRepoPath ?? (activeProject?.repoPaths.length === 1 ? activeProject.repoPaths[0] : undefined);
  const repoScopedUrl = displayedRepoPath
    ? `${projectUrl}/repos/${encodeURIComponent(displayedRepoPath)}`
    : null;
  const repoView = location.pathname.match(/\/(jobs|cost|settings)$/)?.[1] ?? "jobs";
  const activeRepoView = location.pathname.includes("/repos/") ? repoView : "";
  const activeProjectTab = location.pathname === projectUrl
    ? "overview"
    : location.pathname.startsWith(`${projectUrl}/board`)
      ? "board"
      : location.pathname.startsWith(`${projectUrl}/chats`)
        ? "chats"
        : location.pathname.startsWith(`${projectUrl}/settings`)
          ? "settings"
          : "";
  const repoTabs: ViewTabBarItem[] = (["jobs", "cost"] as const).map((view) => ({
    id: view,
    label: view === "jobs" ? "Jobs" : view.charAt(0).toUpperCase() + view.slice(1),
    disabled: !repoScopedUrl,
    disabledTip: "Select a repository above to view Jobs and Cost.",
  }));

  useEffect(() => {
    if (repoPath) {
      setSelectedRepoPath(repoPath);
      return;
    }
    if (activeProject?.repoPaths.length === 1) {
      setSelectedRepoPath(activeProject.repoPaths[0]);
      return;
    }
    setSelectedRepoPath(undefined);
  }, [activeProject, repoPath]);

  if (!loading && activeProject && repoPath && !activeProject.repoPaths.includes(repoPath)) {
    return <Navigate to={`${projectUrl}/board`} replace />;
  }
  // Direct project-id routes are the canonical shell and can resolve their own
  // Project context by ID, even before the sidebar list is hydrated. Redirecting
  // here breaks the stable project route on fresh loads and when a Project is
  // returned from a direct fetch but not yet present in the global list.

  return (
    <div className="flex h-full min-h-0">
      {/* Sidebar */}
      <aside
        className={cn(
          "shrink-0 border-r border-border bg-card flex flex-col transition-all duration-200",
          collapsed ? "w-12" : "w-56",
        )}
      >
        <div className="flex items-center justify-between px-3 py-3 border-b border-border">
          {!collapsed && (
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              Projects
            </span>
          )}
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto py-1" aria-label="Project list">
          {!collapsed && !loading && projects.length > 0 && (
            <div className="px-2 pb-2">
              <input
                type="text"
                value={filterQuery}
                onChange={(e) => setFilterQuery(e.target.value)}
                placeholder="Filter projects"
                aria-label="Filter projects"
                className="w-full rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              />
            </div>
          )}
          {loading ? (
            <div className="flex justify-center py-4">
              <Spinner className="w-4 h-4" />
            </div>
          ) : projects.length === 0 ? (
            <p className="text-xs text-muted-foreground text-center py-4 px-2">
              No Projects
            </p>
          ) : filteredProjects.length === 0 ? (
            <p className="text-xs text-muted-foreground text-center py-4 px-2">
              No matches
            </p>
          ) : (
            filteredProjects.map((project) => {
              const repoDisclosureId = `project-repos-${project.id}`;
              const repoListExpanded = expandedProjectRepoListId === project.id;
              return (
                <div key={project.id} className="px-1 py-0.5">
                  <div className="flex items-center gap-2">
                    <Link
                      to={`/projects/id/${encodeURIComponent(project.id)}/board`}
                      title={project.repoPaths.join(", ")}
                      className={cn(
                        "flex flex-1 items-center gap-2 rounded-md px-2 py-2 text-sm transition-colors",
                        project.id === projectId
                          ? "bg-accent text-foreground"
                          : "text-muted-foreground hover:text-foreground hover:bg-accent/50",
                      )}
                    >
                      <FolderGit2 size={14} className="shrink-0" />
                      {!collapsed && (
                        <>
                          <span className="flex-1 truncate">{project.name}</span>
                          {project.repoPaths.length > 1 && (
                            <span className="text-[10px] text-muted-foreground shrink-0">
                              {project.repoPaths.length} repos
                            </span>
                          )}
                        </>
                      )}
                    </Link>
                    {!collapsed && project.repoPaths.length > 0 && (
                      <button
                        type="button"
                        aria-label="Show repositories in this project"
                        aria-expanded={repoListExpanded}
                        aria-controls={repoDisclosureId}
                        className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                        onClick={() => setExpandedProjectRepoListId((current) => current === project.id ? null : project.id)}
                      >
                        {repoListExpanded ? <ChevronLeft size={12} className="rotate-[-90deg]" /> : <ChevronRight size={12} className="rotate-90" />}
                      </button>
                    )}
                  </div>
                  {!collapsed && repoListExpanded && project.repoPaths.length > 0 && (
                    <ul id={repoDisclosureId} className="mt-1 ml-6 space-y-1 text-[11px] text-muted-foreground">
                      {project.repoPaths.map((path) => (
                        <li key={path}>{pathBasename(path) || path}</li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })
          )}
        </nav>

      </aside>

      {/* Main content area */}
      <div className="flex-1 min-w-0 overflow-y-auto p-4 md:p-6">
        {!projectId ? <ProjectsOverview /> : (
          <>
            {activeProject && (
              <div className="max-w-4xl mx-auto mb-5">
                <div className="flex items-baseline justify-between gap-3 mb-3">
                  <div>
                    <h1 className="text-xl font-semibold">{activeProject.name}</h1>
                    <p className="text-xs text-muted-foreground">
                      {activeProject.repoPaths.length} {activeProject.repoPaths.length === 1 ? "repository" : "repositories"} in this Project
                    </p>
                  </div>
                  <Button variant="ghost" size="sm" onClick={() => navigate(`${projectUrl}/settings`)}>Project settings</Button>
                </div>
                <ViewTabBar
                  activeTab={activeProjectTab}
                  onTabChange={(tab) => {
                    const destination = tab === "overview"
                      ? projectUrl
                      : `${projectUrl}/${tab}`;
                    navigate(destination);
                  }}
                  items={projectTabs}
                  variant="inline"
                  mobileBehavior="visible"
                  className="gap-0"
                  ariaLabel="Project navigation"
                />
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <label htmlFor="project-repository" className="text-xs text-muted-foreground">
                    Repository
                  </label>
                  <select
                    id="project-repository"
                    value={displayedRepoPath ?? ""}
                    onChange={(event) => {
                      const selectedRepo = event.target.value;
                      if (!selectedRepo) {
                        setSelectedRepoPath(undefined);
                        return;
                      }
                      setSelectedRepoPath(selectedRepo);
                      navigate(`${projectUrl}/repos/${encodeURIComponent(selectedRepo)}/${repoView}`);
                    }}
                    className="h-8 min-w-48 rounded-md border border-border bg-background px-2 text-xs"
                  >
                    {activeProject.repoPaths.length > 1 && <option value="">Select a repository...</option>}
                    {activeProject.repoPaths.map((path) => (
                      <option key={path} value={path}>{pathBasename(path) || path}</option>
                    ))}
                  </select>
                  <div className="flex-1 min-w-0">
                    <ViewTabBar
                      activeTab={repoScopedUrl ? activeRepoView : ""}
                      onTabChange={(tab) => {
                        if (repoScopedUrl) navigate(`${repoScopedUrl}/${tab}`);
                      }}
                      items={repoTabs}
                      variant="inline"
                      mobileBehavior="visible"
                      className="gap-0"
                      ariaLabel="Repository navigation"
                    />
                    {!repoScopedUrl && (
                      <span className="mt-2 block text-xs text-muted-foreground">
                        Select a repository above to view Jobs and Cost.
                      </span>
                    )}
                  </div>
                </div>
              </div>
            )}
            {loading ? (
              <div className="flex justify-center py-16"><Spinner /></div>
            ) : loadFailed ? (
              <div role="alert" className="rounded-lg border border-red-500/40 bg-card p-8 text-center">
                Project navigation could not be loaded.
              </div>
            ) : (
              <Outlet context={{ onProjectUpdated, onProjectCreated } satisfies RepoLayoutOutletContext} />
            )}
          </>
        )}
      </div>
    </div>
  );
}

