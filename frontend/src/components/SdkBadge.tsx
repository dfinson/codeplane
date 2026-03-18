import { memo } from "react";

const SDK_CONFIG: Record<string, { label: string; className: string }> = {
  copilot: {
    label: "GitHub Copilot",
    className:
      "bg-violet-500/15 text-violet-600 border-violet-500/30 dark:text-violet-400",
  },
  claude: {
    label: "Claude Code",
    className:
      "bg-orange-500/15 text-orange-600 border-orange-500/30 dark:text-orange-400",
  },
};

const DEFAULT_CONFIG = {
  label: (sdk: string) => sdk,
  className: "bg-muted text-muted-foreground border-border",
};

interface SdkBadgeProps {
  sdk: string | undefined;
  /** Use "sm" for job cards, "md" (default) for the detail pane */
  size?: "sm" | "md";
}

export const SdkBadge = memo(function SdkBadge({ sdk, size = "md" }: SdkBadgeProps) {
  if (!sdk) return null;

  const cfg = SDK_CONFIG[sdk];
  const label = cfg?.label ?? DEFAULT_CONFIG.label(sdk);
  const className = cfg?.className ?? DEFAULT_CONFIG.className;

  const sizeClass = size === "sm"
    ? "px-1.5 py-0.5 text-[10px]"
    : "px-2 py-0.5 text-[11px]";

  return (
    <span
      className={`inline-flex items-center rounded-full border font-semibold shrink-0 ${sizeClass} ${className}`}
    >
      {label}
    </span>
  );
});
