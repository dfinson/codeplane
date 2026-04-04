/**
 * Timing, colors, and layout constants for the CodePlane demo video.
 *
 * Composition: 3840×2160 at 30 fps.
 * Target duration: ~45 seconds.
 */

export const FPS = 30;
export const WIDTH = 3840;
export const HEIGHT = 2160;
export const TRANSITION_FRAMES = 12;

// Scene durations in frames
export const SCENES = {
  opening: 120,        // 4.0s — logo + hook question
  problem: 90,         // 3.0s — "every agent is a black box"
  dashboard: 210,      // 7.0s — real dashboard screenshot
  liveExecution: 210,  // 7.0s — running job transcript
  planDiff: 180,       // 6.0s — plan tab + diff viewer
  approval: 150,       // 5.0s — approval flow
  analytics: 180,      // 6.0s — analytics scorecard + models
  mobile: 150,         // 5.0s — mobile showcase
  closing: 120,        // 4.0s — logo + URL
} as const;

export const TOTAL_FRAMES = Object.values(SCENES).reduce((a, b) => a + b, 0)
  - TRANSITION_FRAMES * (Object.keys(SCENES).length - 1);

// Colors — extracted from frontend/src/index.css
export const C = {
  bg: "hsl(220 20% 7%)",
  card: "hsl(215 22% 11%)",
  border: "hsl(215 12% 21%)",
  fg: "hsl(213 27% 90%)",
  muted: "hsl(215 12% 57%)",
  primary: "hsl(217 91% 60%)",
  white: "#ffffff",
  // Brand
  claude: "#D97757",
  copilot: "#8534F3",
} as const;

// Phone frame dimensions for mobile scene (iPhone 14 Pro proportions)
export const PHONE = {
  width: 440,
  height: 900,
  radius: 48,
  bezel: 8,
} as const;
