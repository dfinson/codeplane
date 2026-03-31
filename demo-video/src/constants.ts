/**
 * Central timing constants for the CodePlane demo video.
 * Total duration: 90 seconds = 2700 frames at 30fps.
 *
 * All scene durations are in frames. Transition overlap is 15 frames between scenes.
 */

export const FPS = 30;
export const TOTAL_DURATION_SECONDS = 90;
export const TOTAL_DURATION_FRAMES = TOTAL_DURATION_SECONDS * FPS; // 2700

export const WIDTH = 3840;
export const HEIGHT = 2160;

// Transition overlap in frames (0.5s)
export const TRANSITION_DURATION = 15;

// Scene durations in frames (before accounting for transitions)
// Total scene frames + transitions must equal TOTAL_DURATION_FRAMES
export const SCENES = {
  hook: { durationInFrames: 300, label: "Hook" }, // 0-10s
  problem: { durationInFrames: 360, label: "Problem" }, // 10-22s
  dashboard: { durationInFrames: 390, label: "Dashboard" }, // 22-35s
  createJob: { durationInFrames: 390, label: "Create Job" }, // 35-48s
  liveMonitoring: { durationInFrames: 420, label: "Live Monitoring" }, // 48-62s
  diffReview: { durationInFrames: 360, label: "Diff Review" }, // 62-74s
  analytics: { durationInFrames: 270, label: "Analytics" }, // 74-83s
  closing: { durationInFrames: 315, label: "Closing" }, // 83-90s+
} as const;

// Total raw frames = 2805. With 7 transitions of 15 frames each = 105 overlap.
// Effective duration: 2805 - 105 = 2700 frames = 90s ✓

// Colors - derived from the actual CodePlane UI (dark mode)
export const COLORS = {
  bg: "#0a0a0f",
  bgCard: "#12121a",
  bgCardHover: "#1a1a25",
  primary: "#6366f1", // Indigo-500
  primaryLight: "#818cf8",
  accent: "#22d3ee", // Cyan-400
  accentGreen: "#34d399", // Emerald-400
  text: "#f1f5f9",
  textMuted: "#94a3b8",
  textDim: "#64748b",
  border: "#1e293b",
  danger: "#f87171",
  warning: "#fbbf24",
  success: "#34d399",
} as const;

// Typography
export const FONT = {
  family: "Inter, system-ui, -apple-system, sans-serif",
  mono: "JetBrains Mono, SF Mono, Consolas, monospace",
} as const;
