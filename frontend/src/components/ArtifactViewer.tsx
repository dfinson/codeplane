import { useEffect, useState } from "react";
import { type LucideIcon, Download, FileText, FileCode, ChevronDown, ChevronRight, BookOpen, ScrollText, ListChecks, Activity, ShieldCheck } from "lucide-react";
import { fetchArtifacts, downloadArtifactUrl, fetchArtifactText } from "../api/client";
import { Badge } from "./ui/badge";
import { Spinner } from "./ui/spinner";

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

const TYPE_ICON: Record<string, LucideIcon> = {
  diff_snapshot: FileCode,
  session_log: ScrollText,
  agent_plan: ListChecks,
  telemetry_report: Activity,
  approval_history: ShieldCheck,
  document: BookOpen,
  custom: FileText,
};

const PREVIEWABLE_MIMES = new Set([
  "text/plain",
  "text/markdown",
  "text/html",
  "text/csv",
  "application/json",
]);

function isPreviewable(a: Artifact): boolean {
  return PREVIEWABLE_MIMES.has(a.mimeType) && a.sizeBytes < 512 * 1024;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function ArtifactPreview({ artifact }: { artifact: Artifact }) {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchArtifactText(artifact.id)
      .then(setContent)
      .catch(() => setContent("(failed to load preview)"))
      .finally(() => setLoading(false));
  }, [artifact.id]);

  if (loading) return <div className="py-4 flex justify-center"><Spinner /></div>;

  return (
    <div className="max-h-80 overflow-y-auto bg-background/50 rounded-md border border-border/50 p-4">
      <pre className="text-xs text-foreground/80 whitespace-pre-wrap break-words font-mono leading-relaxed">
        {content}
      </pre>
    </div>
  );
}

function ArtifactRow({ artifact }: { artifact: Artifact }) {
  const [expanded, setExpanded] = useState(false);
  const Icon = TYPE_ICON[artifact.type] ?? FileText;
  const canPreview = isPreviewable(artifact);

  return (
    <>
      <tr className="border-b border-border/50 hover:bg-accent/30">
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-2">
            {canPreview ? (
              <button
                onClick={() => setExpanded((e) => !e)}
                className="flex items-center gap-1.5 text-left hover:text-foreground transition-colors"
              >
                {expanded ? <ChevronDown size={12} className="text-muted-foreground shrink-0" /> : <ChevronRight size={12} className="text-muted-foreground shrink-0" />}
                <Icon size={14} className="text-muted-foreground shrink-0" />
                <span className="truncate">{artifact.name}</span>
              </button>
            ) : (
              <>
                <Icon size={14} className="text-muted-foreground shrink-0" />
                <span className="truncate">{artifact.name}</span>
              </>
            )}
          </div>
        </td>
        <td className="px-4 py-2.5 hidden sm:table-cell">
          <Badge variant="secondary">{artifact.type}</Badge>
        </td>
        <td className="px-4 py-2.5 text-muted-foreground">{formatSize(artifact.sizeBytes)}</td>
        <td className="px-4 py-2.5 text-muted-foreground text-xs hidden sm:table-cell">{new Date(artifact.createdAt).toLocaleString()}</td>
        <td className="px-4 py-2.5 text-right">
          <a
            href={downloadArtifactUrl(artifact.id)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center justify-center w-8 h-8 text-muted-foreground hover:text-foreground transition-colors"
          >
            <Download size={14} />
          </a>
        </td>
      </tr>
      {expanded && canPreview && (
        <tr>
          <td colSpan={5} className="px-4 py-3">
            <ArtifactPreview artifact={artifact} />
          </td>
        </tr>
      )}
    </>
  );
}

interface Props { jobId: string; }

export default function ArtifactViewer({ jobId }: Props) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchArtifacts(jobId)
      .then((res) => setArtifacts(res.items as Artifact[]))
      .catch((err) => console.error("Failed to fetch artifacts", err))
      .finally(() => setLoading(false));
  }, [jobId]);

  if (loading) return <div className="flex justify-center py-10"><Spinner /></div>;

  if (artifacts.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-8 text-center">
        <p className="text-sm text-muted-foreground">No artifacts available</p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              <th className="px-4 py-2.5 text-left text-xs font-semibold text-muted-foreground">Name</th>
              <th className="px-4 py-2.5 text-left text-xs font-semibold text-muted-foreground hidden sm:table-cell">Type</th>
              <th className="px-4 py-2.5 text-left text-xs font-semibold text-muted-foreground">Size</th>
              <th className="px-4 py-2.5 text-left text-xs font-semibold text-muted-foreground hidden sm:table-cell">Created</th>
              <th className="px-4 py-2.5 text-right text-xs font-semibold text-muted-foreground" />
            </tr>
          </thead>
          <tbody>
            {artifacts.map((a) => (
              <ArtifactRow key={a.id} artifact={a} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
