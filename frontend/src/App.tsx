import { Routes, Route, NavLink } from "react-router-dom";
import { Component, type ReactNode } from "react";
import { useSSE } from "./hooks/useSSE";
import { useTowerStore, selectConnectionStatus } from "./store";
import { DashboardScreen } from "./components/DashboardScreen";
import { JobDetailScreen } from "./components/JobDetailScreen";
import { JobCreationScreen } from "./components/JobCreationScreen";
import { RepositoryDetailView } from "./components/RepositoryDetailView";
import { SettingsScreen } from "./components/SettingsScreen";

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 32, color: "#f85149" }}>
          <h2>Something went wrong</h2>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 13, marginTop: 12, color: "#e6edf3" }}>
            {this.state.error.message}
            {"\n"}
            {this.state.error.stack}
          </pre>
          <button onClick={() => this.setState({ error: null })} style={{ marginTop: 16, padding: "8px 16px", background: "#238636", border: "none", color: "#fff", borderRadius: 6, cursor: "pointer" }}>
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export function App() {
  const connectionStatus = useTowerStore(selectConnectionStatus);

  // Mount global SSE connection
  useSSE();

  return (
    <div className="app-layout">
      {connectionStatus === "disconnected" && (
        <div className="disconnected-banner">
          Connection lost — events may be stale
        </div>
      )}
      <header className="app-header">
        <div className="app-header__title">Tower</div>
        <nav className="app-header__nav">
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/jobs/new">New Job</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
        <div className="app-header__status">
          <span
            className={`status-dot status-dot--${connectionStatus}`}
          />
          {connectionStatus}
        </div>
      </header>
      <main className="app-main">
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<DashboardScreen />} />
            <Route path="/jobs/new" element={<JobCreationScreen />} />
            <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
            <Route
              path="/repos/:repoPath"
              element={<RepositoryDetailView />}
            />
            <Route path="/settings" element={<SettingsScreen />} />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  );
}
