/**
 * TerminalDrawer — persistent bottom drawer that houses terminal sessions.
 *
 * Rendered at the App level (outside <Routes>) so it persists across
 * page navigation. Supports multiple session tabs, resize via drag, and
 * collapse/expand.
 */

import { useCallback, useEffect, useState } from "react";
import { Plus, X, Minus, Maximize2, TerminalSquare, GitBranch } from "lucide-react";
import { TerminalPanel } from "./TerminalPanel";
import { useStore } from "../store";
import { useShallow } from "zustand/react/shallow";
import { Tooltip } from "./ui/tooltip";
import { useDrag } from "../hooks/useDrag";

const MIN_HEIGHT = 150;
const DEFAULT_HEIGHT = 300;
const MAX_HEIGHT_RATIO = 0.7;

export function TerminalDrawer() {
  const {
    terminalDrawerOpen,
    terminalSessions,
    activeTerminalTab,
    terminalDrawerHeight,
    toggleTerminalDrawer,
    setActiveTerminalTab,
    removeTerminalSession,
    setTerminalDrawerHeight,
    createTerminalSession,
  } = useStore(useShallow((s) => ({
    terminalDrawerOpen: s.terminalDrawerOpen,
    terminalSessions: s.terminalSessions,
    activeTerminalTab: s.activeTerminalTab,
    terminalDrawerHeight: s.terminalDrawerHeight,
    toggleTerminalDrawer: s.toggleTerminalDrawer,
    setActiveTerminalTab: s.setActiveTerminalTab,
    removeTerminalSession: s.removeTerminalSession,
    setTerminalDrawerHeight: s.setTerminalDrawerHeight,
    createTerminalSession: s.createTerminalSession,
  })));

  const [maximized, setMaximized] = useState(false);

  const sessionList = Object.values(terminalSessions);

  // Auto-create a session when the drawer opens with no sessions at all
  useEffect(() => {
    if (terminalDrawerOpen && sessionList.length === 0) {
      createTerminalSession();
    }
  }, [terminalDrawerOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  // Handle drag-to-resize
  const dragHandlers = useDrag({
    axis: "y",
    onDrag: (delta) => {
      const maxH = window.innerWidth < 640
        ? window.innerHeight * 0.5
        : window.innerHeight * MAX_HEIGHT_RATIO;
      setTerminalDrawerHeight(Math.min(Math.max(terminalDrawerHeight + delta, MIN_HEIGHT), maxH));
    },
  });

  const handleNewSession = useCallback(() => {
    createTerminalSession();
  }, [createTerminalSession]);

  const handleCloseSession = useCallback(
    (id: string, e: React.MouseEvent) => {
      e.stopPropagation();
      removeTerminalSession(id);
    },
    [removeTerminalSession],
  );

  const toggleMaximize = useCallback(() => {
    if (maximized) {
      setTerminalDrawerHeight(DEFAULT_HEIGHT);
    } else {
      setTerminalDrawerHeight(window.innerHeight * MAX_HEIGHT_RATIO);
    }
    setMaximized(!maximized);
  }, [maximized, setTerminalDrawerHeight]);

  if (!terminalDrawerOpen) return null;

  const height = terminalDrawerHeight || DEFAULT_HEIGHT;

  return (
    <div
      className="border-t border-border bg-card flex flex-col shrink-0"
      style={{ height }}
    >
      {/* Drag handle */}
      <div
        className="h-3 cursor-row-resize hover:bg-primary/30 transition-colors shrink-0 flex items-center justify-center touch-none"
        {...dragHandlers}
      >
        <div className="w-8 h-0.5 bg-muted-foreground/30 rounded-full" />
      </div>

      {/* Tab bar */}
      <div className="flex items-center h-8 shrink-0 border-b border-border px-1 gap-0.5 overflow-x-auto">
        {sessionList.map((session) => (
          <button
            key={session.id}
            onClick={() => setActiveTerminalTab(session.id)}
            className={`flex items-center gap-1.5 px-2.5 h-7 rounded-sm text-xs font-medium transition-colors shrink-0 ${
              activeTerminalTab === session.id
                ? "bg-accent text-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
            }`}
          >
            <TerminalSquare size={12} />
            {session.jobId && (
              <GitBranch size={9} className="text-muted-foreground/60 shrink-0 -mr-0.5" />
            )}
            <span className="max-w-[120px] truncate">
              {session.label || session.cwd?.split("/").pop() || "Terminal"}
            </span>
            <span
              onClick={(e) => handleCloseSession(session.id, e)}
              className="ml-0.5 p-1.5 rounded hover:bg-muted-foreground/20"
            >
              <X size={12} />
            </span>
          </button>
        ))}

        <Tooltip content="New terminal session">
          <button
            onClick={handleNewSession}
            className="flex items-center justify-center w-9 h-9 rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors shrink-0"
          >
            <Plus size={13} />
          </button>
        </Tooltip>

        <div className="flex-1" />

        <Tooltip content={maximized ? "Restore" : "Maximize"}>
          <button
            onClick={toggleMaximize}
            className="p-2 rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
          >
            <Maximize2 size={12} />
          </button>
        </Tooltip>
        <Tooltip content="Minimize terminal">
          <button
            onClick={toggleTerminalDrawer}
            className="p-2 rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
          >
            <Minus size={12} />
          </button>
        </Tooltip>
      </div>

      {/* Terminal area */}
      <div className="flex-1 min-h-0">
        {activeTerminalTab && terminalSessions[activeTerminalTab] ? (
          <TerminalPanel
            sessionId={activeTerminalTab}
            onExit={() => {
              // Terminal process exited — no action needed
            }}
          />
        ) : sessionList.length === 0 ? (
          <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
            <button onClick={handleNewSession} className="flex items-center gap-2 hover:text-foreground transition-colors">
              <Plus size={14} />
              Create a terminal session
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
