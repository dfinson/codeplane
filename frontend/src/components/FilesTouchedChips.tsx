import { Eye, Pencil } from "lucide-react";
import type { Step } from "../store";

function basename(path: string): string {
  return path.split("/").pop() ?? path;
}

export function FilesTouchedChips({ step }: { step: Step }) {
  if (!step.filesRead?.length && !step.filesWritten?.length) return null;

  return (
    <div className="flex flex-wrap gap-1 mt-1.5">
      {step.filesWritten?.map((f) => (
        <span
          key={f}
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-emerald-500/10 text-emerald-600"
        >
          <Pencil size={10} />
          {basename(f)}
        </span>
      ))}
      {step.filesRead?.slice(0, 4).map((f) => (
        <span
          key={f}
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-muted text-muted-foreground"
        >
          <Eye size={10} />
          {basename(f)}
        </span>
      ))}
      {(step.filesRead?.length ?? 0) > 4 && (
        <span className="text-xs text-muted-foreground">
          +{step.filesRead!.length - 4} more
        </span>
      )}
    </div>
  );
}
