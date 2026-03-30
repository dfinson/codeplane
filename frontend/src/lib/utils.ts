import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const isMac =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad|iPod/.test(navigator.platform);

/** Return the platform-appropriate modifier key label: ⌘ on Mac, Ctrl on others. */
export const modKey = isMac ? "⌘" : "Ctrl";
