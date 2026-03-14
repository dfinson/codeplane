import { memo } from "react";
import {
  type LucideIcon,
  Loader2, Clock, ShieldQuestion, CheckCircle2, XCircle, Ban,
} from "lucide-react";

const STATE_CONFIG: Record<string, { bg: string; text: string; label: string; Icon: LucideIcon }> = {
  queued: { bg: "bg-yellow-900/30", text: "text-yellow-400", label: "Queued", Icon: Clock },
  running: { bg: "bg-blue-900/30", text: "text-blue-400", label: "Running", Icon: Loader2 },
  waiting_for_approval: { bg: "bg-orange-900/30", text: "text-orange-400", label: "Approval", Icon: ShieldQuestion },
  succeeded: { bg: "bg-green-900/30", text: "text-green-400", label: "Succeeded", Icon: CheckCircle2 },
  failed: { bg: "bg-red-900/30", text: "text-red-400", label: "Failed", Icon: XCircle },
  canceled: { bg: "bg-gray-800/50", text: "text-gray-400", label: "Canceled", Icon: Ban },
};

const DEFAULT_CFG = { bg: "bg-gray-800/50", text: "text-gray-400", label: "Unknown", Icon: Clock };

export const StateBadge = memo(function StateBadge({ state }: { state: string }) {
  const cfg = STATE_CONFIG[state] ?? DEFAULT_CFG;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wide ${cfg.bg} ${cfg.text}`}>
      <cfg.Icon size={12} />
      {cfg.label}
    </span>
  );
});
