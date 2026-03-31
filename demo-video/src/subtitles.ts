/**
 * Subtitle data for the CodePlane demo video.
 * Each entry has startMs, endMs, and text.
 * These map directly to the SRT file and are displayed via Remotion's caption system.
 */

export interface SubtitleEntry {
  startMs: number;
  endMs: number;
  text: string;
}

export const SUBTITLES: SubtitleEntry[] = [
  // Scene 1: Hook (0-10s)
  { startMs: 500, endMs: 3000, text: "A control plane for coding agents." },
  { startMs: 3500, endMs: 6000, text: "Run, supervise, and review — from any device." },
  { startMs: 6500, endMs: 9500, text: "Built for headless agent execution." },

  // Scene 2: Problem (10-22s)
  { startMs: 10500, endMs: 13500, text: "Agents run in the dark." },
  { startMs: 14000, endMs: 16500, text: "No visibility into reasoning or tool calls." },
  { startMs: 17000, endMs: 19000, text: "No approval gates for risky operations." },
  { startMs: 19500, endMs: 21500, text: "No way to intervene mid-run." },

  // Scene 3: Dashboard (22-35s)
  { startMs: 22500, endMs: 25000, text: "See every job at a glance." },
  { startMs: 25500, endMs: 28500, text: "Kanban board tracks queued, running, and review states." },
  { startMs: 29000, endMs: 31500, text: "Multiple agents run in parallel." },
  { startMs: 32000, endMs: 34500, text: "Each job runs in its own git worktree." },

  // Scene 4: Create Job (35-48s)
  { startMs: 35500, endMs: 38500, text: "Write a prompt. Pick your agent and model." },
  { startMs: 39000, endMs: 41500, text: "Choose a permission mode." },
  { startMs: 42000, endMs: 44500, text: "Full auto, review & approve, or observe only." },
  { startMs: 45000, endMs: 47500, text: "One click to launch." },

  // Scene 5: Live Monitoring (48-62s)
  { startMs: 48500, endMs: 51000, text: "Watch the agent think in real time." },
  { startMs: 51500, endMs: 54000, text: "Reasoning, tool calls, and plan progress stream live." },
  { startMs: 54500, endMs: 57000, text: "Approval gates pause on risky actions." },
  { startMs: 57500, endMs: 59500, text: "Approve, reject, or trust the session." },
  { startMs: 60000, endMs: 61500, text: "Send operator messages to steer the agent." },

  // Scene 6: Diff Review (62-74s)
  { startMs: 62500, endMs: 65000, text: "Review every change before it lands." },
  { startMs: 65500, endMs: 68000, text: "Syntax-highlighted side-by-side diffs." },
  { startMs: 68500, endMs: 70500, text: "Browse the full workspace, not just changed files." },
  { startMs: 71000, endMs: 73500, text: "Merge, create a PR, or discard." },

  // Scene 7: Analytics (74-83s)
  { startMs: 74500, endMs: 77000, text: "Track token usage and costs across the fleet." },
  { startMs: 77500, endMs: 80000, text: "Compare models by cost and performance." },
  { startMs: 80500, endMs: 82500, text: "No more invisible token burn." },

  // Scene 8: Closing (83-90s)
  { startMs: 83500, endMs: 86000, text: "Headless. Remote-first. Open." },
  { startMs: 86500, endMs: 89500, text: "CodePlane — control your coding agents." },
];
