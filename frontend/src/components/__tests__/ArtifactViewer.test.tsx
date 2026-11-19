import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchArtifacts } from "../../api/client";
import { useStore } from "../../store";
import ArtifactViewer from "../ArtifactViewer";

vi.mock("../../api/client", () => ({
  fetchArtifacts: vi.fn(),
  downloadArtifactUrl: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(fetchArtifacts).mockReset();
  useStore.setState({
    jobs: {},
    artifactVersions: {},
  });
});

describe("ArtifactViewer", () => {
  it("does not show a collection spinner or success claim for observation-only history", async () => {
    vi.mocked(fetchArtifacts).mockResolvedValue({ items: [], collectionStatus: "not_applicable" });
    render(<ArtifactViewer jobId="observation" />);
    expect(await screen.findByText("This is observation history, not an executed workspace session.")).toBeInTheDocument();
    expect(screen.queryByText("Collecting artifacts…")).not.toBeInTheDocument();
  });

  it("surfaces durable collection failures when no artifacts were produced", async () => {
    vi.mocked(fetchArtifacts).mockResolvedValue({
      items: [],
      collectionStatus: "failed",
      collectionError: "OSError: workspace disappeared",
      collectionUpdatedAt: "2026-08-02T20:00:00Z",
    });

    render(<ArtifactViewer jobId="job-1" />);

    expect(await screen.findByText("Artifact collection failed")).toBeInTheDocument();
    expect(screen.getByText("OSError: workspace disappeared")).toBeInTheDocument();
  });

  it("refetches after artifacts.updated increments the job version", async () => {
    vi.mocked(fetchArtifacts).mockResolvedValue({
      items: [],
      collectionStatus: "completed",
      collectionError: null,
      collectionUpdatedAt: "2026-08-02T20:00:00Z",
    });

    render(<ArtifactViewer jobId="job-1" />);
    await waitFor(() => expect(fetchArtifacts).toHaveBeenCalledTimes(1));

    act(() => {
      useStore.setState({ artifactVersions: { "job-1": 1 } });
    });

    await waitFor(() => expect(fetchArtifacts).toHaveBeenCalledTimes(2));
    expect(screen.getByText("No artifacts collected")).toBeInTheDocument();
  });
});
