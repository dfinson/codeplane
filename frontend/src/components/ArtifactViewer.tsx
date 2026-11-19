import { useCallback, useEffect, useMemo, useState } from "react";
import {
  type LucideIcon,
  Download,
  FileText,
  FileCode,
  ChevronDown,
  ChevronRight,
  BookOpen,
  ScrollText,
  Activity,
  ShieldCheck,
  ClipboardList,
  Archive,
  Terminal,
  Package,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import SyntaxHighlighter from "react-syntax-highlighter/dist/esm/prism-async-light";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { fetchArtifacts, downloadArtifactUrl, fetchArtifactText } from "../api/client";
import type { ArtifactListResponse } from "../api/types";
import { Spinner } from "./ui/spinner";
import { Tooltip } from "./ui/tooltip";
import { useStore } from "../store";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Artifact {
  id: string;
  jobId: string;
  name: string;
  type: string;
  mimeType: string;
  sizeBytes: number;
  phase: string;
  createdAt: string;
}

type CollectionStatus = ArtifactListResponse["collectionStatus"];

/** Display key — the actual group a given artifact is rendered under.
 *  This may differ from the raw `type` (e.g. legacy `document`-typed
 *  agent.log files are reclassified to `agent_log`). */
type DisplayType =
  | "agent_summary"
  | "diff_snapshot"
  | "session_log"
  | "agent_log"
  | "approval_history"
  | "document"
  | "custom"
  | "exports";

// ---------------------------------------------------------------------------
// Icons, labels, descriptions, and sort order
// ---------------------------------------------------------------------------

const TYPE_ICON: Record<string, LucideIcon> = {
  agent_summary: ClipboardList,
  diff_snapshot: FileCode,
  session_log: ScrollText,
  agent_log: Terminal,
  approval_history: ShieldCheck,
  document: BookOpen,
  custom: Package,
  exports: Activity,
  // Legacy / fallback
  session_snapshot: Archive,
};

const TYPE_LABEL: Record<string, string> = {
  agent_summary: "Session Summaries",
  diff_snapshot: "Code Changes",
  session_log: "Full Transcript",
  agent_log: "Agent Debug Log",
  approval_history: "Approval Log",
  document: "Agent Files",
  custom: "Other Files",
  exports: "Data Exports",
};

const TYPE_DESCRIPTION: Record<string, string> = {
  agent_summary: "LLM-generated summary of what was accomplished, key decisions, and resume context per session.",
  diff_snapshot: "Snapshot of all code changes made by the agent, serialized as JSON.",
  session_log: "Raw transcript of all tool calls and agent actions across sessions.",
  agent_log: "Timestamped log of LLM calls and tool invocations for debugging.",
  approval_history: "Chronological record of all approval requests and their resolutions.",
  document: "Markdown, text, and other files created by the agent during execution.",
  custom: "Non-text files placed by the agent in .codeplane/artifacts/.",
  exports: "Machine-readable data also visible in other tabs (Metrics, Progress).",
};

/** Groups that are redundant with other tabs — collapsed into "Data Exports". */
const EXPORT_TYPES = new Set(["telemetry_report", "agent_plan"]);

/** Preferred display order — lower = higher in the list. */
const GROUP_ORDER: Record<string, number> = {
  agent_summary: 0,
  diff_snapshot: 1,
  document: 2,
  session_log: 3,
  agent_log: 4,
  approval_history: 5,
  custom: 6,
  exports: 99,
};

// ---------------------------------------------------------------------------
// Classify artifacts into display groups
// ---------------------------------------------------------------------------

function classifyArtifact(a: Artifact): DisplayType {
  // Telemetry + plan → "exports" bucket
  if (EXPORT_TYPES.has(a.type)) return "exports";
  // Legacy: agent.log stored as type=document → reclassify
  if (a.type === "document" && a.name.endsWith("agent.log")) return "agent_log";
  // New type from backend
  if (a.type === "agent_log") return "agent_log";
  // Legacy session_snapshot → treat as session_log
  if (a.type === "session_snapshot") return "session_log";
  return a.type as DisplayType;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const PREVIEWABLE_MIMES = new Set([
  "text/plain",
  "text/markdown",
  "text/html",
  "text/csv",
  "application/json",
]);

const IMAGE_MIMES = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
  "image/svg+xml",
  "image/bmp",
  "image/x-icon",
  "image/avif",
]);

