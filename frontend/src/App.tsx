import { Component, type ReactNode, Suspense, useEffect, useState } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { Routes, Route, Navigate, Link, useNavigate, useLocation, useParams } from "react-router-dom";
import { Search, ExternalLink } from "lucide-react";
import { modKey } from "./lib/utils";
import { CommandPalette } from "./components/CommandPalette";
import { NavMenuSlideout } from "./components/NavMenuSlideout";
import { useSSE } from "./hooks/useSSE";
import { useStore } from "./store";
import { ConnectionStatusIndicator } from "./components/ConnectionStatusIndicator";
import { Spinner } from "./components/ui/spinner";
import { lazyRetry } from "./lib/lazyRetry";

const JobDetailScreen = lazyRetry(() =>
  import("./components/JobDetailScreen").then((module) => ({ default: module.JobDetailScreen })),
);
const JobCreationScreen = lazyRetry(() =>
  import("./components/JobCreationScreen").then((module) => ({ default: module.JobCreationScreen })),
);
const RecipeDefinitionEditor = lazyRetry(() =>
  import("./components/RecipeDefinitionEditor").then((module) => ({ default: module.RecipeDefinitionEditor })),
);
const SettingsScreen = lazyRetry(() =>
  import("./components/SettingsScreen").then((module) => ({ default: module.SettingsScreen })),
);
const HistoryScreen = lazyRetry(() =>
  import("./components/HistoryScreen").then((module) => ({ default: module.HistoryScreen })),
);
const AnalyticsScreen = lazyRetry(() =>
  import("./components/AnalyticsScreen").then((module) => ({ default: module.AnalyticsScreen })),
);
const RepoLayout = lazyRetry(() =>
  import("./components/RepoLayout").then((module) => ({ default: module.RepoLayout })),
);
const RepoOverview = lazyRetry(() =>
  import("./components/RepoOverview").then((module) => ({ default: module.RepoOverview })),
);
const RepoJobs = lazyRetry(() =>
  import("./components/RepoJobs").then((module) => ({ default: module.RepoJobs })),
);
const RepoBoard = lazyRetry(() =>
  import("./components/RepoBoard").then((module) => ({ default: module.RepoBoard })),
);
const RepoCost = lazyRetry(() =>
  import("./components/RepoCost").then((module) => ({ default: module.RepoCost })),
);
const RepoSettings = lazyRetry(() =>
  import("./components/RepoSettings").then((module) => ({ default: module.RepoSettings })),
);
const ProjectChats = lazyRetry(() =>
  import("./components/ProjectChats").then((module) => ({ default: module.ProjectChats })),
);
/* SharedJobView disabled — read-only view not useful yet
const SharedJobView = lazyRetry(() =>
  import("./components/SharedJobView").then((module) => ({ default: module.SharedJobView })),
);
*/
const TerminalDrawer = lazyRetry(() =>
  import("./components/TerminalDrawer").then((module) => ({ default: module.TerminalDrawer })),
);

/* ------------------------------------------------------------------ */
/* Error boundary                                                      */
/* ------------------------------------------------------------------ */

