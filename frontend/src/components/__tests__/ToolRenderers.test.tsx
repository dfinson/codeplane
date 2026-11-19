/**
 * Regression tests for PowerShell/pwsh tool card rendering.
 *
 * Verifies that expanded tool cards show: command + intent + output.
 * Verifies collapsed cards remain concise (toolDisplay or toolIntent label).
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  StructuredToolContent,
  ToolDetail,
  ToolStep,
  hasStructuredRenderer,
} from "../ToolRenderers";
import { classifyTool } from "../CuratedFeedLogic";
import type { TranscriptEntry } from "../../store";

// Mock the store to provide worktreeRoot
vi.mock("../../store", async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    useStore: vi.fn((selector: (state: unknown) => unknown) =>
      selector({ jobs: { "j1": { worktreePath: "/workspace" } } })
    ),
  };
});

function mkEntry(overrides: Partial<TranscriptEntry>): TranscriptEntry {
  return {
    jobId: "j1",
    eventId: "evt-1",
    timestamp: new Date().toISOString(),
    kind: "tool.call.completed",
    content: "",
    toolName: "powershell",
    arguments: JSON.stringify({ command: "Get-ChildItem -Recurse", description: "List all files recursively" }),
    result: "Directory: C:\\project\n\nMode  LastWriteTime  Length Name\n----  -------------  ------ ----\nd---- 2026-01-01     0      src",
    success: true,
    toolDisplay: "$ Get-ChildItem -Recurse",
    toolIntent: "List all files recursively",
    ...overrides,
  };
}

it("renders the failed wire outcome even without a false success field", () => {
  const { container } = render(<ToolStep entry={mkEntry({
    kind: "tool.call.failed", toolIssue: "Permission denied", success: undefined,
  })} isActive />);
  expect(screen.getByText("Permission denied")).toBeInTheDocument();
  expect(container.querySelector(".bg-blue-400")).toBeNull();
});

it("shows an unresolved imported start without a running indicator or invented result", () => {
  const { container } = render(<ToolStep entry={mkEntry({
    kind: "tool.call.started", pairingStatus: "unresolved", pairingReason: "missing_completion",
    result: undefined, success: undefined,
  })} isActive />);
  expect(screen.getByText("Outcome unrecorded")).toBeInTheDocument();
  expect(container.querySelector(".bg-blue-400")).toBeNull();
});

// ---------------------------------------------------------------------------
// hasStructuredRenderer
// ---------------------------------------------------------------------------

describe("hasStructuredRenderer", () => {
  it("recognises powershell", () => {
    expect(hasStructuredRenderer("powershell")).toBe(true);
  });

  it("recognises pwsh", () => {
    expect(hasStructuredRenderer("pwsh")).toBe(true);
  });

  it("still recognises bash", () => {
    expect(hasStructuredRenderer("bash")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// classifyTool (CuratedFeedLogic)
// ---------------------------------------------------------------------------

describe("classifyTool", () => {
  it("classifies powershell as execute", () => {
    expect(classifyTool("powershell")).toBe("execute");
  });

  it("classifies pwsh as execute", () => {
    expect(classifyTool("pwsh")).toBe("execute");
  });

  it("classifies bash as execute", () => {
    expect(classifyTool("bash")).toBe("execute");
  });
});

// ---------------------------------------------------------------------------
// StructuredToolContent — expanded card content
// ---------------------------------------------------------------------------

describe("StructuredToolContent — PowerShell", () => {
  it("shows command text for powershell tool", () => {
    const entry = mkEntry({ toolName: "powershell" });
    render(<StructuredToolContent entry={entry} />);

    // The command should be visible as-is (not hidden behind hover)
    expect(screen.getByText("Get-ChildItem -Recurse")).toBeInTheDocument();
    // The $ prompt prefix should be visible
    expect(screen.getByText("$")).toBeInTheDocument();
  });

  it("shows description/intent when present", () => {
    const entry = mkEntry({ toolName: "powershell" });
    render(<StructuredToolContent entry={entry} />);

    expect(screen.getByText("List all files recursively")).toBeInTheDocument();
  });

  it("shows output in code block", () => {
    const entry = mkEntry({ toolName: "powershell" });
    render(<StructuredToolContent entry={entry} />);

    // Output should contain directory listing
    expect(screen.getByText(/Directory: C:\\project/)).toBeInTheDocument();
  });

  it("shows command text for pwsh tool", () => {
    const entry = mkEntry({
      toolName: "pwsh",
      arguments: JSON.stringify({ command: "Write-Host 'hello'" }),
    });
    render(<StructuredToolContent entry={entry} />);

    expect(screen.getByText("Write-Host 'hello'")).toBeInTheDocument();
  });

  it("omits description block when not present", () => {
    const entry = mkEntry({
      toolName: "powershell",
      arguments: JSON.stringify({ command: "Get-Process" }),
    });
    render(<StructuredToolContent entry={entry} />);

    // Command should still show
    expect(screen.getByText("Get-Process")).toBeInTheDocument();
  });

  it("preserves multiline commands", () => {
    const multilineCmd = "Get-Process |\n  Where-Object { $_.CPU -gt 100 } |\n  Sort-Object CPU";
    const entry = mkEntry({
      toolName: "powershell",
      arguments: JSON.stringify({ command: multilineCmd }),
    });
    const { container } = render(<StructuredToolContent entry={entry} />);

    // The span has whitespace-pre-wrap and contains the full multiline command
    const cmdSpan = container.querySelector(".whitespace-pre-wrap");
    expect(cmdSpan).toBeInTheDocument();
    expect(cmdSpan!.textContent).toBe(multilineCmd);
  });

  it("shows error styling for failed commands", () => {
    const entry = mkEntry({
      toolName: "powershell",
      success: false,
      result: "CommandNotFoundException: The term 'Bad-Command' is not recognized",
    });
    const { container } = render(<StructuredToolContent entry={entry} />);

    // Should have the red error background class
    const commandBlock = container.querySelector(".bg-red-950\\/30");
    expect(commandBlock).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// ToolDetail — full expanded detail including fallback
// ---------------------------------------------------------------------------

describe("ToolDetail — PowerShell expanded card", () => {
  it("uses structured renderer, not raw JSON fallback", () => {
    const entry = mkEntry({ toolName: "powershell" });
    render(<ToolDetail entry={entry} />);

    // Should show the command, NOT raw JSON arguments
    expect(screen.getByText("Get-ChildItem -Recurse")).toBeInTheDocument();
    // Should NOT show a prettified JSON "Input" block (that's the fallback path)
    expect(screen.queryByText("Input")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// ToolStep — collapsed vs expanded
// ---------------------------------------------------------------------------

describe("ToolStep — collapsed/expanded", () => {
  it("collapsed card shows concise label from toolIntent", () => {
    const entry = mkEntry({
      toolName: "powershell",
      toolIntent: "List all files recursively",
    });
    render(<ToolStep entry={entry} isActive={false} />);

    // Collapsed shows the intent as label
    expect(screen.getByText("List all files recursively")).toBeInTheDocument();
    // Should NOT show the full command in collapsed state
    expect(screen.queryByText("Get-ChildItem -Recurse")).not.toBeInTheDocument();
  });

  it("expanded card shows command + intent + output", () => {
    const entry = mkEntry({
      toolName: "powershell",
      toolIntent: "List all files recursively",
    });
    render(<ToolStep entry={entry} isActive={false} />);

    // Click to expand
    const button = screen.getByRole("button");
    fireEvent.click(button);

    // Now the command should be visible
    expect(screen.getByText("Get-ChildItem -Recurse")).toBeInTheDocument();
    // Intent/description should be visible
    expect(screen.getAllByText("List all files recursively").length).toBeGreaterThanOrEqual(1);
  });

  it("collapsed card falls back to toolDisplay when no intent", () => {
    const entry = mkEntry({
      toolName: "powershell",
      toolIntent: undefined,
      toolDisplay: "$ Get-ChildItem -Recurse",
    });
    render(<ToolStep entry={entry} isActive={false} />);

    // Collapsed shows toolDisplay
    expect(screen.getByText("$ Get-ChildItem -Recurse")).toBeInTheDocument();
  });
});
