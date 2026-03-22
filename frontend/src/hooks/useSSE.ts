/**
 * SSE client with exponential backoff reconnection.
 *
 * Connects to /api/events on mount, dispatches events to the Zustand store,
 * and handles reconnection with a Last-Event-ID query parameter (EventSource
 * does not support custom request headers).
 */

import { useCallback, useEffect, useRef } from "react";
import { useStore } from "../store";

/** Reconnection parameters per SPEC §3.5 */
const INITIAL_DELAY_MS = 1000;
const BACKOFF_MULTIPLIER = 2;
const MAX_DELAY_MS = 30_000;
const JITTER_MS = 500;
const MAX_ATTEMPTS = 20;

function jitter(): number {
  return Math.round((Math.random() - 0.5) * 2 * JITTER_MS);
}

export function useSSE(jobId?: string): { reconnect: () => void } {
  const lastEventIdRef = useRef<string | null>(null);
  const attemptRef = useRef(0);
  const connectRef = useRef<(() => void) | null>(null);
  const wasConnectedRef = useRef(false);

  useEffect(() => {
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const { setConnectionStatus, setReconnectAttempt, dispatchSSEEvent } =
      useStore.getState();

    function connect() {
      if (disposed) return;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      es?.close();
      es = null;
      connectRef.current = connect;

      let url = "/api/events";
      const params = new URLSearchParams();
      if (jobId) params.set("job_id", jobId);
      if (lastEventIdRef.current)
        params.set("Last-Event-ID", lastEventIdRef.current);
      if (params.toString()) url += `?${params.toString()}`;

      es = new EventSource(url);

      es.onopen = () => {
        attemptRef.current = 0;
        wasConnectedRef.current = true;
        // Defer the Zustand update to a macrotask (setTimeout 0) rather than a
        // microtask (queueMicrotask).  React 18's useSyncExternalStore schedules
        // its own flush via queueMicrotask; if our Zustand set() fires in the
        // same microtask checkpoint, concurrent flush callbacks can see stale
        // snapshots and trigger the "Too many re-renders" (React #185) loop.
        // A macrotask guarantees react's current render+commit fully complete
        // before any store update is processed.
        setTimeout(() => {
          setConnectionStatus("connected");
          setReconnectAttempt(0);
        }, 0);
      };

      // Handle named event types
      const eventTypes = [
        "job_state_changed",
        "log_line",
        "transcript_update",
        "diff_update",
        "approval_requested",
        "approval_resolved",
        "session_heartbeat",
        "snapshot",
        "job_succeeded",
        "job_failed",
        "job_resolved",
        "job_archived",
        "session_resumed",
        "job_title_updated",
        "progress_headline",
        "model_downgraded",
        "agent_plan_updated",
        "tool_group_summary",
        "merge_completed",
        "merge_conflict",
      ];

      for (const eventType of eventTypes) {
        es.addEventListener(eventType, (ev: MessageEvent) => {
          if (ev.lastEventId && /^\d+$/.test(ev.lastEventId)) {
            lastEventIdRef.current = ev.lastEventId;
          }
          try {
            const data: unknown = JSON.parse(ev.data as string);
            // Defer to a macrotask so this Zustand set() never lands in the
            // same microtask checkpoint as React's useSyncExternalStore flush.
            // See comment on onopen above for the full explanation of #185.
            setTimeout(() => dispatchSSEEvent(eventType, data), 0);
          } catch {
            // Ignore unparseable events
          }
        });
      }

      es.onerror = () => {
        es?.close();
        es = null;

        if (disposed) return;

        attemptRef.current += 1;

        if (attemptRef.current > MAX_ATTEMPTS) {
          setTimeout(() => {
            setConnectionStatus("disconnected");
            setReconnectAttempt(attemptRef.current);
          }, 0);
          return;
        }

        setTimeout(() => {
          setConnectionStatus(wasConnectedRef.current ? "reconnecting" : "connecting");
          setReconnectAttempt(attemptRef.current);
        }, 0);

        if (reconnectTimer) clearTimeout(reconnectTimer);
        const delay = Math.min(
          INITIAL_DELAY_MS * BACKOFF_MULTIPLIER ** (attemptRef.current - 1),
          MAX_DELAY_MS
        );
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null;
          connect();
        }, delay + jitter());
      };
    }

    connect();

    return () => {
      disposed = true;
      connectRef.current = null;
      es?.close();
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, [jobId]);

  const reconnect = useCallback(() => {
    attemptRef.current = 0;
    useStore.getState().setConnectionStatus(wasConnectedRef.current ? "reconnecting" : "connecting");
    connectRef.current?.();
  }, []);

  return { reconnect };
}
