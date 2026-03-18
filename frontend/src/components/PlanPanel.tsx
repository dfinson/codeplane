import { useState } from "react";
import { ChevronDown, ChevronRight, ListChecks, Circle, CheckCircle2, Loader2, SkipForward } from "lucide-react";
import { useStore, selectJobPlan } from "../store";
import type { PlanStep } from "../store";

function StepIcon({ status }: { status: PlanStep["status"] }) {
  switch (status) {
    case "done":
      return <CheckCircle2 size={13} className="text-emerald-400 shrink-0" />;
    case "active":
      return <Loader2 size={13} className="text-blue-400 animate-spin shrink-0" />;
    case "skipped":
      return <SkipForward size={13} className="text-muted-foreground/50 shrink-0" />;
    default:
      return <Circle size={13} className="text-muted-foreground/40 shrink-0" />;
  }
}

export function PlanPanel({ jobId }: { jobId: string }) {
  const steps = useStore(selectJobPlan(jobId));
  const [expanded, setExpanded] = useState(true);

  if (steps.length === 0) return null;

  const doneCount = steps.filter((s) => s.status === "done").length;
  const activeStep = steps.find((s) => s.status === "active");

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="flex items-center gap-2 px-4 py-2.5 w-full text-left border-b border-border hover:bg-accent/50 transition-colors"
      >
        {expanded ? (
          <ChevronDown size={13} className="text-muted-foreground shrink-0" />
        ) : (
          <ChevronRight size={13} className="text-muted-foreground shrink-0" />
        )}
        <ListChecks size={13} className="text-muted-foreground shrink-0" />
        <span className="text-sm font-semibold text-muted-foreground">Plan</span>
        {!expanded && activeStep && (
          <span className="text-xs text-muted-foreground/70 truncate ml-1">
            — {activeStep.label}
          </span>
        )}
        <span className="ml-auto text-[11px] text-muted-foreground/50 tabular-nums shrink-0">
          {doneCount}/{steps.length}
        </span>
      </button>

      {expanded && (
        <div className="px-4 py-3">
          <div className="space-y-2">
            {steps.map((step, i) => (
              <div
                key={i}
                className={`flex items-start gap-2 ${
                  step.status === "done"
                    ? "text-muted-foreground/60"
                    : step.status === "active"
                      ? "text-foreground"
                      : step.status === "skipped"
                        ? "text-muted-foreground/40 line-through"
                        : "text-muted-foreground"
                }`}
              >
                <div className="mt-0.5">
                  <StepIcon status={step.status} />
                </div>
                <span className={`text-xs leading-snug ${step.status === "active" ? "font-medium" : ""}`}>
                  {step.label}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
