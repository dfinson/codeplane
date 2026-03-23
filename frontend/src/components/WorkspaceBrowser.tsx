import { Suspense, useState, useEffect, useCallback, useMemo, useRef } from "react";
import { Folder, FolderOpen, FileCode, FilePlus2, FileEdit, FileMinus2, FileSymlink, ChevronRight, ChevronDown, ArrowLeft } from "lucide-react";
import Editor from "@monaco-editor/react";
import type { OnMount } from "@monaco-editor/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import { fetchWorkspaceFiles, fetchWorkspaceFile } from "../api/client";
import { useStore, selectJobDiffs } from "../store";
import type { DiffFileModel } from "../api/types";
import { useIsMobile } from "../hooks/useIsMobile";
import { Spinner } from "./ui/spinner";
import { cn } from "../lib/utils";
import { lazyRetry } from "../lib/lazyRetry";

const MobileSyntaxView = lazyRetry(() => import("./MobileSyntaxView"));

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
  diffMap: Map<string, DiffFileModel>;
  changedDirs: Set<string>;
}

const STATUS_COLOR: Record<string, string> = {
  added: "text-emerald-400",
  modified: "text-blue-400",
  deleted: "text-red-400 line-through",
  renamed: "text-yellow-400",
};

function FileIcon({ status }: { status?: string }) {
  switch (status) {
    case "added": return <FilePlus2 size={14} className="text-emerald-400 shrink-0" />;
    case "modified": return <FileEdit size={14} className="text-blue-400 shrink-0" />;
    case "deleted": return <FileMinus2 size={14} className="text-red-400 shrink-0" />;
    case "renamed": return <FileSymlink size={14} className="text-yellow-400 shrink-0" />;
    default: return <FileCode size={14} className="text-muted-foreground shrink-0" />;
  }
}