class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  private isChunkError(error: Error): boolean {
    const msg = error.message || "";
    return /loading.*chunk|dynamic.*import|failed to fetch/i.test(msg);
  }
  render() {
    if (this.state.error) {
      const isChunk = this.isChunkError(this.state.error);
      return (
        <div className="p-8 max-w-2xl mx-auto">
          <p className="text-lg font-semibold text-red-400 mb-2">
            {isChunk ? "A network error occurred loading the page" : "Something went wrong"}
          </p>
          {!isChunk && (
            <pre className="text-xs text-muted-foreground whitespace-pre-wrap bg-card rounded-lg p-4 border border-border overflow-auto">
              {this.state.error.message}{"\n"}{this.state.error.stack}
            </pre>
          )}
          <button
            onClick={() => isChunk ? window.location.reload() : this.setState({ error: null })}
            className="mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90"
          >
            {isChunk ? "Reload page" : "Try again"}
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

function RouteFallback() {
  return (
    <div className="flex items-center justify-center py-20">
      <Spinner size="lg" />
    </div>
  );
}

/**
 * Resolves a legacy `/projects/:repoPath[/...]` URL (repo-path-as-routing-key,
 * pre project-id routing) to its canonical `/projects/id/:projectId[/...]` URL.
 * Old bookmarks/shared links must still work — this looks up the owning
 * Project by repo membership and redirects, rather than 404ing outright.
 * `repoScoped` routes (cost/jobs) carry the repo path forward as a
 * nested segment since those screens stay repo-keyed by spec.
 */
function LegacyRepoRedirect({ suffix, repoScoped }: { suffix: string; repoScoped?: boolean }) {
  const { repoPath } = useParams<{ repoPath: string }>();
  const decoded = repoPath ?? "";
  const [target, setTarget] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    let ignore = false;
    if (!decoded) { setTarget(null); return; }
    import("./api/client").then(({ fetchProjects }) =>
      fetchProjects().then((res) => {
        if (ignore) return;
        const project = res.items.find((p) => p.repoPaths.includes(decoded));
        if (!project) { setTarget(null); return; }
        const base = `/projects/id/${encodeURIComponent(project.id)}`;
        setTarget(repoScoped ? `${base}/repos/${encodeURIComponent(decoded)}${suffix}` : `${base}${suffix}`);
      }).catch(() => { if (!ignore) setTarget(null); }),
    );
    return () => { ignore = true; };
  }, [decoded, suffix, repoScoped]);

  if (target === undefined) return <RouteFallback />;
  return <Navigate to={target ?? "/projects"} replace />;
}

/* ------------------------------------------------------------------ */
/* App                                                                 */
/* ------------------------------------------------------------------ */

export function App() {
  useSSE();
  const navigate = useNavigate();
  const location = useLocation();
  const isJobDetail = /^\/jobs\/[^/]+$/.test(location.pathname) && location.pathname !== "/jobs/new";
  const jobId = isJobDetail ? location.pathname.split("/")[2] : null;
  const jobTitle = useStore((s) => jobId ? s.jobs[jobId]?.title : null);
  const toggleTerminalDrawer = useStore((s) => s.toggleTerminalDrawer);
  const terminalDrawerOpen = useStore((s) => s.terminalDrawerOpen);
  const initSdksAndModels = useStore((s) => s.initSdksAndModels);

  useEffect(() => {
    initSdksAndModels();
  }, [initSdksAndModels]);

  // Global keyboard shortcuts
  useHotkeys(
    "ctrl+`",
    () => {
      if (!terminalDrawerOpen) {
        toggleTerminalDrawer();
      } else {
        const active = document.activeElement;
        const terminalEl = document.querySelector(".xterm-helper-textarea, .xterm canvas");
        const focusedInTerminal = terminalEl && (active === terminalEl || terminalEl.contains(active));
        if (focusedInTerminal) {
          toggleTerminalDrawer();
        } else {
          (terminalEl as HTMLElement | null)?.focus();
        }
      }
    },
    { enableOnFormTags: true, preventDefault: true, useKey: true },
    [terminalDrawerOpen, toggleTerminalDrawer],
  );
  useHotkeys("alt+j", () => navigate("/"), { preventDefault: true });
  useHotkeys("alt+n", () => navigate("/jobs/new"), { preventDefault: true });
  useHotkeys("alt+a", () => navigate("/analytics"), { preventDefault: true });
  useHotkeys("alt+r", () => navigate("/projects"), { preventDefault: true });
  useHotkeys("alt+h", () => navigate("/history"), { preventDefault: true });
  useHotkeys("ctrl+comma,meta+comma", () => navigate("/settings"), {
    enableOnFormTags: true,
    preventDefault: true,
  });

  return (
    <div className="flex flex-col h-[100dvh] overflow-x-hidden">
      <header className={`flex items-center justify-between px-4 h-12 shrink-0 border-b border-border bg-card ${isJobDetail ? "hidden" : ""}`}>
        <Link to="/" className="no-underline flex items-center gap-3.5 hover:opacity-80 transition-opacity">
          <img src="/mark.png" alt="" className="h-8 w-8 object-contain brightness-110 drop-shadow-[0_0_3px_rgba(255,255,255,0.08)]" />
          <span className="font-semibold text-white/95 tracking-tight leading-none">
            CodePlane
          </span>
        </Link>

        {isJobDetail && jobTitle ? (
          <div className="hidden md:flex items-center gap-2 flex-1 min-w-0 mx-4">
            <span className="text-muted-foreground/60">/</span>
            <span className="text-sm font-medium text-foreground/90 truncate max-w-[20rem]">{jobTitle}</span>
          </div>
        ) : (
          <button
            onClick={() => window.dispatchEvent(new CustomEvent("open-command-palette"))}
            aria-label="Search or navigate"
            className="hidden md:flex md:w-72 lg:w-96 items-center justify-between gap-3 rounded-lg border border-border bg-background/70 px-4 py-2 text-sm text-muted-foreground shadow-sm transition-colors hover:text-foreground hover:bg-accent"
          >
            <span className="flex items-center gap-2">
              <Search size={14} />
              <span>Search or navigate...</span>
            </span>
            <kbd className="rounded border border-border px-1.5 py-0.5 font-mono text-xs">{modKey}+K</kbd>
          </button>
        )}

        <div className="flex items-center gap-1">
          <a
            href="https://dfinson.github.io/codeplane"
            target="_blank"
            rel="noreferrer"
            className="hidden md:flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
          >
            <span>Docs</span>
            <ExternalLink size={13} />
          </a>
          <ConnectionStatusIndicator />
          <NavMenuSlideout />
        </div>
      </header>

      <main className={`flex-1 ${isJobDetail ? "p-0 md:px-0 md:py-0 overflow-y-auto md:overflow-hidden md:flex md:flex-col" : "p-3 sm:p-4 md:p-6 overflow-y-auto"} ${terminalDrawerOpen ? "min-h-0" : ""}`}>
        <ErrorBoundary>
          <Suspense fallback={<RouteFallback />}>
            <Routes>
              <Route path="/" element={<Navigate to="/projects" replace />} />
              <Route path="/jobs/new" element={<JobCreationScreen />} />
              <Route path="/recipes/:definitionId?" element={<RecipeDefinitionEditor />} />
              <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
              <Route path="/history" element={<HistoryScreen />} />
              <Route path="/analytics" element={<AnalyticsScreen />} />
              <Route path="/chats" element={<ProjectChats />} />
              <Route path="/chats/:chatId" element={<ProjectChats />} />
              <Route path="/projects" element={<RepoLayout />}>
                <Route index element={<RepoOverview />} />
                <Route path="id/:projectId" element={<RepoOverview />} />
                <Route path="id/:projectId/board" element={<RepoBoard />} />
                <Route path="id/:projectId/board/task/:taskLinkId" element={<RepoBoard />} />
                <Route path="id/:projectId/chats" element={<ProjectChats />} />
                <Route path="id/:projectId/chats/:chatId" element={<ProjectChats />} />
                <Route path="id/:projectId/settings" element={<RepoSettings />} />
                <Route path="id/:projectId/repos/:repoPath/jobs" element={<RepoJobs />} />
                <Route path="id/:projectId/repos/:repoPath/cost" element={<RepoCost />} />
                <Route path="id/:projectId/repos/:repoPath/settings" element={<RepoSettings />} />
                {/* Legacy repo-path-keyed URLs (pre project-id routing). Not stable/shareable
                    across a Project's member-repo edits, but old bookmarks/links must still
                    resolve — redirect to the canonical project-id URL instead of 404ing. */}
                <Route path=":repoPath" element={<LegacyRepoRedirect suffix="" />} />
                <Route path=":repoPath/board" element={<LegacyRepoRedirect suffix="/board" />} />
                <Route path=":repoPath/chats" element={<LegacyRepoRedirect suffix="/chats" />} />
                <Route path=":repoPath/settings" element={<LegacyRepoRedirect suffix="/settings" />} />
                <Route path=":repoPath/jobs" element={<LegacyRepoRedirect suffix="/jobs" repoScoped />} />
                <Route path=":repoPath/cost" element={<LegacyRepoRedirect suffix="/cost" repoScoped />} />
              </Route>
              <Route path="/settings" element={<SettingsScreen />} />
              {/* Share disabled — read-only view not useful yet
              <Route path="/shared/:token" element={<SharedJobView />} />
              */}
            </Routes>
          </Suspense>
        </ErrorBoundary>
      </main>

      <Suspense fallback={null}>
        <TerminalDrawer />
      </Suspense>
      <CommandPalette />
    </div>
  );
}
