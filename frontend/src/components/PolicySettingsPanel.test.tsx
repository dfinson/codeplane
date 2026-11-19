import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchPolicySettings, updatePolicyPreset, updatePolicyConfig, updateUsdCeilings } from "../api/client";
import { PolicySettingsPanel } from "./PolicySettingsPanel";

vi.mock("../api/client", () => ({
  fetchPolicySettings: vi.fn(),
  updatePolicyPreset: vi.fn(),
  updatePolicyConfig: vi.fn(),
  updateUsdCeilings: vi.fn(),
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

  it("edits zero/null spend overrides and approval batching without TF", async () => {
    vi.mocked(updateUsdCeilings).mockImplementation(async (ceilings) => ({ ceilings }));
    vi.mocked(updatePolicyConfig).mockResolvedValue({ preset: "supervised", batchWindowSeconds: 10 });
    render(<PolicySettingsPanel />);
    const warning = await screen.findByRole("spinbutton", { name: "Supervised warn USD" });
    expect((warning as HTMLInputElement).value).toBe("20");
    fireEvent.change(warning, { target: { value: "0" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Supervised escalate USD" }), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /save ceilings/i }));
    await waitFor(() => expect(updateUsdCeilings).toHaveBeenCalledWith(expect.objectContaining({
      supervised: { warnUsd: 0, ceilingUsd: null },
    })));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Batch Window (seconds)" }), { target: { value: "10" } });
    await waitFor(() => expect(updatePolicyConfig).toHaveBeenCalledWith({ batchWindowSeconds: 10 }));
    expect(screen.getByText(/Native cost units and unavailable currency are not USD/)).toBeTruthy();
  });

  it("still changes the supported preset", async () => {
    vi.mocked(updatePolicyPreset).mockResolvedValue({ preset: "locked", batchWindowSeconds: 5 });
    render(<PolicySettingsPanel />);
    fireEvent.click(await screen.findByRole("button", { name: /Locked/ }));
    await waitFor(() => expect(updatePolicyPreset).toHaveBeenCalledWith("locked"));
  });
});
