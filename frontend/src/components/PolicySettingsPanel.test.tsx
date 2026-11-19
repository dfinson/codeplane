import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchPolicySettings, updatePolicyPreset } from "../api/client";
import { PolicySettingsPanel } from "./PolicySettingsPanel";

vi.mock("../api/client", () => ({
  fetchPolicySettings: vi.fn(),
  updatePolicyPreset: vi.fn(),
}));
vi.mock("../store", () => ({
  useStore: (select: (state: { policySettingsVersion: number }) => unknown) => select({ policySettingsVersion: 0 }),
}));

describe("final standalone policy settings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchPolicySettings).mockResolvedValue({
      config: { preset: "supervised", batchWindowSeconds: 5 },
      usdCeilings: {
        autonomous: { warnUsd: null, ceilingUsd: null },
        supervised: { warnUsd: 20, ceilingUsd: 40 },
        locked: { warnUsd: 5, ceilingUsd: 10 },
      },
      mcpServers: [],
    });
  });

  it("shows current USD thresholds without exposing retired override or batch editors", async () => {
    render(<PolicySettingsPanel />);
    expect(await screen.findByText("Verified USD thresholds")).toBeTruthy();
    expect(screen.getByText("$20")).toBeTruthy();
    expect(screen.queryAllByRole("spinbutton")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /save ceilings/i })).toBeNull();
    expect(screen.getByText(/Native cost units and unavailable currency are not USD/)).toBeTruthy();
  });

  it("still changes the supported preset", async () => {
    vi.mocked(updatePolicyPreset).mockResolvedValue({ preset: "locked", batchWindowSeconds: 5 });
    render(<PolicySettingsPanel />);
    fireEvent.click(await screen.findByRole("button", { name: /Locked/ }));
    await waitFor(() => expect(updatePolicyPreset).toHaveBeenCalledWith("locked"));
  });
});
