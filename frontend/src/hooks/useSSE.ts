/**
 * SSE client with exponential backoff reconnection.
 *
 * Connects to /api/events on mount, dispatches events to the Zustand store,
 * and handles reconnection with a Last-Event-ID query parameter (EventSource
 * does not support custom request headers).
 */

import { useCallback, useEffect, useRef } from "react";
import { useTowerStore } from "../store";

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

  useEffect(() => {
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const { setConnectionStatus, dispatchSSEEvent } =
      useTowerStore.getState();

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
        setConnectionStatus("connected");
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
      ];

      for (const eventType of eventTypes) {
        es.addEventListener(eventType, (ev: MessageEvent) => {
          if (ev.lastEventId && /^\d+$/.test(ev.lastEventId)) {
            lastEventIdRef.current = ev.lastEventId;
          }
          try {
            const data: unknown = JSON.parse(ev.data as string);
            dispatchSSEEvent(eventType, data);
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
          setConnectionStatus("disconnected");
          return;
        }

        setConnectionStatus("reconnecting");

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
    useTowerStore.getState().setConnectionStatus("reconnecting");
    connectRef.current?.();
  }, []);

  return { reconnect };
}
