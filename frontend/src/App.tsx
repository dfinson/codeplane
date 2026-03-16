import { Component, type ReactNode } from "react";
import { Routes, Route, Link } from "react-router-dom";
import { Settings, History } from "lucide-react";
import { useSSE } from "./hooks/useSSE";
import { useStore, selectConnectionStatus } from "./store";
import { DashboardScreen } from "./components/DashboardScreen";
import { JobDetailScreen } from "./components/JobDetailScreen";
import { JobCreationScreen } from "./components/JobCreationScreen";
import { SettingsScreen } from "./components/SettingsScreen";
import { HistoryScreen } from "./components/HistoryScreen";
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

  return (
    <div className="flex flex-col h-screen">
      <header className="flex items-center justify-between px-4 h-12 shrink-0 border-b border-border bg-card">
        <Link to="/" className="no-underline flex items-center gap-1.5">
          <img src="/logo-192.png" alt="" className="h-5 w-5" />
          <span className="font-bold text-sm text-foreground tracking-tight cursor-pointer hover:opacity-80">
            CodePlane
          </span>
        </Link>

        <div className="flex items-center gap-2">
          <ConnectionStatus />
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

      <main className="flex-1 overflow-y-auto p-4">
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
    </div>
  );
}
