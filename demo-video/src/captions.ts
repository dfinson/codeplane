/**
 * Sparse, prominent scene titles.
 * Used as large overlay text — not rolling captions.
 */

import { SCENES, TRANSITION_FRAMES } from "./constants";

interface Title {
  text: string;
  startFrame: number;
  endFrame: number;
}

// Compute scene start frames accounting for transitions
function sceneStart(index: number): number {
  const keys = Object.keys(SCENES) as (keyof typeof SCENES)[];
  let frame = 0;
  for (let i = 0; i < index; i++) {
    frame += SCENES[keys[i]] - TRANSITION_FRAMES;
  }
  return frame;
}

const S = SCENES;
const keys = Object.keys(S) as (keyof typeof S)[];

export const titles: Title[] = [
  {
    text: "You run 5 agents across 3 repos.\nWhere does the work stand?",
    startFrame: sceneStart(0) + 30,
    endFrame: sceneStart(0) + S.opening - 10,
  },
  {
    text: "Every coding agent is a black box.",
    startFrame: sceneStart(1) + 10,
    endFrame: sceneStart(1) + S.problem - 10,
  },
  {
    text: "See everything. Across every agent.",
    startFrame: sceneStart(2) + 10,
    endFrame: sceneStart(2) + 60,
  },
  {
    text: "Watch it think. Watch it build.",
    startFrame: sceneStart(3) + 10,
    endFrame: sceneStart(3) + 60,
  },
  {
    text: "Stay in the loop\nwithout breaking flow.",
    startFrame: sceneStart(5) + 10,
    endFrame: sceneStart(5) + 60,
  },
  {
    text: "Know what it costs\nand what it's worth.",
    startFrame: sceneStart(6) + 10,
    endFrame: sceneStart(6) + 60,
  },
  {
    text: "Full visibility from anywhere.",
    startFrame: sceneStart(7) + 10,
    endFrame: sceneStart(7) + 60,
  },
];

export function getTitleAtFrame(frame: number): Title | null {
  return titles.find((t) => frame >= t.startFrame && frame < t.endFrame) ?? null;
}
