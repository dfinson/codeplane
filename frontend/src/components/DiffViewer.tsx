import { useState, useEffect } from "react";
import { type LucideIcon, FileCode, FilePlus, FileMinus, FileEdit } from "lucide-react";
import { DiffEditor } from "@monaco-editor/react";
import { useTowerStore, selectJobDiffs } from "../store";
import { fetchJobDiff } from "../api/client";
import { Spinner } from "./ui/spinner";
import { cn } from "../lib/utils";

interface DiffViewerProps {
  jobId: string;
}

const STATUS_ICON: Record<string, LucideIcon> = {
  added: FilePlus,
  deleted: FileMinus,
  modified: FileEdit,
  renamed: FileEdit,
};

const STATUS_BADGE: Record<string, string> = {
  added: "text-green-400 border-green-800",
  deleted: "text-red-400 border-red-800",
  modified: "text-blue-400 border-blue-800",
  renamed: "text-yellow-400 border-yellow-800",
};

const STATUS_ICON_CLASS: Record<string, string> = {
  added: "text-green-400",
  deleted: "text-red-400",
  modified: "text-blue-400",
  renamed: "text-yellow-400",
};

function guessLanguage(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  const map: Record<string, string> = {
    ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
    py: "python", rs: "rust", go: "go", java: "java", kt: "kotlin",
    rb: "ruby", php: "php", cs: "csharp", cpp: "cpp", c: "c", h: "c",
    json: "json", yaml: "yaml", yml: "yaml", toml: "toml",
    md: "markdown", html: "html", css: "css", scss: "scss",
    sql: "sql", sh: "shell", bash: "shell", dockerfile: "dockerfile",
  };
  return map[ext] ?? "plaintext";
}

export default function DiffViewer({ jobId }: DiffViewerProps) {
  const diffs = useTowerStore(selectJobDiffs(jobId));
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [original, setOriginal] = useState("");
  const [modified, setModified] = useState("");
  const [loading, setLoading] = useState(false);

  // Fetch historical diff from API on mount (for completed jobs / page refresh)
  useEffect(() => {
    fetchJobDiff(jobId)
      .then((files) => {
        if (files.length > 0) {
          useTowerStore.setState((s) => ({
            diffs: { ...s.diffs, [jobId]: files },
          }));
        }
      })
      .catch(() => {});
  }, [jobId]);

  const selectedFile = diffs[selectedIdx];

  useEffect(() => {
    if (!selectedFile) return;
    setLoading(true);

    const additions = selectedFile.hunks
      ?.flatMap((h: { lines?: { type: string; content: string }[] }) =>
        (h.lines ?? []).filter((l: { type: string }) => l.type !== "deletion").map((l: { content: string }) => l.content),
      )
      .join("\n") ?? "";

    const deletions = selectedFile.hunks
      ?.flatMap((h: { lines?: { type: string; content: string }[] }) =>
        (h.lines ?? []).filter((l: { type: string }) => l.type !== "addition").map((l: { content: string }) => l.content),
      )
      .join("\n") ?? "";

    setOriginal(deletions);
    setModified(additions);
    setLoading(false);
  }, [selectedFile]);

  const totalAdditions = diffs.reduce((sum, f) => sum + (f.additions ?? 0), 0);
  const totalDeletions = diffs.reduce((sum, f) => sum + (f.deletions ?? 0), 0);

  if (diffs.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-8 text-center">
        <p className="text-sm text-muted-foreground">No changes detected</p>
      </div>
    );
  }

  return (
    <div className="flex gap-3 h-[500px]">
      {/* File list sidebar */}
      <div className="w-64 shrink-0 flex flex-col overflow-hidden rounded-lg border border-border bg-card">
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-border">
          <span className="text-xs font-semibold text-muted-foreground">{diffs.length} files</span>
          <div className="flex items-center gap-2">
            <span className="text-xs text-green-400">+{totalAdditions}</span>
            <span className="text-xs text-red-400">-{totalDeletions}</span>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          {diffs.map((file, i) => {
            const Icon = STATUS_ICON[file.status] ?? FileCode;
            return (
              <button
                key={i}
                type="button"
                onClick={() => setSelectedIdx(i)}
                className={cn(
                  "flex items-center gap-2 px-3 py-2 text-sm transition-colors w-full text-left",
                  i === selectedIdx ? "bg-accent" : "hover:bg-accent/50",
                )}
              >
                <Icon size={14} className={cn("shrink-0", STATUS_ICON_CLASS[file.status])} />
                <span className="text-xs truncate flex-1 text-foreground">{file.path}</span>
                <span className={cn("text-xs border rounded px-1", STATUS_BADGE[file.status])}>
                  +{file.additions} -{file.deletions}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Monaco Diff Editor */}
      <div className="flex-1 overflow-hidden rounded-lg border border-border bg-card">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <Spinner />
          </div>
        ) : selectedFile ? (
          <DiffEditor
            original={original}
            modified={modified}
            language={guessLanguage(selectedFile.path)}
            theme="vs-dark"
            options={{
              readOnly: true,
              minimap: { enabled: false },
              renderSideBySide: true,
              scrollBeyondLastLine: false,
              fontSize: 13,
            }}
          />
        ) : null}
      </div>
    </div>
  );
}
