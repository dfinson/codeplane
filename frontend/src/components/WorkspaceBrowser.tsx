import { useState, useEffect, useCallback, lazy, Suspense } from "react";
import { Folder, FolderOpen, FileCode, ChevronRight, ChevronDown, ArrowLeft, GitBranch, FolderTree } from "lucide-react";
import Editor from "@monaco-editor/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import { fetchWorkspaceFiles, fetchWorkspaceFile } from "../api/client";
import { useTowerStore, selectJobDiffs } from "../store";
import { useIsMobile } from "../hooks/useIsMobile";
import { Spinner } from "./ui/spinner";
import { cn } from "../lib/utils";

const DiffViewer = lazy(() => import("./DiffViewer"));

interface TreeEntry {
  path: string;
  type: "file" | "directory";
  sizeBytes?: number | null;
}

interface TreeNodeProps {
  entry: TreeEntry;
  depth: number;
  selected: string | null;
  onSelect: (path: string) => void;
  jobId: string;
  isMobile: boolean;
}

function TreeNode({ entry, depth, selected, onSelect, jobId, isMobile }: TreeNodeProps) {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<TreeEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const isDir = entry.type === "directory";
  const name = entry.path.split("/").pop() ?? entry.path;

  const handleToggle = useCallback(async () => {
    if (!isDir) {
      onSelect(entry.path);
      return;
    }
    if (!expanded && children.length === 0) {
      setLoading(true);
      try {
        const res = await fetchWorkspaceFiles(jobId, { path: entry.path });
        setChildren(res.items);
      } catch { /* */ } finally { setLoading(false); }
    }
    setExpanded(!expanded);
  }, [isDir, expanded, children.length, entry.path, jobId, onSelect]);

  return (
    <>
      <button
        type="button"
        onClick={handleToggle}
        className={cn(
          "flex items-center gap-1.5 py-1 px-2 rounded text-sm w-full transition-colors text-left",
          selected === entry.path ? "bg-accent" : "hover:bg-accent/50",
        )}
        style={{ paddingLeft: depth * 16 + 8 }}
        title={entry.path}
      >
        {isDir ? (
          expanded ? <ChevronDown size={14} className="shrink-0" /> : <ChevronRight size={14} className="shrink-0" />
        ) : (
          <span className="w-3.5" />
        )}
        {isDir ? (
          expanded
            ? <FolderOpen size={14} className="text-yellow-500 shrink-0" />
            : <Folder size={14} className="text-yellow-500 shrink-0" />
        ) : (
          <FileCode size={14} className="text-muted-foreground shrink-0" />
        )}
        <span className={cn("text-xs", isMobile ? "break-all" : "truncate")}>{name}</span>
        {loading && <Spinner size="sm" className="ml-auto" />}
      </button>
      {expanded && children.map((c) => (
        <TreeNode key={c.path} entry={c} depth={depth + 1} selected={selected} onSelect={onSelect} jobId={jobId} isMobile={isMobile} />
      ))}
    </>
  );
}

function guessLang(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  const m: Record<string, string> = {
    ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
    py: "python", rs: "rust", go: "go", java: "java", json: "json",
    yaml: "yaml", yml: "yaml", md: "markdown", html: "html", css: "css",
    sh: "shell", sql: "sql", toml: "toml", rb: "ruby", php: "php",
  };
  return m[ext] ?? "plaintext";
}

function isMarkdown(path: string): boolean {
  return path.split(".").pop()?.toLowerCase() === "md";
}

interface Props {
  jobId: string;
  jobState?: string;
  resolution?: string | null;
  archivedAt?: string | null;
}

