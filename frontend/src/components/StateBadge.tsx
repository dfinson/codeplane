import { memo } from "react";
import {
  type LucideIcon,
  Loader2, Clock, ShieldQuestion, CheckCircle2, XCircle, Ban, Eye,
} from "lucide-react";
import { Tooltip } from "./ui/tooltip";

const STATE_CONFIG: Record<string, { bg: string; text: string; label: string; Icon: LucideIcon; tip: string }> = {
  preparing: { bg: "bg-violet-900/30", text: "text-violet-400", label: "Preparing", Icon: Loader2, tip: "" },
  queued: { bg: "bg-yellow-900/30", text: "text-yellow-400", label: "Queued", Icon: Clock, tip: "Waiting in line to start" },
  running: { bg: "bg-blue-900/30", text: "text-blue-400", label: "Running", Icon: Loader2, tip: "Agent is actively working" },
  observing: { bg: "bg-muted", text: "text-muted-foreground", label: "Observation", Icon: Eye, tip: "External history; no managed execution or workspace" },
  waiting_for_approval: { bg: "bg-orange-900/30", text: "text-orange-400", label: "Approval", Icon: ShieldQuestion, tip: "Agent paused — waiting for your approval to continue" },
  review: { bg: "bg-cyan-900/30", text: "text-cyan-400", label: "In Review", Icon: Eye, tip: "Agent finished — changes are ready for your review" },
  completed: { bg: "bg-green-900/30", text: "text-green-400", label: "Completed", Icon: CheckCircle2, tip: "Job finished and changes have been resolved" },
  failed: { bg: "bg-red-900/30", text: "text-red-400", label: "Failed", Icon: XCircle, tip: "Job encountered an error and stopped" },
  canceled: { bg: "bg-gray-800/50", text: "text-gray-400", label: "Canceled", Icon: Ban, tip: "Job was manually canceled" },
};

const DEFAULT_CFG = { bg: "bg-gray-800/50", text: "text-gray-400", label: "Unknown", Icon: Clock, tip: "Unknown job state" };

export const StateBadge = memo(function StateBadge({ state }: { state: string }) {
  const cfg = STATE_CONFIG[state] ?? DEFAULT_CFG;
  const badge = (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wide ${cfg.bg} ${cfg.text}`}>
      <cfg.Icon size={12} aria-hidden="true" />
      {cfg.label}
    </span>
  );
  return cfg.tip ? <Tooltip content={cfg.tip}>{badge}</Tooltip> : badge;
});
