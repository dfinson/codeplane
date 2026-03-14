import { Component, type ReactNode } from "react";
import { Routes, Route, NavLink, Link } from "react-router-dom";
import { Group, Badge, UnstyledButton, Text } from "@mantine/core";
import { type LucideIcon, LayoutDashboard, Plus, Settings } from "lucide-react";
import { useSSE } from "./hooks/useSSE";
import { useTowerStore, selectConnectionStatus } from "./store";
import { DashboardScreen } from "./components/DashboardScreen";
import { JobDetailScreen } from "./components/JobDetailScreen";
import { JobCreationScreen } from "./components/JobCreationScreen";
import { SettingsScreen } from "./components/SettingsScreen";

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
          <Text size="lg" fw={600} c="red" mb="sm">Something went wrong</Text>
          <pre className="text-xs text-[var(--mantine-color-dimmed)] whitespace-pre-wrap bg-[var(--mantine-color-dark-7)] rounded-lg p-4 border border-[var(--mantine-color-dark-4)] overflow-auto">
            {this.state.error.message}{"\n"}{this.state.error.stack}
          </pre>
          <UnstyledButton
            onClick={() => this.setState({ error: null })}
            className="mt-4 px-4 py-2 bg-[var(--mantine-color-blue-7)] text-white rounded-md text-sm font-medium hover:bg-[var(--mantine-color-blue-6)]"
          >
            Try again
          </UnstyledButton>
        </div>
      );
    }
    return this.props.children;
  }
}

/* ------------------------------------------------------------------ */
/* Nav link component                                                  */
/* ------------------------------------------------------------------ */

function NavItem({
  to,
  icon: Icon,
  label,
  end,
}: {
  to: string;
  icon: LucideIcon;
  label: string;
  end?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        `flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium transition-colors no-underline ${
          isActive
            ? "bg-[var(--mantine-color-dark-5)] text-white"
            : "text-[var(--mantine-color-dimmed)] hover:text-white hover:bg-[var(--mantine-color-dark-6)]"
        }`
      }
    >
      <Icon size={16} />
      <span className="hidden sm:inline">{label}</span>
    </NavLink>
  );
}

/* ------------------------------------------------------------------ */
/* Connection status indicator                                         */
/* ------------------------------------------------------------------ */

function ConnectionStatus() {
  const status = useTowerStore(selectConnectionStatus);
  const color =
    status === "connected" ? "green" : status === "reconnecting" ? "yellow" : "red";
  return (
    <Badge
      variant="dot"
      color={color}
      size="sm"
      className="cursor-default select-none"
    >
      {status === "reconnecting" ? "connecting" : status}
    </Badge>
  );
}

/* ------------------------------------------------------------------ */
/* App                                                                 */
/* ------------------------------------------------------------------ */

export function App() {
  useSSE();

  return (
    <div className="flex flex-col h-screen">
      <header className="flex items-center justify-between px-4 h-12 shrink-0 border-b border-[var(--mantine-color-dark-4)] bg-[var(--mantine-color-dark-7)]">
        <Link to="/" className="no-underline">
          <Text fw={700} size="md" c="white" className="tracking-tight cursor-pointer hover:opacity-80">
            Tower
          </Text>
        </Link>

        <Group gap="sm">
          <NavItem to="/" icon={LayoutDashboard} label="Dashboard" end />
          <NavItem to="/jobs/new" icon={Plus} label="New Job" />
          <NavItem to="/settings" icon={Settings} label="Settings" />
        </Group>

        <ConnectionStatus />
      </header>

      <main className="flex-1 overflow-y-auto p-4">
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<DashboardScreen />} />
            <Route path="/jobs/new" element={<JobCreationScreen />} />
            <Route path="/jobs/:jobId" element={<JobDetailScreen />} />
            <Route path="/settings" element={<SettingsScreen />} />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  );
}
