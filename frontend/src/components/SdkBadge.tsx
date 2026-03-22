import { memo, type ReactElement } from "react";
import { siGithubcopilot, siClaude } from "simple-icons";

function SimpleIcon({ icon, size }: { icon: { path: string }; size: number }): ReactElement {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
      style={{ display: "inline", flexShrink: 0 }}
    >
      <path d={icon.path} />
    </svg>
  );
}

type SdkIconComponent = (props: { size: number }) => ReactElement;

const SDK_CONFIG: Record<
  string,
  { label: string; className: string; Icon: SdkIconComponent }
> = {
  copilot: {
    label: "GitHub Copilot",
    className:
      "bg-violet-500/15 text-violet-600 border-violet-500/30 dark:text-violet-400",
    Icon: ({ size }) => <SimpleIcon icon={siGithubcopilot} size={size} />,
  },
  claude: {
    label: "Claude Code",
    className:
      "bg-orange-500/15 text-orange-600 border-orange-500/30 dark:text-orange-400",
    Icon: ({ size }) => <SimpleIcon icon={siClaude} size={size} />,
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
  const Icon = cfg?.Icon;

  const sizeClass =
    size === "sm" ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-0.5 text-[11px]";
  const iconSize = size === "sm" ? 9 : 11;

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border font-semibold shrink-0 ${sizeClass} ${className}`}
    >
      {Icon && <Icon size={iconSize} />}
      {label}
    </span>
  );
});