export default function WorkspaceBrowser({ jobId, jobState, resolution, archivedAt }: Props) {
  const [entries, setEntries] = useState<TreeEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const isMobile = useIsMobile();
  const [mdMode, setMdMode] = useState<"preview" | "raw">("preview");
  const [mode, setMode] = useState<"files" | "changes">("files");
  const diffs = useTowerStore(selectJobDiffs(jobId));
  const hasChanges = diffs.length > 0;

  useEffect(() => {
    fetchWorkspaceFiles(jobId)
      .then((res) => setEntries(res.items))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [jobId]);

  const handleSelect = useCallback(async (path: string) => {
    setSelected(path);
    setMdMode("preview");
    setFileLoading(true);
    try {
      const res = await fetchWorkspaceFile(jobId, path);
      setFileContent(res.content);
    } catch {
      setFileContent("// Failed to load file");
    } finally {
      setFileLoading(false);
    }
  }, [jobId]);

  if (loading && mode === "files") return <div className="flex justify-center py-10"><Spinner /></div>;

  const showMdToggle = selected != null && isMarkdown(selected) && fileContent != null && !fileLoading;
  const mobileShowFile = isMobile && selected != null;

  const modeToggle = (
    <div className="flex items-center gap-1 p-1 rounded-lg bg-muted/50 border border-border">
      <button
        type="button"
        onClick={() => setMode("files")}
        className={cn(
          "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors",
          mode === "files" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
        )}
      >
        <FolderTree size={13} />
        Files
      </button>
      <button
        type="button"
        onClick={() => setMode("changes")}
        className={cn(
          "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors",
          mode === "changes" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
        )}
      >
        <GitBranch size={13} />
        Changes
        {hasChanges && (
          <span className="ml-0.5 px-1.5 py-0.5 text-[10px] rounded-full bg-primary/15 text-primary font-semibold">
            {diffs.length}
          </span>
        )}
      </button>
    </div>
  );

  const treePanel = (
    <div className={cn(
      "shrink-0 flex flex-col overflow-hidden rounded-lg border border-border bg-card",
      isMobile ? "flex-1" : "w-64",
    )}>
      <div className="px-3 py-2.5 border-b border-border">
        <span className="text-xs font-semibold text-muted-foreground">Files</span>
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        {entries.map((e) => (
          <TreeNode key={e.path} entry={e} depth={0} selected={selected} onSelect={handleSelect} jobId={jobId} isMobile={isMobile} />
        ))}
      </div>
    </div>
  );

  const filePanel = (
    <div className="flex-1 min-h-0 overflow-hidden rounded-lg border border-border bg-card flex flex-col">
      {(isMobile || showMdToggle) && (
        <div className="flex items-center gap-1 px-3 py-1.5 border-b border-border shrink-0">
          {isMobile && (
            <button
              type="button"
              onClick={() => { setSelected(null); setFileContent(null); }}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mr-auto"
            >
              <ArrowLeft size={14} />
              Back
            </button>
          )}
          {showMdToggle && (
            <>
              <button
                type="button"
                onClick={() => setMdMode("preview")}
                className={cn(
                  "px-2.5 py-0.5 rounded text-xs font-medium transition-colors",
                  mdMode === "preview" ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground"
                )}
              >
                Preview
              </button>
              <button
                type="button"
                onClick={() => setMdMode("raw")}
                className={cn(
                  "px-2.5 py-0.5 rounded text-xs font-medium transition-colors",
                  mdMode === "raw" ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground"
                )}
              >
                Raw
              </button>
            </>
          )}
        </div>
      )}

      {fileLoading ? (
        <div className="flex items-center justify-center flex-1"><Spinner /></div>
      ) : selected && fileContent != null ? (
        showMdToggle && mdMode === "preview" ? (
          <div className="flex-1 overflow-y-auto p-5 prose prose-sm prose-invert max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>{fileContent}</ReactMarkdown>
          </div>
        ) : (
          <div className="flex-1 overflow-hidden">
            <Editor
              value={fileContent}
              language={guessLang(selected)}
              theme="vs-dark"
              options={{
                readOnly: true,
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                fontSize: isMobile ? 12 : 13,
                lineNumbersMinChars: isMobile ? 2 : 3,
                glyphMargin: false,
                lineDecorationsWidth: isMobile ? 2 : 4,
                folding: !isMobile,
              }}
            />
          </div>
        )
      ) : (
        <p className="text-sm text-muted-foreground text-center py-8">Select a file to preview</p>
      )}
    </div>
  );

  if (mode === "changes") {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          {modeToggle}
        </div>
        <Suspense fallback={<div className="flex justify-center py-10"><Spinner /></div>}>
          <DiffViewer jobId={jobId} jobState={jobState} resolution={resolution} archivedAt={archivedAt} />
        </Suspense>
      </div>
    );
  }

  if (isMobile) {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          {modeToggle}
        </div>
        <div className="flex flex-col h-[60vh] min-h-[300px]">
          {mobileShowFile ? filePanel : treePanel}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        {modeToggle}
      </div>
      <div className="flex flex-row gap-3 h-[60vh] min-h-[300px] max-h-[600px]">
        {treePanel}
        {filePanel}
      </div>
    </div>
  );
}
