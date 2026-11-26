import { create } from "zustand";
import { fetchPeerMessages, fetchSessionDirectory } from "../api/sessionCoordination";
import type { PeerMessageListResponse, SessionDirectoryResponse } from "../api/types";

interface SessionCoordinationState {
  directories: Record<string, SessionDirectoryResponse>;
  messages: Record<string, PeerMessageListResponse>;
  loading: Record<string, boolean>;
  errors: Record<string, string | undefined>;
  requests: Record<string, object>;
  load: (jobId: string, offset?: number) => Promise<void>;
}

export const useSessionCoordination = create<SessionCoordinationState>((set, get) => ({
  directories: {},
  messages: {},
  loading: {},
  errors: {},
  requests: {},
  load: async (jobId, offset = 0) => {
    const request = {};
    set((state) => ({
      requests: { ...state.requests, [jobId]: request },
      loading: { ...state.loading, [jobId]: true },
      errors: { ...state.errors, [jobId]: undefined },
    }));
    try {
      const [directory, messages] = await Promise.all([
        fetchSessionDirectory(jobId, offset),
        fetchPeerMessages(jobId, offset),
      ]);
      if (get().requests[jobId] !== request) return;
      set((state) => ({
        directories: { ...state.directories, [jobId]: directory },
        messages: { ...state.messages, [jobId]: messages },
      }));
    } catch (error) {
      if (get().requests[jobId] !== request) return;
      set((state) => ({
        errors: {
          ...state.errors,
          [jobId]: error instanceof Error ? error.message : "Could not load session details",
        },
      }));
    } finally {
      if (get().requests[jobId] === request) {
        set((state) => ({ loading: { ...state.loading, [jobId]: false } }));
      }
    }
  },
}));
