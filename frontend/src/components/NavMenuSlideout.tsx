import { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Menu, TerminalSquare, BarChart3, History, Settings, ExternalLink } from "lucide-react";
import { Sheet } from "./ui/sheet";
import { Tooltip } from "./ui/tooltip";
import { useStore } from "../store";
import { modKey } from "../lib/utils";

export function NavMenuSlideout() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const toggleTerminalDrawer = useStore((s) => s.toggleTerminalDrawer);
  const terminalDrawerOpen = useStore((s) => s.terminalDrawerOpen);
  const sessionCount = useStore((s) => Object.keys(s.terminalSessions).length);

  function closeAndNavigate(path: string) {
    setOpen(false);
    navigate(path);
  }

  function handleTerminalClick() {
    setOpen(false);
    toggleTerminalDrawer();
  }

  const isActive = (path: string) =>
    location.pathname === path || location.pathname.startsWith(path + "/");

  return (
    <>
      <Tooltip content="Menu">
        <button
          onClick={() => setOpen(true)}
          aria-label="Open navigation menu"
          aria-expanded={open}
          className={cn(
            "p-2.5 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-md transition-colors",
            open
              ? "text-foreground bg-accent"
              : "text-muted-foreground hover:text-foreground hover:bg-accent",
          )}
        >
          <Menu size={16} />
        </button>
      </Tooltip>

      <Sheet open={open} onClose={() => setOpen(false)} title="Navigation">
        <nav className="flex flex-col gap-1" aria-label="Main navigation">
          <button
            onClick={handleTerminalClick}
            className={cn(
              "w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors",
              terminalDrawerOpen
                ? "text-foreground bg-accent"
                : "text-muted-foreground hover:text-foreground hover:bg-accent",
            )}
          >
            <TerminalSquare size={15} className="shrink-0" />
            <span className="flex-1 text-left font-medium">Terminal</span>
            {sessionCount > 0 && (
              <span className="text-xs text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                {sessionCount}
              </span>
            )}
            <kbd className="hidden sm:inline rounded border border-border px-1.5 py-0.5 font-mono text-xs text-muted-foreground/70">
              {modKey}+`
            </kbd>
          </button>

          <button
            onClick={() => closeAndNavigate("/analytics")}
            className={cn(
              "w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors",
              isActive("/analytics")
                ? "text-foreground bg-accent"
                : "text-muted-foreground hover:text-foreground hover:bg-accent",
            )}
          >
            <BarChart3 size={15} className="shrink-0" />
            <span className="flex-1 text-left font-medium">Analytics</span>
            <kbd className="hidden sm:inline rounded border border-border px-1.5 py-0.5 font-mono text-xs text-muted-foreground/70">
              Alt+A
            </kbd>
          </button>

          <button
            onClick={() => closeAndNavigate("/history")}
            className={cn(
              "w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors",
              isActive("/history")
                ? "text-foreground bg-accent"
                : "text-muted-foreground hover:text-foreground hover:bg-accent",
            )}
          >
            <History size={15} className="shrink-0" />
            <span className="flex-1 text-left font-medium">Job History</span>
            <kbd className="hidden sm:inline rounded border border-border px-1.5 py-0.5 font-mono text-xs text-muted-foreground/70">
              Alt+H
            </kbd>
          </button>

          <div className="my-2 border-t border-border" />

          <button
            onClick={() => closeAndNavigate("/settings")}
            className={cn(
              "w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors",
              isActive("/settings")
                ? "text-foreground bg-accent"
                : "text-muted-foreground hover:text-foreground hover:bg-accent",
            )}
          >
            <Settings size={15} className="shrink-0" />
            <span className="flex-1 text-left font-medium">Settings</span>
            <kbd className="hidden sm:inline rounded border border-border px-1.5 py-0.5 font-mono text-xs text-muted-foreground/70">
              {modKey},
            </kbd>
          </button>

          <a
            href="https://dfinson.github.io/codeplane"
            target="_blank"
            rel="noreferrer"
            onClick={() => setOpen(false)}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors text-muted-foreground hover:text-foreground hover:bg-accent"
          >
            <ExternalLink size={15} className="shrink-0" />
            <span className="flex-1 text-left font-medium">Documentation</span>
          </a>
        </nav>
      </Sheet>
    </>
  );
}
