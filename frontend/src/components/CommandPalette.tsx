import { useState, useEffect, useMemo, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Dialog, DialogContent, DialogTitle } from "./ui/dialog";
import { useStore } from "../store";
import {
  Search,
  Plus,
  History,
  Settings,
  TerminalSquare,
  ArrowRight,
} from "lucide-react";

interface PaletteItem {
  id: string;
  label: string;
  description?: string;
  icon: React.ReactNode;
  action: () => void;
  keywords?: string[];
}

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const navigate = useNavigate();
  const jobs = useStore((s) => Object.values(s.jobs));

  // Ctrl/Cmd+K toggles the palette
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Allow the header trigger button to open the palette
  useEffect(() => {
    const openPalette = () => setOpen(true);
    window.addEventListener("open-command-palette", openPalette);
    return () => window.removeEventListener("open-command-palette", openPalette);
  }, []);

  const staticItems: PaletteItem[] = useMemo(
    () => [
      {
        id: "new-job",
        label: "New Job",
        description: "Create a new agent job",
        icon: <Plus className="h-4 w-4" />,
        action: () => navigate("/jobs/new"),
        keywords: ["create", "add", "start"],
      },
      {
        id: "history",
        label: "History",
        description: "View archived jobs",
        icon: <History className="h-4 w-4" />,
        action: () => navigate("/history"),
        keywords: ["archive", "past", "old"],
      },
      {
        id: "settings",
        label: "Settings",
        description: "Configure CodePlane",
        icon: <Settings className="h-4 w-4" />,
        action: () => navigate("/settings"),
        keywords: ["config", "preferences", "options"],
      },
      {
        id: "terminal",
        label: "Toggle Terminal",
        description: "Open or close the terminal drawer",
        icon: <TerminalSquare className="h-4 w-4" />,
        action: () => useStore.getState().toggleTerminalDrawer(),
        keywords: ["console", "shell", "cli"],
      },
    ],
    [navigate],
  );

  const jobItems: PaletteItem[] = useMemo(
    () =>
      jobs.map((job) => ({
        id: `job-${job.id}`,
        label: job.title || job.id,
        description: `${job.state} · ${job.repo}`,
        icon: <ArrowRight className="h-4 w-4" />,
        action: () => navigate(`/jobs/${job.id}`),
        keywords: [job.id, job.repo, job.state],
      })),
    [jobs, navigate],
  );

  const allItems = useMemo(
    () => [...staticItems, ...jobItems],
    [staticItems, jobItems],
  );

  const filtered = useMemo(() => {
    if (!query.trim()) return allItems;
    const q = query.toLowerCase();
    return allItems.filter(
      (item) =>
        item.label.toLowerCase().includes(q) ||
        item.description?.toLowerCase().includes(q) ||
        item.keywords?.some((k) => k.toLowerCase().includes(q)),
    );
  }, [allItems, query]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  const runItem = useCallback((item: PaletteItem) => {
    item.action();
    setOpen(false);
    setQuery("");
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter" && filtered[selectedIndex]) {
        e.preventDefault();
        runItem(filtered[selectedIndex]);
      }
    },
    [filtered, selectedIndex, runItem],
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setQuery("");
      }}
    >
      <DialogContent className="max-w-lg p-0 gap-0 overflow-hidden">
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <div className="flex items-center border-b border-border px-3 pr-14">
          <Search className="h-4 w-4 text-muted-foreground shrink-0" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search jobs, navigate…"
            className="flex-1 bg-transparent border-0 outline-none px-3 py-3 text-sm placeholder:text-muted-foreground"
            autoFocus
          />
          <kbd className="text-xs text-muted-foreground border border-border rounded px-1.5 py-0.5 font-mono shrink-0">
            ESC
          </kbd>
        </div>
        <div className="max-h-72 overflow-y-auto p-1">
          {filtered.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-6">
              No results found
            </p>
          )}
          {filtered.map((item, i) => (
            <button
              key={item.id}
              onClick={() => runItem(item)}
              onMouseEnter={() => setSelectedIndex(i)}
              className={`w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm text-left transition-colors ${
                i === selectedIndex
                  ? "bg-accent text-accent-foreground"
                  : "text-foreground hover:bg-accent/50"
              }`}
            >
              <span className="text-muted-foreground shrink-0">
                {item.icon}
              </span>
              <div className="flex-1 min-w-0">
                <span className="font-medium">{item.label}</span>
                {item.description && (
                  <span className="ml-2 text-xs text-muted-foreground">
                    {item.description}
                  </span>
                )}
              </div>
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
