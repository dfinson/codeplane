import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const isMac =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad|iPod/.test(navigator.platform);

/** Return the platform-appropriate modifier key label: ⌘ on Mac, Ctrl on others. */
export const modKey = isMac ? "⌘" : "Ctrl";

/** Steps that indicate active setup is still in progress. */
const ACTIVE_SETUP_STEPS = new Set([
  "creating_workspace",
  "configuring_policy",
  "exploring_codebase",
  "starting_agent",
]);

/** Human-readable label for each setup step. */
const SETUP_STEP_LABELS: Record<string, string> = {
  creating_workspace: "Creating workspace…",
  configuring_policy: "Configuring permissions…",
  exploring_codebase: "Exploring codebase…",
  starting_agent: "Starting agent…",
};

/** Whether a setup step indicates work is still in progress. */
export function isActiveSetupStep(step: string | null | undefined): boolean {
  return !!step && ACTIVE_SETUP_STEPS.has(step);
}

/** Display label for the current setup step. */
export function setupStepLabel(step: string | null | undefined): string {
  if (!step) return "Setting up…";
  return SETUP_STEP_LABELS[step] ?? "Setting up…";
}