const VIDEO_MIMES = new Set([
  "video/mp4",
  "video/webm",
  "video/ogg",
]);

const PDF_MIME = "application/pdf";

function isMediaPreviewable(a: Artifact): boolean {
  return IMAGE_MIMES.has(a.mimeType) || VIDEO_MIMES.has(a.mimeType) || a.mimeType === PDF_MIME;
}

function isPreviewable(a: Artifact): boolean {
  if (PREVIEWABLE_MIMES.has(a.mimeType) && a.sizeBytes < 512 * 1024) return true;
  if (isMediaPreviewable(a)) return true;
  return false;
}

function isMarkdownArtifact(a: Artifact): boolean {
  return a.mimeType === "text/markdown" || a.name.split(".").pop()?.toLowerCase() === "md";
}

function isJsonArtifact(a: Artifact): boolean {
  return a.mimeType === "application/json" || a.name.split(".").pop()?.toLowerCase() === "json";
}

function getLanguageFromName(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  const map: Record<string, string> = {
    ts: "typescript", tsx: "tsx", js: "javascript", jsx: "jsx",
    py: "python", rs: "rust", go: "go", rb: "ruby",
    java: "java", kt: "kotlin", swift: "swift", cs: "csharp",
    cpp: "cpp", c: "c", h: "c", hpp: "cpp",
    css: "css", scss: "scss", html: "html", xml: "xml",
    json: "json", yaml: "yaml", yml: "yaml", toml: "toml",
    md: "markdown", sql: "sql", sh: "bash", bash: "bash",
    dockerfile: "docker", makefile: "makefile",
    csv: "csv", log: "log", txt: "text",
  };
  return map[ext] || "text";
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ---------------------------------------------------------------------------
// JSON Tree Viewer — collapsible key/value tree for JSON artifacts
// ---------------------------------------------------------------------------

function JsonNode({ name, value, depth }: { name?: string; value: unknown; depth: number }) {
  const [open, setOpen] = useState(depth < 2);

  if (value === null || value === undefined) {
    return (
      <div className="flex items-baseline gap-1" style={{ paddingLeft: depth * 16 }}>
        {name != null && <span className="text-blue-400">{name}:</span>}
        <span className="text-muted-foreground italic">null</span>
      </div>
    );
  }

  if (typeof value === "object" && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, unknown>);
    return (
      <div style={{ paddingLeft: depth * 16 }}>
        <button onClick={() => setOpen(!open)} className="flex items-center gap-1 hover:text-foreground transition-colors text-left">
          {open ? <ChevronDown size={10} className="shrink-0" /> : <ChevronRight size={10} className="shrink-0" />}
          {name != null && <span className="text-blue-400">{name}</span>}
          <span className="text-muted-foreground">{`{${entries.length}}`}</span>
        </button>
        {open && entries.map(([k, v]) => <JsonNode key={k} name={k} value={v} depth={depth + 1} />)}
      </div>
    );
  }

  if (Array.isArray(value)) {
    return (
      <div style={{ paddingLeft: depth * 16 }}>
        <button onClick={() => setOpen(!open)} className="flex items-center gap-1 hover:text-foreground transition-colors text-left">
          {open ? <ChevronDown size={10} className="shrink-0" /> : <ChevronRight size={10} className="shrink-0" />}
          {name != null && <span className="text-blue-400">{name}</span>}
          <span className="text-muted-foreground">[{value.length}]</span>
        </button>
        {open && value.map((item, i) => <JsonNode key={i} name={String(i)} value={item} depth={depth + 1} />)}
      </div>
    );
  }

  // Primitive values
  const display = typeof value === "string"
    ? <span className="text-green-400">"{value.length > 200 ? value.slice(0, 200) + "…" : value}"</span>
    : typeof value === "number"
      ? <span className="text-amber-400">{value}</span>
      : typeof value === "boolean"
        ? <span className="text-purple-400">{String(value)}</span>
        : <span>{String(value)}</span>;

  return (
    <div className="flex items-baseline gap-1 flex-wrap" style={{ paddingLeft: depth * 16 }}>
      {name != null && <span className="text-blue-400">{name}:</span>}
      {display}
    </div>
  );
}

