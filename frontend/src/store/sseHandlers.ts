/**
 * SSE event handler lookup table.
 *
 * Individual handlers are split into group files under ./handlers/.
 * This module re-assembles them into the single lookup table consumed
 * by the store's SSE dispatcher.
 */

export type { SSEHandler } from "./handlers/types";
export { enrichJob } from "./handlers/jobHandlers";
export { mergeTrackerOperations } from "./handlers/miscHandlers";

import type { SSEHandler } from "./handlers/types";
import { jobHandlers } from "./handlers/jobHandlers";
import { transcriptHandlers } from "./handlers/transcriptHandlers";
import { timelineHandlers } from "./handlers/timelineHandlers";
import { approvalHandlers } from "./handlers/approvalHandlers";
import { miscHandlers } from "./handlers/miscHandlers";

// ---------------------------------------------------------------------------
// Lookup table
// ---------------------------------------------------------------------------

export const sseHandlers: Record<string, SSEHandler> = {
  ...jobHandlers,
  ...transcriptHandlers,
  ...timelineHandlers,
  ...approvalHandlers,
  ...miscHandlers,
};
