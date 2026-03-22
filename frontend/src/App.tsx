import { Component, type ReactNode, Suspense, lazy, useEffect, useCallback } from "react";
import { Routes, Route, Link, useNavigate } from "react-router-dom";
import { Settings, History, TerminalSquare, Search } from "lucide-react";
import { CommandPalette } from "./components/CommandPalette";
import { useSSE } from "./hooks/useSSE";
import { useStore, selectConnectionStatus } from "./store";
import { DashboardScreen } from "./components/DashboardScreen";
import { DotBadge } from "./components/ui/badge";
import { Spinner } from "./components/ui/spinner";
import { Tooltip } from "./components/ui/tooltip";

const JobDetailScreen = lazy(() =>
  import("./components/JobDetailScreen").then((module) => ({ default: module.JobDetailScreen })),
);
const JobCreationScreen = lazy(() =>
  import("./components/JobCreationScreen").then((module) => ({ default: module.JobCreationScreen })),
);
const SettingsScreen = lazy(() =>
  import("./components/SettingsScreen").then((module) => ({ default: module.SettingsScreen })),
);
const HistoryScreen = lazy(() =>
  import("./components/HistoryScreen").then((module) => ({ default: module.HistoryScreen })),
);
const TerminalDrawer = lazy(() =>
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
  render() {
    if (this.state.error) {
      return (
        <div className="p-8 max-w-2xl mx-auto">
          <p className="text-lg font-semibold text-red-400 mb-2">Something went wrong</p>
          <pre className="text-xs text-muted-foreground whitespace-pre-wrap bg-card rounded-lg p-4 border border-border overflow-auto">
            {this.state.error.message}{"\n"}{this.state.error.stack}
          </pre>
          <button
            onClick={() => this.setState({ error: null })}
            className="mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90"
          >
            Try again
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
  const sessionCount = useStore((s) => Object.keys(s.terminalSessions).length);

  // Global keyboard shortcuts
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === "`") {
        e.preventDefault();
        if (!terminalDrawerOpen) {
          // Drawer is closed — open it
          toggleTerminalDrawer();
        } else {
          // Drawer is open — close only if focus is already inside the terminal,
          // otherwise just bring focus to the terminal (xterm textarea/canvas)
          const active = document.activeElement;
          const terminalEl = document.querySelector(".xterm-helper-textarea, .xterm canvas");
          const focusedInTerminal = terminalEl && (active === terminalEl || terminalEl.contains(active));
          if (focusedInTerminal) {
            toggleTerminalDrawer();
          } else {
            (terminalEl as HTMLElement | null)?.focus();
          }
        }
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "n") {
        e.preventDefault();
        navigate("/jobs/new");
      }
      if ((e.metaKey || e.ctrlKey) && e.key === ",") {
        e.preventDefault();
        navigate("/settings");
      }
    },
    [toggleTerminalDrawer, terminalDrawerOpen, navigate],
  );
  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

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
          className="hidden sm:flex min-w-56 items-center justify-between gap-3 rounded-lg border border-border bg-background/70 px-4 py-2 text-sm text-muted-foreground shadow-sm transition-colors hover:text-foreground hover:bg-accent"
        >
          <span className="flex items-center gap-2">
            <Search size={14} />
            <span>Search all jobs…</span>
          </span>
          <kbd className="rounded border border-border px-1.5 py-0.5 font-mono text-xs">⌘K</kbd>
        </button>

        <div className="flex items-center gap-1 opacity-[0.78]">
          <ConnectionStatusIndicator />
          <Tooltip content={`Terminal (Ctrl+\`)${sessionCount > 0 ? ` — ${sessionCount} session${sessionCount > 1 ? "s" : ""}` : ""}`}>
            <button
              onClick={toggleTerminalDrawer}
              aria-label="Toggle terminal"
              title={`Terminal (Ctrl+\`)${sessionCount > 0 ? ` — ${sessionCount} session${sessionCount > 1 ? "s" : ""}` : ""}`}
              className={`p-2.5 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-md transition-colors ${
                terminalDrawerOpen
                  ? "text-foreground bg-accent"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent"
              }`}
            >
              <TerminalSquare size={16} />
            </button>
          </Tooltip>
          <Tooltip content="Job history">
            <Link
              to="/history"
              aria-label="Job history"
              title="Job History"
              className="p-2.5 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors no-underline"
            >
              <History size={16} />
            </Link>
          </Tooltip>
          <Tooltip content="Settings (⌘,)">
            <Link
              to="/settings"
              aria-label="Settings (⌘,)"
              className="p-2.5 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors no-underline"
            >
              <Settings size={16} />
            </Link>
          </Tooltip>
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
