import { request } from "./client";
import type {
  PeerMessageListResponse,
  PeerMessageRequest,
  PeerMessageResponse,
  SessionDirectoryResponse,
} from "./types";

export function fetchSessionDirectory(jobId: string, offset = 0): Promise<SessionDirectoryResponse> {
  return request(`/jobs/${encodeURIComponent(jobId)}/sessions?offset=${offset}`);
}

export function fetchPeerMessages(jobId: string, offset = 0): Promise<PeerMessageListResponse> {
  return request(`/jobs/${encodeURIComponent(jobId)}/peer-messages?offset=${offset}`);
}

export function relayPeerMessage(jobId: string, body: PeerMessageRequest): Promise<PeerMessageResponse> {
  return request(`/jobs/${encodeURIComponent(jobId)}/peer-messages`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