function JsonTreeViewer({ content }: { content: string }) {
  try {
    const parsed = JSON.parse(content);
    return (
      <div className="max-h-80 overflow-y-auto bg-background/50 rounded-md border border-border/50 p-4 text-xs font-mono leading-relaxed">
        <JsonNode value={parsed} depth={0} />
      </div>
    );
  } catch {
    return (
      <div className="max-h-80 overflow-y-auto bg-background/50 rounded-md border border-border/50 p-4">
        <pre className="text-xs text-foreground/80 whitespace-pre-wrap break-words font-mono leading-relaxed">{content}</pre>
      </div>
    );
  }
}

// ---------------------------------------------------------------------------
// Syntax-highlighted code block for artifact raw view
// ---------------------------------------------------------------------------

function ArtifactSyntaxView({ content, language }: { content: string; language: string }) {
  return (
    <div className="max-h-80 overflow-y-auto bg-background/50 rounded-md border border-border/50">
      <SyntaxHighlighter
        language={language}
        style={oneDark}
        customStyle={{ margin: 0, padding: "1rem", background: "transparent", fontSize: "0.75rem", lineHeight: "1.625" }}
        wrapLongLines
      >
        {content}
      </SyntaxHighlighter>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Preview
// ---------------------------------------------------------------------------

type PreviewMode = "preview" | "raw";

function ArtifactPreview({ artifact }: { artifact: Artifact }) {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<PreviewMode>("preview");

  const isMd = isMarkdownArtifact(artifact);
  const isJson = isJsonArtifact(artifact);
  const isMedia = isMediaPreviewable(artifact);
  const showToggle = isMd || isJson;

  useEffect(() => {
    // Media files don't need text content fetching
    if (isMedia) {
      setLoading(false);
      return;
    }
    setMode("preview");
    fetchArtifactText(artifact.id)
      .then(setContent)
      .catch(() => setContent("(failed to load preview)"))
      .finally(() => setLoading(false));
  }, [artifact.id, isMedia]);

  if (loading) return <div className="py-4 flex justify-center"><Spinner /></div>;

  // Media preview: images, videos, PDFs
  if (isMedia) {
    const url = downloadArtifactUrl(artifact.id);
    if (IMAGE_MIMES.has(artifact.mimeType)) {
      return (
        <div className="max-h-96 overflow-auto bg-background/50 rounded-md border border-border/50 p-4 flex items-center justify-center">
          <img src={url} alt={artifact.name} className="max-w-full max-h-80 object-contain rounded" />
        </div>
      );
    }
    if (VIDEO_MIMES.has(artifact.mimeType)) {
      return (
        <div className="max-h-96 overflow-auto bg-background/50 rounded-md border border-border/50 p-4 flex items-center justify-center">
          <video src={url} controls className="max-w-full max-h-80 rounded">
            Your browser does not support the video element.
          </video>
        </div>
      );
    }
    if (artifact.mimeType === PDF_MIME) {
      return (
        <div className="bg-background/50 rounded-md border border-border/50 overflow-hidden">
          <iframe src={url} title={artifact.name} className="w-full h-96 border-0" />
        </div>
      );
    }
  }

  if (content == null) return null;

  const toggleBar = showToggle && (
    <div className="flex items-center gap-1 mb-2">
      <button
        type="button"
        onClick={() => setMode("preview")}
        className={`px-2.5 py-0.5 rounded text-xs font-medium transition-colors ${
          mode === "preview" ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground"
        }`}
      >
        {isJson ? "Tree" : "Preview"}
      </button>
      <button
        type="button"
        onClick={() => setMode("raw")}
        className={`px-2.5 py-0.5 rounded text-xs font-medium transition-colors ${
          mode === "raw" ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground"
        }`}
      >
        Raw
      </button>
    </div>
  );

  // Markdown: rendered preview or syntax-highlighted raw
  if (isMd) {
    return (
      <div>
        {toggleBar}
        {mode === "preview" ? (
          <div className="max-h-80 overflow-y-auto bg-background/50 rounded-md border border-border/50 p-5 prose prose-sm prose-invert max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>{content}</ReactMarkdown>
          </div>
        ) : (
          <ArtifactSyntaxView content={content} language="markdown" />
        )}
      </div>
    );
  }

  // JSON: tree viewer or syntax-highlighted raw
  if (isJson) {
    return (
      <div>
        {toggleBar}
        {mode === "preview" ? (
          <JsonTreeViewer content={content} />
        ) : (
          <ArtifactSyntaxView content={content} language="json" />
        )}
      </div>
    );
  }

  // All other text files: syntax-highlighted by detected language
  return <ArtifactSyntaxView content={content} language={getLanguageFromName(artifact.name)} />;
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function ArtifactRow({ artifact, displayType }: { artifact: Artifact; displayType: DisplayType }) {
  const [expanded, setExpanded] = useState(false);
  const Icon = TYPE_ICON[displayType] ?? TYPE_ICON[artifact.type] ?? FileText;
  const canPreview = isPreviewable(artifact);

  // Show a softer label for exports: e.g. "telemetry_report" → "Telemetry"
  const exportSubLabel = artifact.type === "telemetry_report" ? "Telemetry" : artifact.type === "agent_plan" ? "Plan" : null;

  return (
    <>
      <tr className="border-b border-border/50 last:border-0 hover:bg-accent/30">
        <td className="pl-10 pr-4 py-2.5">
          <div className="flex items-center gap-2">
            {canPreview ? (
              <button
                onClick={() => setExpanded((e) => !e)}
                aria-expanded={expanded}
                className="flex items-center gap-1.5 text-left hover:text-foreground transition-colors"
              >
                {expanded ? <ChevronDown size={12} className="text-muted-foreground shrink-0" /> : <ChevronRight size={12} className="text-muted-foreground shrink-0" />}
                <Icon size={14} className="text-muted-foreground shrink-0" />
                <span className="truncate">{artifact.name}</span>
                {exportSubLabel && <span className="text-[10px] text-muted-foreground px-1.5 py-0.5 rounded bg-accent/50">{exportSubLabel}</span>}
              </button>
            ) : (
              <>
                <Icon size={14} className="text-muted-foreground shrink-0" />
                <span className="truncate">{artifact.name}</span>
                {exportSubLabel && <span className="text-[10px] text-muted-foreground px-1.5 py-0.5 rounded bg-accent/50">{exportSubLabel}</span>}
              </>
            )}
          </div>
        </td>
        <td className="px-4 py-2.5 text-muted-foreground text-xs">{formatSize(artifact.sizeBytes)}</td>
        <td className="px-4 py-2.5 text-muted-foreground text-xs hidden sm:table-cell">{new Date(artifact.createdAt).toLocaleString()}</td>
        <td className="px-4 py-2.5 text-right">
          <Tooltip content={`Download ${artifact.name}`}>
            <a
              href={downloadArtifactUrl(artifact.id)}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`Download ${artifact.name}`}
              className="inline-flex items-center justify-center w-8 h-8 text-muted-foreground hover:text-foreground transition-colors"
            >
              <Download size={14} aria-hidden="true" />
            </a>
          </Tooltip>
        </td>
      </tr>
      {expanded && canPreview && (
        <tr>
          <td colSpan={4} className="pl-10 pr-4 py-3">
            <ArtifactPreview artifact={artifact} />
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Group
// ---------------------------------------------------------------------------

function ArtifactGroup({ displayType, artifacts }: { displayType: DisplayType; artifacts: Artifact[] }) {
  const [open, setOpen] = useState(false);
  const Icon = TYPE_ICON[displayType] ?? FileText;
  const label = TYPE_LABEL[displayType] ?? displayType.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const description = TYPE_DESCRIPTION[displayType];
  const totalSize = artifacts.reduce((sum, a) => sum + a.sizeBytes, 0);

  return (
    <div className="border-b border-border/50 last:border-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2.5 px-4 py-2.5 hover:bg-accent/30 transition-colors text-left"
      >
        {open ? (
          <ChevronDown size={13} className="text-muted-foreground shrink-0" />
        ) : (
          <ChevronRight size={13} className="text-muted-foreground shrink-0" />
        )}
        <Icon size={14} className="text-muted-foreground shrink-0" />
        <span className="text-sm font-medium">{label}</span>
        <span className="text-xs text-muted-foreground ml-1">
          ({artifacts.length}{artifacts.length > 1 ? ` · ${formatSize(totalSize)}` : ""})
        </span>
      </button>
      {open && (
        <>
          {description && (
            <p className="text-xs text-muted-foreground px-4 pb-2 pl-10">{description}</p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <tbody>
                {artifacts.map((a) => (
                  <ArtifactRow key={a.id} artifact={a} displayType={displayType} />
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface Props {
  jobId: string;
  /** Callback to report the artifact count up to the parent (for tab badge). */
  onCountChange?: (count: number) => void;
}

export default function ArtifactViewer({ jobId, onCountChange }: Props) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [collectionStatus, setCollectionStatus] = useState<CollectionStatus>("pending");
  const [collectionError, setCollectionError] = useState<string | null>(null);

  // Subscribe to job state changes so artifacts refresh when job reaches
  // review/completed/failed states (when post-completion artifacts are created).
  const jobState = useStore((s) => s.jobs[jobId]?.state);
  const artifactVersion = useStore((s) => s.artifactVersions[jobId] ?? 0);

  const loadArtifacts = useCallback(async () => {
    return fetchArtifacts(jobId)
      .then((res) => {
        const items = res.items as Artifact[];
        setArtifacts(items);
        setCollectionStatus(res.collectionStatus);
        setCollectionError(res.collectionError ?? null);
        onCountChange?.(items.length);
        return res.collectionStatus;
      })
      .catch((err) => {
        console.error("Failed to fetch artifacts", err);
        throw err;
      })
      .finally(() => setLoading(false));
  }, [jobId, onCountChange]);

  // Load on mount + whenever job state changes (covers session end + completion)
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = () => {
      loadArtifacts()
        .then((status) => {
          const terminal = ["review", "completed", "failed", "canceled"].includes(jobState ?? "");
          if (!cancelled && terminal && (status === "pending" || status === "collecting")) {
            timer = setTimeout(refresh, 1000);
          }
        })
        .catch(() => {
          // The error is surfaced in the console; a later state/SSE update retries.
        });
    };
    refresh();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [loadArtifacts, jobState, artifactVersion]);

  // Classify and group
  const sortedGroups = useMemo(() => {
    const groups: Record<string, Artifact[]> = {};
    for (const a of artifacts) {
      const dt = classifyArtifact(a);
      (groups[dt] ??= []).push(a);
    }
    return Object.entries(groups).sort(
      ([a], [b]) => (GROUP_ORDER[a] ?? 50) - (GROUP_ORDER[b] ?? 50),
    );
  }, [artifacts]);

  if (loading) return <div className="flex justify-center py-10"><Spinner /></div>;

  if (artifacts.length === 0) {
    const collecting = collectionStatus === "pending" || collectionStatus === "collecting";
    return (
      <div className="rounded-lg border border-border bg-card p-8 text-center">
        {collecting && <div className="flex justify-center mb-3"><Spinner /></div>}
        <p className="text-sm text-muted-foreground">
          {collectionStatus === "failed" ? "Artifact collection failed" : collecting ? "Collecting artifacts…" : "No artifacts collected"}
        </p>
        {collectionError ? (
          <p className="text-xs text-destructive mt-2 break-words">{collectionError}</p>
        ) : (
          <p className="text-xs text-muted-foreground mt-1">
            {collectionStatus === "not_applicable"
              ? "This is observation history, not an executed workspace session."
              : "Artifacts are finalized when the session ends."}
          </p>
        )}
      </div>
    );
  }

  const totalSize = artifacts.reduce((sum, a) => sum + a.sizeBytes, 0);

  return (
    <div>
      {collectionStatus === "failed" && (
        <div className="mb-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          Artifact refresh failed: {collectionError ?? "Unknown collection error"}
        </div>
      )}
      <div className="flex items-center justify-between mb-2 px-1">
        <p className="text-xs text-muted-foreground">
          {artifacts.length} artifact{artifacts.length !== 1 ? "s" : ""} · {formatSize(totalSize)} total
        </p>
      </div>
      <div className="rounded-lg border border-border bg-card overflow-hidden">
        {sortedGroups.map(([type, items]) => (
          <ArtifactGroup key={type} displayType={type as DisplayType} artifacts={items} />
        ))}
      </div>
    </div>
  );
}
