import { Search, X } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "../lib/utils";
import { fetchTranscriptSearch } from "../api/client";

interface SearchResult {
  seq: number;
  role: string;
  content: string;
  toolName: string | null;
  stepId: string | null;
  stepNumber: number | null;
  timestamp: string;
}

export type FilterChipKey = "errors" | "tools" | "agent" | "approvals";

interface FilterChipDisplay {
  key: FilterChipKey;
  label: string;
  count?: number;
}

interface StepSearchBarProps {
  jobId: string;
  onSelect?: (result: SearchResult) => void;
  activeFilter?: FilterChipKey | null;
  onFilterChange?: (filter: FilterChipKey | null) => void;
  /** Only chips with data to show — computed by parent from step state */
  visibleChips?: FilterChipDisplay[];
}

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function StepSearchBar({ jobId, onSelect, activeFilter, onFilterChange, visibleChips }: StepSearchBarProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const debouncedQuery = useDebounce(query, 300);

  useEffect(() => {
    if (!debouncedQuery || debouncedQuery.length < 2) {
      setResults([]);
      return;
    }
    fetchTranscriptSearch(jobId, debouncedQuery)
      .then(setResults)
      .catch(() => setResults([]));
  }, [jobId, debouncedQuery]);

  return (
    <div className="relative mb-2">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
        <Search size={14} className="text-muted-foreground shrink-0" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search transcript…"
          className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
        />
        {query && (
          <button
            onClick={() => { setQuery(""); setResults([]); }}
            className="text-muted-foreground hover:text-foreground"
          >
            <X size={14} />
          </button>
        )}
      </div>
      {/* Filter chips — only shown when relevant data exists */}
      {onFilterChange && visibleChips && visibleChips.length > 0 && (
        <div className="flex items-center gap-1.5 px-3 py-1.5 overflow-x-auto">
          {visibleChips.map((chip) => (
            <button
              key={chip.key}
              onClick={() => onFilterChange(activeFilter === chip.key ? null : chip.key)}
              className={cn(
                "shrink-0 px-2 py-0.5 rounded-full text-xs transition-colors",
                activeFilter === chip.key
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted text-muted-foreground hover:text-foreground",
              )}
            >
              {chip.label}{chip.count != null ? ` (${chip.count})` : ""}
            </button>
          ))}
        </div>
      )}
      {results.length > 0 && (
        <div className="absolute z-10 top-full left-0 right-0 bg-card border border-border rounded-b-md shadow-lg max-h-64 overflow-y-auto">
          {results.map((r) => (
            <button
              key={r.seq}
              onClick={() => { onSelect?.(r); setQuery(""); setResults([]); }}
              className="w-full text-left px-3 py-2 hover:bg-accent text-sm border-b border-border last:border-0"
            >
              <div className="flex items-center gap-2 text-xs text-muted-foreground mb-0.5">
                <span className="capitalize">{r.role}</span>
                {r.stepNumber != null && <span>· Step {r.stepNumber}</span>}
              </div>
              <div className="truncate text-foreground/90">{r.content}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
