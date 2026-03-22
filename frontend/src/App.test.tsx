import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useStore } from "./store";
import { App } from "./App";

// Mock heavy child components to isolate App shell behavior
vi.mock("./hooks/useSSE", () => ({ useSSE: () => ({ reconnect: vi.fn() }) }));
vi.mock("./components/DashboardScreen", () => ({
  DashboardScreen: () => <div data-testid="dashboard">Dashboard</div>,
}));
vi.mock("./components/JobDetailScreen", () => ({
  JobDetailScreen: () => <div data-testid="job-detail">JobDetail</div>,
}));
vi.mock("./components/JobCreationScreen", () => ({
  JobCreationScreen: () => <div data-testid="job-creation">JobCreation</div>,
}));
vi.mock("./components/SettingsScreen", () => ({
  SettingsScreen: () => <div data-testid="settings">Settings</div>,
}));
vi.mock("./components/HistoryScreen", () => ({
  HistoryScreen: () => <div data-testid="history">History</div>,
}));
vi.mock("./components/TerminalDrawer", () => ({
  TerminalDrawer: () => <div data-testid="terminal-drawer" />,
}));

function renderApp(route = "/") {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useStore.setState({
    connectionStatus: "connected",
    terminalDrawerOpen: false,
    terminalSessions: {},
  });
});

describe("App", () => {
  it("renders header with CodePlane branding", () => {
    renderApp();
    expect(screen.getByText("CodePlane")).toBeInTheDocument();
  });

  it("renders navigation links for settings and history", () => {
    renderApp();
    expect(screen.getByTitle("Job History")).toBeInTheDocument();
    // Settings link is icon-only; find by href
    const settingsLink = document.querySelector('a[href="/settings"]');
    expect(settingsLink).toBeInTheDocument();
  });

  it("shows connection status from store", () => {
    useStore.setState({ connectionStatus: "connected" });
    renderApp();
    expect(screen.getByText("connected")).toBeInTheDocument();
  });

  it("shows reconnecting status", () => {
    useStore.setState({ connectionStatus: "reconnecting" });
    renderApp();
    expect(screen.getByText("Reconnecting\u2026")).toBeInTheDocument();
  });

  it("routes / to DashboardScreen", () => {
    renderApp("/");
    expect(screen.getByTestId("dashboard")).toBeInTheDocument();
  });

  it("routes /jobs/new to JobCreationScreen", async () => {
    renderApp("/jobs/new");
    await waitFor(() => expect(screen.getByTestId("job-creation")).toBeInTheDocument());
  });

  it("routes /jobs/:jobId to JobDetailScreen", async () => {
    renderApp("/jobs/job-42");
    await waitFor(() => expect(screen.getByTestId("job-detail")).toBeInTheDocument());
  });

  it("routes /settings to SettingsScreen", async () => {
    renderApp("/settings");
    await waitFor(() => expect(screen.getByTestId("settings")).toBeInTheDocument());
  });

  it("routes /history to HistoryScreen", async () => {
    renderApp("/history");
    await waitFor(() => expect(screen.getByTestId("history")).toBeInTheDocument());
  });

  it("toggles terminal drawer on button click", () => {
    renderApp();
    const btn = screen.getByTitle(/terminal/i);
    fireEvent.click(btn);
    expect(useStore.getState().terminalDrawerOpen).toBe(true);
    fireEvent.click(btn);
    expect(useStore.getState().terminalDrawerOpen).toBe(false);
  });

  it("toggles terminal drawer on Ctrl+` shortcut", () => {
    renderApp();
    expect(useStore.getState().terminalDrawerOpen).toBe(false);
    fireEvent.keyDown(window, { key: "`", ctrlKey: true });
    expect(useStore.getState().terminalDrawerOpen).toBe(true);
  });

  it("renders terminal drawer component", () => {
    renderApp();
    expect(screen.getByTestId("terminal-drawer")).toBeInTheDocument();
  });

  it("shows session count in terminal button title", () => {
    useStore.setState({
      terminalSessions: { s1: {} as never, s2: {} as never },
    });
    renderApp();
    const btn = screen.getByTitle(/terminal.*2 sessions/i);
    expect(btn).toBeInTheDocument();
  });
});