function TreeNode({ entry, depth, selected, onSelect, jobId, isMobile, diffMap, changedDirs }: TreeNodeProps) {
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

  const diff = !isDir ? diffMap.get(entry.path) : undefined;
  const hasChanges = isDir && changedDirs.has(entry.path);
  const statusColor = diff ? (STATUS_COLOR[diff.status] ?? "") : "text-muted-foreground";

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
          <>
            {expanded
              ? <FolderOpen size={14} className="text-yellow-500 shrink-0" />
              : <Folder size={14} className="text-yellow-500 shrink-0" />}
          </>
        ) : (
          <FileIcon status={diff?.status} />
        )}
        <span className={cn("text-xs", isMobile ? "break-all" : "truncate", !isDir && statusColor)}>{name}</span>
        {isDir && hasChanges && <span className="w-1.5 h-1.5 rounded-full bg-blue-400 shrink-0" />}
        {diff && (diff.additions > 0 || diff.deletions > 0) && (
          <span className="ml-auto text-xs tabular-nums flex gap-1">
            {diff.additions > 0 && <span className="text-emerald-400">+{diff.additions}</span>}
            {diff.deletions > 0 && <span className="text-red-400">−{diff.deletions}</span>}
          </span>
        )}
        {loading && <Spinner size="sm" className="ml-auto" />}
      </button>
      {expanded && children.map((c) => (
        <TreeNode key={c.path} entry={c} depth={depth + 1} selected={selected} onSelect={onSelect} jobId={jobId} isMobile={isMobile} diffMap={diffMap} changedDirs={changedDirs} />
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

function getLanguageFromPath(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() || "";
  const map: Record<string, string> = {
    ts: "typescript", tsx: "tsx", js: "javascript", jsx: "jsx",
    py: "python", rs: "rust", go: "go", rb: "ruby",
    java: "java", kt: "kotlin", swift: "swift", cs: "csharp",
    cpp: "cpp", c: "c", h: "c", hpp: "cpp",
    css: "css", scss: "scss", html: "html", xml: "xml",
    json: "json", yaml: "yaml", yml: "yaml", toml: "toml",
    md: "markdown", sql: "sql", sh: "bash", bash: "bash",
    dockerfile: "docker", makefile: "makefile",
  };
  return map[ext] || "text";
}

function isMarkdown(path: string): boolean {
  return path.split(".").pop()?.toLowerCase() === "md";
}

interface Props {
  jobId: string;
}

/** Compute Monaco decoration ranges for added lines in the given diff file. */
function buildAddedDecorations(
  diffFile: DiffFileModel,
  totalLines: number,
): { range: { startLineNumber: number; startColumn: number; endLineNumber: number; endColumn: number }; options: { isWholeLine: boolean; className: string; linesDecorationsClassName: string } }[] {
  if (diffFile.status === "added") {
    // Every line in the file is new
    return Array.from({ length: totalLines }, (_, i) => ({
      range: { startLineNumber: i + 1, startColumn: 1, endLineNumber: i + 1, endColumn: 1 },
      options: { isWholeLine: true, className: "diff-add-line", linesDecorationsClassName: "diff-add-decoration" },
    }));
  }

  const decorations: ReturnType<typeof buildAddedDecorations> = [];
  for (const hunk of diffFile.hunks) {
    let newLine = hunk.newStart;
    for (const line of hunk.lines) {
      if (line.type === "addition") {
        decorations.push({
          range: { startLineNumber: newLine, startColumn: 1, endLineNumber: newLine, endColumn: 1 },
          options: { isWholeLine: true, className: "diff-add-line", linesDecorationsClassName: "diff-add-decoration" },
        });
        newLine++;
      } else if (line.type === "context") {
        newLine++;
      }
      // deletion lines don't exist in the new file — skip
    }
  }
  return decorations;
}

export default function WorkspaceBrowser({ jobId }: Props) {
  const [entries, setEntries] = useState<TreeEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const isMobile = useIsMobile();
  const [mdMode, setMdMode] = useState<"preview" | "raw">("preview");

  // Monaco editor refs for diff decoration management
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const editorRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const decorationsRef = useRef<any>(null);

  const diffs = useStore(selectJobDiffs(jobId));

  const diffMap = useMemo(() => {
    const map = new Map<string, DiffFileModel>();
    for (const d of diffs) map.set(d.path, d);
    return map;
  }, [diffs]);

  // Always-current refs so handleEditorMount can read the latest selected/diffMap
  // without being invalidated by a stale closure (handleEditorMount deps are []).
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const diffMapRef = useRef(diffMap);
  diffMapRef.current = diffMap;

  const changedDirs = useMemo(() => {
    const dirs = new Set<string>();
    for (const d of diffs) {
      const parts = d.path.split("/");
      for (let i = 1; i < parts.length; i++) {
        dirs.add(parts.slice(0, i).join("/"));
      }
    }
    return dirs;
  }, [diffs]);

  // Re-apply diff decorations whenever the selected file or its content changes.
  useEffect(() => {
    const ed = editorRef.current;
    const coll = decorationsRef.current;
    // ed.getModel() returns null when the editor has been disposed (e.g. the
    // loading spinner unmounted a previous editor but the ref was not cleared).
    if (!ed || !coll || !ed.getModel()) return;

    const diffFile = selected ? diffMap.get(selected) : undefined;
    if (!diffFile) {
      coll.set([]);
      return;
    }
    const totalLines = ed.getModel()?.getLineCount() ?? 0;
    coll.set(buildAddedDecorations(diffFile, totalLines));
  }, [selected, diffMap, fileContent]);

  const handleEditorMount: OnMount = useCallback((ed) => {
    editorRef.current = ed;
    decorationsRef.current = ed.createDecorationsCollection([]);
    // Use always-current refs so we never read stale selected/diffMap values.
    const diffFile = selectedRef.current ? diffMapRef.current.get(selectedRef.current) : undefined;
    if (diffFile) {
      const totalLines = ed.getModel()?.getLineCount() ?? 0;
      decorationsRef.current.set(buildAddedDecorations(diffFile, totalLines));
    }
  }, []);

  useEffect(() => {
    fetchWorkspaceFiles(jobId)
      .then((res) => setEntries(res.items))
      .catch((err) => console.error("Failed to fetch workspace files", err))
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

  if (loading) return <div className="flex justify-center py-10"><Spinner /></div>;

  const showMdToggle = selected != null && isMarkdown(selected) && fileContent != null && !fileLoading;
  const mobileShowFile = isMobile && selected != null;

  const treePanel = (
    <div className={cn(
      "shrink-0 flex flex-col overflow-hidden rounded-lg border border-border bg-card",
      isMobile ? "flex-1" : "w-64",
    )}>
      <div className="px-3 py-2.5 border-b border-border">
        <span className="text-xs font-semibold text-muted-foreground">Files</span>
      </div>
      {diffs.length > 0 && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground px-2 py-1 border-b border-border">
          <span className="text-emerald-400">{diffs.filter(d => d.status === "added").length} added</span>
          <span className="text-blue-400">{diffs.filter(d => d.status === "modified").length} modified</span>
          <span className="text-red-400">{diffs.filter(d => d.status === "deleted").length} deleted</span>
        </div>
      )}
      <div className="flex-1 overflow-y-auto py-1">
        {entries.map((e) => (
          <TreeNode key={e.path} entry={e} depth={0} selected={selected} onSelect={handleSelect} jobId={jobId} isMobile={isMobile} diffMap={diffMap} changedDirs={changedDirs} />
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
            {isMobile ? (
              <Suspense fallback={<div className="flex items-center justify-center h-full"><Spinner /></div>}>
                <MobileSyntaxView
                  content={fileContent || ""}
                  language={getLanguageFromPath(selected)}
                  diffHunks={diffMap.get(selected)?.hunks}
                />
              </Suspense>
            ) : (
              <Editor
                value={fileContent}
                language={guessLang(selected)}
                theme="vs-dark"
                onMount={handleEditorMount}
                options={{
                  readOnly: true,
                  minimap: { enabled: false },
                  scrollBeyondLastLine: false,
                  fontSize: 13,
                  lineNumbersMinChars: 3,
                  glyphMargin: false,
                  lineDecorationsWidth: 4,
                  folding: true,
                }}
              />
            )}
          </div>
        )
      ) : (
        <p className="text-sm text-muted-foreground text-center py-8">Select a file to preview</p>
      )}
    </div>
  );

  if (isMobile) {
    return (
      <div className="flex flex-col h-[calc(100vh-14rem)] md:h-[60vh] min-h-[300px]">
        {mobileShowFile ? filePanel : treePanel}
      </div>
    );
  }

  return (
    <div className="flex flex-row gap-3 h-[calc(100vh-14rem)] md:h-[60vh] min-h-[300px] max-h-[600px]">
      {treePanel}
      {filePanel}
    </div>
  );
}
