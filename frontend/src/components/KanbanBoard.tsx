import { useState, useMemo, useRef, useEffect, useCallback } from "react";
import { Search, ArrowDownUp } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { useStore, selectSignoffJobs, selectActiveJobs, selectAttentionJobs } from "../store";
import type { JobSummary } from "../store";
import { KanbanColumn } from "./KanbanColumn";
import { KANBAN_COLUMNS } from "../constants/kanban";
import { Input } from "./ui/input";
import { Button } from "./ui/button";

type SortKey = "newest" | "oldest" | "updated" | "alpha";

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "newest", label: "Newest" },
  { key: "oldest", label: "Oldest" },
  { key: "updated", label: "Recently Updated" },
  { key: "alpha", label: "A → Z" },
];

function sortJobs(jobs: JobSummary[], sort: SortKey): JobSummary[] {
  const copy = [...jobs];
  switch (sort) {
    case "newest":
      return copy.sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
    case "oldest":
      return copy.sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());
    case "updated":
      return copy.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
    case "alpha":
      return copy.sort((a, b) => (a.title ?? a.id).localeCompare(b.title ?? b.id));
  }
}

function filterJobs(jobs: JobSummary[], query: string): JobSummary[] {
  if (!query.trim()) return jobs;
  const q = query.trim().toLowerCase();
  return jobs.filter(
    (j) =>
      (j.title ?? "").toLowerCase().includes(q) ||
      j.id.toLowerCase().includes(q) ||
      j.repo.toLowerCase().includes(q) ||
      (j.branch ?? "").toLowerCase().includes(q) ||
      j.prompt.toLowerCase().includes(q),
  );
}

export function KanbanBoard() {
  const activeJobs = useStore(useShallow(selectActiveJobs));
  const signoffJobs = useStore(useShallow(selectSignoffJobs));
  const attentionJobs = useStore(useShallow(selectAttentionJobs));

  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("newest");
  const [sortOpen, setSortOpen] = useState(false);
  const filterInputRef = useRef<HTMLInputElement>(null);

  const handleFilterKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === "/" && document.activeElement?.tagName !== "INPUT" && document.activeElement?.tagName !== "TEXTAREA") {
      e.preventDefault();
      filterInputRef.current?.focus();
    }
  }, []);

  useEffect(() => {
    window.addEventListener("keydown", handleFilterKeyDown);
    return () => window.removeEventListener("keydown", handleFilterKeyDown);
  }, [handleFilterKeyDown]);

  const process = (jobs: JobSummary[]) => sortJobs(filterJobs(jobs, query), sort);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const filteredActive = useMemo(() => process(activeJobs), [activeJobs, query, sort]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const filteredSignoff = useMemo(() => process(signoffJobs), [signoffJobs, query, sort]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const filteredAttention = useMemo(() => process(attentionJobs), [attentionJobs, query, sort]);

  const currentSortLabel = SORT_OPTIONS.find((o) => o.key === sort)!.label;

  return (
    <div className="flex flex-col gap-3 h-[calc(100vh-140px)] max-sm:hidden">
      {/* Toolbar */}
      <div className="flex items-center gap-2 shrink-0">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
          <Input
            ref={filterInputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setQuery("");
                filterInputRef.current?.blur();
              }
            }}
            placeholder="Filter active jobs…"
            className="pl-8 pr-8 h-8 text-sm"
          />
          {!query && (
            <kbd className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded border border-border px-1 py-px font-mono text-[10px] text-muted-foreground leading-none">
              /
            </kbd>
          )}
        </div>
        <div className="relative">
          <Button
            variant="outline"
            size="sm"
            className="h-8 gap-1.5 text-xs"
            onClick={() => setSortOpen((v) => !v)}
          >
            <ArrowDownUp size={12} />
            {currentSortLabel}
          </Button>
          {sortOpen && (
            <div className="absolute right-0 top-full mt-1 z-50 min-w-[160px] rounded-md border border-border bg-popover shadow-md py-1">
              {SORT_OPTIONS.map((opt) => (
                <button
                  key={opt.key}
                  className={`w-full text-left px-3 py-1.5 text-xs hover:bg-accent transition-colors ${sort === opt.key ? "text-primary font-medium" : "text-foreground"}`}
                  onClick={() => { setSort(opt.key); setSortOpen(false); }}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Board */}
      <div className="grid grid-cols-3 gap-3 flex-1 min-h-0 max-lg:grid-cols-2">
        <KanbanColumn title={KANBAN_COLUMNS.IN_PROGRESS} jobs={filteredActive} />
        <KanbanColumn title={KANBAN_COLUMNS.AWAITING_INPUT} jobs={filteredSignoff} />
        <KanbanColumn title={KANBAN_COLUMNS.FAILED} jobs={filteredAttention} />
      </div>
    </div>
  );
}
