import { Skeleton } from "./ui/skeleton";

export function KanbanSkeleton() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 p-4">
      {Array.from({ length: 3 }).map((_, col) => (
        <div key={col} className="space-y-3">
          <Skeleton className="h-8 w-32" />
          {Array.from({ length: col === 0 ? 3 : 2 }).map((_, i) => (
            <div key={i} className="rounded-lg border border-border p-4 space-y-2">
              <Skeleton className="h-5 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-3 w-full" />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
