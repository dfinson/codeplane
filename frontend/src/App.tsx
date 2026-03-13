import { Routes, Route, NavLink } from "react-router-dom";
import { useSSE } from "./hooks/useSSE";
import { useTowerStore, selectConnectionStatus } from "./store";
import { DashboardScreen } from "./components/DashboardScreen";
import { JobDetailScreen } from "./components/JobDetailScreen";
import { JobCreationScreen } from "./components/JobCreationScreen";
import { RepositoryDetailView } from "./components/RepositoryDetailView";
import { SettingsScreen } from "./components/SettingsScreen";

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
      </main>
    </div>
  );
}
