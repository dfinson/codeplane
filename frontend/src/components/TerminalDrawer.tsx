/**
 * TerminalDrawer — persistent bottom drawer that houses terminal sessions.
 *
 * Rendered at the App level (outside <Routes>) so it persists across
 * page navigation. Supports multiple session tabs, resize via drag, and
 * collapse/expand.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Plus, X, Minus, Maximize2, TerminalSquare } from "lucide-react";
import { TerminalPanel } from "./TerminalPanel";
import { useStore } from "../store";
import { useShallow } from "zustand/react/shallow";

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

  const dragging = useRef(false);
  const [maximized, setMaximized] = useState(false);

  const sessionList = Object.values(terminalSessions).filter((s) => !s.jobId);

  // Auto-create a session when the drawer opens with no global sessions
  useEffect(() => {
    if (terminalDrawerOpen && sessionList.length === 0) {
      createTerminalSession();
    }
  }, [terminalDrawerOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  // Handle drag-to-resize
  const onDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragging.current = true;

      const startY = e.clientY;
      const startHeight = terminalDrawerHeight;

      const onMove = (ev: MouseEvent) => {
        if (!dragging.current) return;
        const delta = startY - ev.clientY;
        const maxH = window.innerHeight * MAX_HEIGHT_RATIO;
        const newHeight = Math.min(Math.max(startHeight + delta, MIN_HEIGHT), maxH);
        setTerminalDrawerHeight(newHeight);
      };

      const onUp = () => {
        dragging.current = false;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [terminalDrawerHeight, setTerminalDrawerHeight],
  );

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
        className="h-1 cursor-row-resize hover:bg-primary/30 transition-colors shrink-0"
        onMouseDown={onDragStart}
      />

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
            <span className="max-w-[120px] truncate">
              {session.label || session.cwd?.split("/").pop() || "Terminal"}
            </span>
            <span
              onClick={(e) => handleCloseSession(session.id, e)}
              className="ml-0.5 p-0.5 rounded hover:bg-muted-foreground/20"
            >
              <X size={10} />
            </span>
          </button>
        ))}

        <button
          onClick={handleNewSession}
          className="flex items-center justify-center w-6 h-6 rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors shrink-0"
          title="New terminal session"
        >
          <Plus size={13} />
        </button>

        <div className="flex-1" />

        <button
          onClick={toggleMaximize}
          className="p-1 rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
          title={maximized ? "Restore" : "Maximize"}
        >
          <Maximize2 size={12} />
        </button>
        <button
          onClick={toggleTerminalDrawer}
          className="p-1 rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
          title="Minimize terminal"
        >
          <Minus size={12} />
        </button>
      </div>

      {/* Terminal area */}
      <div className="flex-1 min-h-0">
        {activeTerminalTab && terminalSessions[activeTerminalTab] ? (
          <TerminalPanel
            sessionId={activeTerminalTab}
            onExit={(code) => {
              // Could show a "Process exited" message
              console.log(`Terminal session ${activeTerminalTab} exited with code ${code}`);
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
