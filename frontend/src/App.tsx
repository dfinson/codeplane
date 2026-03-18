import { Component, type ReactNode, useEffect, useCallback } from "react";
import { Routes, Route, Link } from "react-router-dom";
import { Settings, History, TerminalSquare } from "lucide-react";
import { useSSE } from "./hooks/useSSE";
import { useStore, selectConnectionStatus } from "./store";
import { DashboardScreen } from "./components/DashboardScreen";
import { JobDetailScreen } from "./components/JobDetailScreen";
import { JobCreationScreen } from "./components/JobCreationScreen";
import { SettingsScreen } from "./components/SettingsScreen";
import { HistoryScreen } from "./components/HistoryScreen";
import { TerminalDrawer } from "./components/TerminalDrawer";
import { DotBadge } from "./components/ui/badge";

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

function ConnectionStatus() {
  const status = useStore(selectConnectionStatus);
  const color = status === "connected" ? "green" : status === "reconnecting" ? "yellow" : "red";
  return (
    <DotBadge color={color}>
      {status === "reconnecting" ? "connecting" : status}
    </DotBadge>
  );
}

/* ------------------------------------------------------------------ */
/* App                                                                 */
/* ------------------------------------------------------------------ */

export function App() {
  useSSE();
  const toggleTerminalDrawer = useStore((s) => s.toggleTerminalDrawer);
  const terminalDrawerOpen = useStore((s) => s.terminalDrawerOpen);
  const sessionCount = useStore((s) => Object.keys(s.terminalSessions).length);

  // Ctrl+` keyboard shortcut to toggle the terminal drawer
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === "`") {
        e.preventDefault();
        toggleTerminalDrawer();
      }
    },
    [toggleTerminalDrawer],
  );
  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  return (
    <div className="flex flex-col h-screen">
      <header className="flex items-center justify-between px-4 h-12 shrink-0 border-b border-border bg-card">
        <Link to="/" className="no-underline flex items-center gap-3.5 hover:opacity-80 transition-opacity">
          <img src="/mark.png" alt="" className="h-8 w-8 object-contain brightness-110 drop-shadow-[0_0_3px_rgba(255,255,255,0.08)]" />
          <span className="font-semibold text-white/95 tracking-tight leading-none">
            CodePlane
          </span>
        </Link>

        <div className="flex items-center gap-3 opacity-[0.78]">
          <ConnectionStatus />
          <button
            onClick={toggleTerminalDrawer}
            className={`p-1.5 rounded-md transition-colors ${
              terminalDrawerOpen
                ? "text-foreground bg-accent"
                : "text-muted-foreground hover:text-foreground hover:bg-accent"
            }`}
            title={`Terminal (Ctrl+\`)${sessionCount > 0 ? ` — ${sessionCount} session${sessionCount > 1 ? "s" : ""}` : ""}`}
          >
            <TerminalSquare size={16} />
          </button>
          <Link
            to="/history"
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors no-underline"
            title="Job History"
          >
            <History size={16} />
          </Link>
          <Link
            to="/settings"
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors no-underline"
          >
            <Settings size={16} />
          </Link>
        </div>
      </header>

      <main className={`flex-1 overflow-y-auto p-4 ${terminalDrawerOpen ? "min-h-0" : ""}`}>
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<DashboardScreen />} />
            <Route path="/jobs/new" element={<JobCreationScreen />} />
            <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
            <Route path="/history" element={<HistoryScreen />} />
            <Route path="/settings" element={<SettingsScreen />} />
          </Routes>
        </ErrorBoundary>
      </main>

      <TerminalDrawer />
    </div>
  );
}
