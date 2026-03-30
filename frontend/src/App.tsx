import { Component, type ReactNode, Suspense, useEffect } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { Routes, Route, Link, useNavigate } from "react-router-dom";
import { Search, ExternalLink } from "lucide-react";
import { modKey } from "./lib/utils";
import { CommandPalette } from "./components/CommandPalette";
import { NavMenuSlideout } from "./components/NavMenuSlideout";
import { useSSE } from "./hooks/useSSE";
import { useStore, selectConnectionStatus } from "./store";
import { DashboardScreen } from "./components/DashboardScreen";
import { DotBadge } from "./components/ui/badge";
import { Spinner } from "./components/ui/spinner";
import { lazyRetry } from "./lib/lazyRetry";

const JobDetailScreen = lazyRetry(() =>
  import("./components/JobDetailScreen").then((module) => ({ default: module.JobDetailScreen })),
);
const JobCreationScreen = lazyRetry(() =>
  import("./components/JobCreationScreen").then((module) => ({ default: module.JobCreationScreen })),
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

/* ------------------------------------------------------------------ */
/* Connection status                                                   */
/* ------------------------------------------------------------------ */

function ConnectionStatusIndicator() {
  const status = useStore(selectConnectionStatus);
  const color = status === "connected" ? "green" : status === "disconnected" ? "red" : "yellow";
  const label =
    status === "connecting" ? "Connecting\u2026"
    : status === "reconnecting" ? "Reconnecting\u2026"
    : status === "connected" ? "Connected"
    : "Disconnected";
  return (
    <DotBadge color={color} aria-live="polite" aria-label={`Connection status: ${label}`}>
      {label}
    </DotBadge>
  );
}

function RouteFallback() {
  return (
    <div className="flex items-center justify-center py-20">
      <Spinner size="lg" />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* App                                                                 */
/* ------------------------------------------------------------------ */

export function App() {
  useSSE();
  const navigate = useNavigate();
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
  useHotkeys("alt+h", () => navigate("/history"), { preventDefault: true });
  useHotkeys("ctrl+comma,meta+comma", () => navigate("/settings"), {
    enableOnFormTags: true,
    preventDefault: true,
  });

  return (
    <div className="flex flex-col h-screen overflow-x-hidden">
      <header className="flex items-center justify-between px-4 h-12 shrink-0 border-b border-border bg-card">
        <Link to="/" className="no-underline flex items-center gap-3.5 hover:opacity-80 transition-opacity">
          <img src="/mark.png" alt="" className="h-8 w-8 object-contain brightness-110 drop-shadow-[0_0_3px_rgba(255,255,255,0.08)]" />
          <span className="font-semibold text-white/95 tracking-tight leading-none">
            CodePlane
          </span>
        </Link>

        <button
          onClick={() => window.dispatchEvent(new CustomEvent("open-command-palette"))}
          className="hidden sm:flex sm:w-72 md:w-96 items-center justify-between gap-3 rounded-lg border border-border bg-background/70 px-4 py-2 text-sm text-muted-foreground shadow-sm transition-colors hover:text-foreground hover:bg-accent"
        >
          <span className="flex items-center gap-2">
            <Search size={14} />
            <span>Search or navigate...</span>
          </span>
          <kbd className="rounded border border-border px-1.5 py-0.5 font-mono text-xs">{modKey}K</kbd>
        </button>

        <div className="flex items-center gap-1">
          <a
            href="https://dfinson.github.io/codeplane"
            target="_blank"
            rel="noreferrer"
            className="hidden sm:flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
          >
            <span>Docs</span>
            <ExternalLink size={13} />
          </a>
          <ConnectionStatusIndicator />
          <NavMenuSlideout />
        </div>
      </header>

      <main className={`flex-1 overflow-y-auto p-3 sm:p-4 md:p-6 ${terminalDrawerOpen ? "min-h-0" : ""}`}>
        <ErrorBoundary>
          <Suspense fallback={<RouteFallback />}>
            <Routes>
              <Route path="/" element={<DashboardScreen />} />
              <Route path="/jobs/new" element={<JobCreationScreen />} />
              <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
              <Route path="/history" element={<HistoryScreen />} />
              <Route path="/analytics" element={<AnalyticsScreen />} />
              <Route path="/settings" element={<SettingsScreen />} />
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
