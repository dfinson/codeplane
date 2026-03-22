/**
 * TerminalPanel — React component wrapping xterm.js.
 *
 * Renders a full terminal emulator connected to a backend PTY session
 * via WebSocket. Handles initialization, addon loading, auto-resize,
 * and cleanup.
 */

import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { SearchAddon } from "@xterm/addon-search";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { useTerminalSocket } from "../hooks/useTerminalSocket";

interface TerminalPanelProps {
  /** Session ID to attach to. */
  sessionId: string | null;
  /** Called when the underlying shell process exits. */
  onExit?: (code: number) => void;
  /** Additional CSS class for the container. */
  className?: string;
}

export function TerminalPanel({ sessionId, onExit, className }: TerminalPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const [terminal, setTerminal] = useState<Terminal | null>(null);

  // Initialize xterm.js instance
  useEffect(() => {
    if (!containerRef.current) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Menlo, Monaco, monospace",
      theme: {
        background: "#0a0a0f",
        foreground: "#e4e4e7",
        cursor: "#a78bfa",
        selectionBackground: "#4338ca44",
        black: "#18181b",
        red: "#f87171",
        green: "#4ade80",
        yellow: "#fbbf24",
        blue: "#60a5fa",
        magenta: "#c084fc",
        cyan: "#22d3ee",
        white: "#e4e4e7",
        brightBlack: "#52525b",
        brightRed: "#fca5a5",
        brightGreen: "#86efac",
        brightYellow: "#fde68a",
        brightBlue: "#93c5fd",
        brightMagenta: "#d8b4fe",
        brightCyan: "#67e8f9",
        brightWhite: "#fafafa",
      },
      allowProposedApi: true,
    });

    const fitAddon = new FitAddon();
    const searchAddon = new SearchAddon();
    const webLinksAddon = new WebLinksAddon();

    term.loadAddon(fitAddon);
    term.loadAddon(searchAddon);
    term.loadAddon(webLinksAddon);
    term.open(containerRef.current);
    fitAddon.fit();

    termRef.current = term;
    fitAddonRef.current = fitAddon;
    setTerminal(term);

    // Handle container resize via ResizeObserver
    const observer = new ResizeObserver(() => {
      // Small delay to let CSS settle
      requestAnimationFrame(() => fitAddon.fit());
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      term.dispose();
      termRef.current = null;
      fitAddonRef.current = null;
      setTerminal(null);
    };
  }, []);

  // Bridge terminal <-> WebSocket
  useTerminalSocket({ terminal, sessionId, onExit });

  // Re-fit and focus when sessionId changes (switching sessions or first mount)
  useEffect(() => {
    if (fitAddonRef.current) {
      requestAnimationFrame(() => {
        fitAddonRef.current?.fit();
        termRef.current?.focus();
      });
    }
  }, [sessionId]);

  return (
    <div
      ref={containerRef}
      className={className}
      style={{
        width: "100%",
        height: "100%",
        overflow: "hidden",
        backgroundColor: "#0a0a0f",
      }}
    />
  );
}
