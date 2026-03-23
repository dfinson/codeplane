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

async function renderApp(route = "/") {
  const result = render(
    <MemoryRouter initialEntries={[route]}>
      <App />
    </MemoryRouter>,
  );
  await screen.findByTestId("terminal-drawer");
  return result;
}

beforeEach(() => {
  useStore.setState({
    connectionStatus: "connected",
    terminalDrawerOpen: false,
    terminalSessions: {},
    // Pre-mark SDK catalogue as loaded so initSdksAndModels() is a no-op
    // in these tests (they don't test SDK/model loading and have no API mock).
    sdksLoading: false,
  });
});

describe("App", () => {
  it("renders header with CodePlane branding", async () => {
    await renderApp();
    expect(screen.getByText("CodePlane")).toBeInTheDocument();
  });

  it("renders navigation links for settings and history", async () => {
    await renderApp();
    // Nav items are inside the slide-out menu — open it first
    fireEvent.click(screen.getByLabelText("Open navigation menu"));
    expect(screen.getByText("Settings")).toBeInTheDocument();
    expect(screen.getByText("Job History")).toBeInTheDocument();
  });

  it("shows connection status from store", async () => {
    useStore.setState({ connectionStatus: "connected" });
    await renderApp();
    expect(screen.getByText("Connected")).toBeInTheDocument();
  });

  it("shows reconnecting status", async () => {
    useStore.setState({ connectionStatus: "reconnecting" });
    await renderApp();
    expect(screen.getByText("Reconnecting\u2026")).toBeInTheDocument();
  });

  it("routes / to DashboardScreen", async () => {
    await renderApp("/");
    expect(screen.getByTestId("dashboard")).toBeInTheDocument();
  });

  it("routes /jobs/new to JobCreationScreen", async () => {
    await renderApp("/jobs/new");
    await waitFor(() => expect(screen.getByTestId("job-creation")).toBeInTheDocument());
  });

  it("routes /jobs/:jobId to JobDetailScreen", async () => {
    await renderApp("/jobs/job-42");
    await waitFor(() => expect(screen.getByTestId("job-detail")).toBeInTheDocument());
  });

  it("routes /settings to SettingsScreen", async () => {
    await renderApp("/settings");
    await waitFor(() => expect(screen.getByTestId("settings")).toBeInTheDocument());
  });

  it("routes /history to HistoryScreen", async () => {
    await renderApp("/history");
    await waitFor(() => expect(screen.getByTestId("history")).toBeInTheDocument());
  });

  it("toggles terminal drawer on button click", async () => {
    await renderApp();
    // Terminal is inside the slide-out menu — open it, click terminal to open drawer
    fireEvent.click(screen.getByLabelText("Open navigation menu"));
    fireEvent.click(screen.getByText("Terminal").closest("button")!);
    expect(useStore.getState().terminalDrawerOpen).toBe(true);
    // Re-open menu, click terminal again to close drawer
    fireEvent.click(screen.getByLabelText("Open navigation menu"));
    fireEvent.click(screen.getByText("Terminal").closest("button")!);
    expect(useStore.getState().terminalDrawerOpen).toBe(false);
  });

  it("toggles terminal drawer on Ctrl+` shortcut", async () => {
    await renderApp();
    expect(useStore.getState().terminalDrawerOpen).toBe(false);
    fireEvent.keyDown(document, { key: "`", ctrlKey: true });
    expect(useStore.getState().terminalDrawerOpen).toBe(true);
  });

  it("renders terminal drawer component", async () => {
    await renderApp();
    expect(screen.getByTestId("terminal-drawer")).toBeInTheDocument();
  });

  it("shows session count in terminal button title", async () => {
    useStore.setState({
      terminalSessions: { s1: {} as never, s2: {} as never },
    });
    await renderApp();
    // Session count badge is shown inside the Terminal menu item
    fireEvent.click(screen.getByLabelText("Open navigation menu"));
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});
