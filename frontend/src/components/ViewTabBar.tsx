import type { ReactNode } from "react";
import { type LucideIcon, Radio, TerminalSquare, FolderTree, BarChart3, Package, ScanSearch } from "lucide-react";
import { Tooltip } from "./ui/tooltip";
import { cn } from "../lib/utils";

const DEFAULT_TAB_ITEMS = [
  { id: "live", icon: Radio, label: "Live", tip: "Real-time agent activity feed" },
  { id: "files", icon: FolderTree, label: "Files", tip: "Browse the workspace file tree" },
  { id: "review", icon: ScanSearch, label: "Review", tip: "Review code changes and the agent story", conditional: true },
  { id: "metrics", icon: BarChart3, label: "Metrics", tip: "Cost, tokens, and performance metrics" },
  { id: "artifacts", icon: Package, label: "Artifacts", tip: "Checkpoints, logs, and exported files", conditional: true },
] as const;

export interface ViewTabBarItem {
  id: string;
  label: string;
  icon?: LucideIcon;
  tip?: string;
  hidden?: boolean;
  disabled?: boolean;
  disabledTip?: string;
  badgeCount?: number;
}

interface ViewTabBarProps {
  activeTab: string;
  onTabChange: (tab: string) => void;
  hasChanges?: boolean;
  hasRecipe?: boolean;
  hasArtifacts?: boolean;
  artifactCount?: number;
  hasWorktree?: boolean;
  jobTerminalCount?: number;
  onOpenTerminal?: () => void;
  items?: ViewTabBarItem[];
  variant?: "card" | "inline";
  mobileBehavior?: "hidden" | "visible";
  className?: string;
  itemClassName?: string;
  ariaLabel?: string;
}

function wrapWithTooltip(content: string | undefined, child: ReactNode, key: string) {
  if (!content) return child;
  return (
    <Tooltip key={key} content={content} side="bottom">
      {child}
    </Tooltip>
  );
}

export function ViewTabBar({
  activeTab,
  onTabChange,
  hasChanges = false,
  hasRecipe = false,
  hasArtifacts = false,
  artifactCount = 0,
  hasWorktree = false,
  jobTerminalCount = 0,
  onOpenTerminal,
  items,
  variant = "card",
  mobileBehavior = "hidden",
  className,
  itemClassName,
  ariaLabel,
}: ViewTabBarProps) {
  const visibleTabs: ViewTabBarItem[] = items
    ? items.filter((item) => !item.hidden)
    : DEFAULT_TAB_ITEMS.filter((tab) => {
        if (tab.id === "review") return hasChanges;
        if (tab.id === "artifacts") return hasArtifacts;
        return true;
      }).map((tab) => ({
        id: tab.id,
        label: tab.id === "live" && hasRecipe ? "Run" : tab.label,
        icon: tab.icon,
        tip: tab.id === "live" && hasRecipe ? "Pinned recipe steps, attempts and evidence" : tab.tip,
        badgeCount: tab.id === "artifacts" ? artifactCount : undefined,
      }));

  const containerClassName = cn(
    "flex items-center overflow-x-auto scrollbar-none shrink-0",
    mobileBehavior === "hidden" ? "hidden md:flex" : "flex",
    variant === "card"
      ? "gap-0.5 mx-3 mt-1.5 px-3 h-9 rounded-lg border border-border bg-card"
      : "gap-1 border-b border-border",
    className,
  );

  return (
    <div
      className={containerClassName}
      role={ariaLabel ? "navigation" : undefined}
      aria-label={ariaLabel}
    >
      {visibleTabs.map(({ id, icon: Icon, label, tip, disabled = false, disabledTip, badgeCount }) => {
        const tooltip = disabled ? disabledTip ?? tip : tip;
        const isActive = activeTab === id;
        const button = (
          <button
            key={id}
            type="button"
            aria-current={isActive ? "page" : undefined}
            aria-disabled={disabled || undefined}
            disabled={disabled}
            onClick={() => {
              if (!disabled) onTabChange(id);
            }}
            className={cn(
              "flex items-center gap-1.5 whitespace-nowrap relative transition-colors",
              variant === "card"
                ? "px-2.5 py-1.5 rounded-md text-xs font-medium"
                : "px-3 py-2 text-xs font-medium border-b-2 -mb-px",
              isActive
                ? variant === "card"
                  ? "text-primary"
                  : "border-primary text-foreground"
                : variant === "card"
                  ? "text-muted-foreground hover:text-foreground hover:bg-accent/50"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              disabled && "cursor-not-allowed text-muted-foreground/60 hover:bg-transparent hover:text-muted-foreground/60",
              itemClassName,
            )}
          >
            {Icon && <Icon size={variant === "card" ? 14 : 13} className="shrink-0" />}
            <span>{label}</span>
            {badgeCount && badgeCount > 0 && (
              <span className="text-[9px] leading-none bg-muted text-muted-foreground rounded-full px-1 py-0.5 font-normal">
                {badgeCount}
              </span>
            )}
            {variant === "card" && isActive && <span className="absolute bottom-0 left-2 right-2 h-0.5 bg-primary rounded-full" />}
          </button>
        );

        return wrapWithTooltip(tooltip, button, id);
      })}
      {!items && hasWorktree && onOpenTerminal && (
        <>
          <div className="w-px h-4 bg-border mx-1" />
          <Tooltip content="Open a shell in the job's worktree" side="bottom">
            <button
              type="button"
              onClick={onOpenTerminal}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors whitespace-nowrap"
            >
              <TerminalSquare size={14} className="shrink-0" />
              <span>Terminal</span>
              {jobTerminalCount > 0 && (
                <span className="text-[9px] font-bold text-primary">{jobTerminalCount}</span>
              )}
            </button>
          </Tooltip>
        </>
      )}
    </div>
  );
}
