/**
 * Persisted view state store.
 *
 * Stored in localStorage — separate from the main store to avoid serialising
 * the full transcript on every update.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

interface ViewStateStore {
  /** Last seen transcript sequence number per job — drives resume banner. */
  lastSeenSeq: Record<string, number>;
  /** Whether to show step view ("steps") or raw transcript ("raw"). */
  stepViewMode: "steps" | "raw";
  /** Manually collapsed steps per job (stepId array). */
  collapsedSteps: Record<string, string[]>;

  setLastSeenSeq: (jobId: string, seq: number) => void;
  setStepViewMode: (mode: "steps" | "raw") => void;
  toggleCollapsedStep: (jobId: string, stepId: string) => void;
}

export const useViewStateStore = create<ViewStateStore>()(
  persist(
    (set, get) => ({
      lastSeenSeq: {},
      stepViewMode: "steps",
      collapsedSteps: {},

      setLastSeenSeq: (jobId, seq) =>
        set((s) => ({ lastSeenSeq: { ...s.lastSeenSeq, [jobId]: seq } })),

      setStepViewMode: (mode) => set({ stepViewMode: mode }),

      toggleCollapsedStep: (jobId, stepId) => {
        const current = get().collapsedSteps[jobId] ?? [];
        const next = current.includes(stepId)
          ? current.filter((id) => id !== stepId)
          : [...current, stepId];
        set((s) => ({ collapsedSteps: { ...s.collapsedSteps, [jobId]: next } }));
      },
    }),
    { name: "codeplane-view-state" },
  ),
);
