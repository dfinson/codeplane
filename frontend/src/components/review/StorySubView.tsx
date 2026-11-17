/**
 * Story sub-view — trail-based narrative code review story.
 *
 * Stories are pre-generated in the background. This component polls
 * until the story is ready, then renders it.
 */
import React, { useState, useEffect, useCallback, useRef } from "react";
import { AlertTriangle, BookOpen, RefreshCw } from "lucide-react";
import { fetchJobStory } from "../../api/client";
import { useStore } from "../../store";
import { selectJobStory } from "../../store/selectors";
import type { StoryBlock } from "../../api/types";
import { Spinner } from "../ui/spinner";
import { cn } from "../../lib/utils";

interface StorySubViewProps {
  jobId: string;
}

/** Split text on `backtick` spans and render inline <code> elements. */
function renderInlineCode(text: string): React.ReactNode[] {
  const parts = text.split(/(`[^`]+`)/g);
  return parts.map((part, i) => {
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={i}
          className="font-mono text-[0.85em] text-primary/80 bg-muted/40 px-1 py-px rounded break-all"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

/** Split block text on double-newlines into separate <p> elements. */
function renderParagraphs(text: string, keyPrefix: string): React.ReactNode[] {
  const paragraphs = text.split(/\n\n+/).filter(Boolean);
  return paragraphs.map((p, i) => (
    <p key={`${keyPrefix}-p${i}`} className="m-0">{renderInlineCode(p.trim())}</p>
  ));
}

/** Render a unified diff snippet with add/remove line highlighting. */
function DiffCard({ file, snippet }: { file: string; snippet: string }) {
  // Strip the "diff --git" header and "---/+++" lines, keep only hunks
  const lines = snippet.split("\n");
  const hunkLines: string[] = [];
  let inHunk = false;
  for (const line of lines) {
    if (line.startsWith("@@")) {
      inHunk = true;
      hunkLines.push(line);
    } else if (inHunk) {
      hunkLines.push(line);
    }
  }
  const displayLines = hunkLines.length > 0 ? hunkLines : lines;

  return (
    <div className="rounded border border-border/50 bg-muted/20 overflow-hidden my-2">
      <div className="flex items-center gap-2 px-3 py-1.5 bg-muted/30 border-b border-border/30 min-w-0">
        <span className="font-mono text-[11px] text-primary/80 truncate min-w-0">{file}</span>
      </div>
      <pre className="px-0 py-1 font-mono text-[11px] overflow-x-auto whitespace-pre m-0">
        {displayLines.map((line, i) => {
          let cls = "text-foreground/60 px-3";
          if (line.startsWith("+") && !line.startsWith("+++")) {
            cls = "text-green-400/90 bg-green-400/10 px-3";
          } else if (line.startsWith("-") && !line.startsWith("---")) {
            cls = "text-red-400/90 bg-red-400/10 px-3";
          } else if (line.startsWith("@@")) {
            cls = "text-blue-400/60 px-3";
          }
          return <div key={i} className={cls}>{line || " "}</div>;
        })}
      </pre>
    </div>
  );
}

/** Build a categorized file overview from story blocks. */
function StoryTOC({ blocks }: { blocks: StoryBlock[] }) {
  const refs = blocks.filter(
    (b): b is StoryBlock & { file: string } => b.type === "reference" && !!b.file,
  );
  if (refs.length === 0) return null;

  // Deduplicate: keep first occurrence of each file
  const seen = new Set<string>();
  const unique: Array<{ file: string; action: "created" | "modified" | "read" }> = [];
  for (const r of refs) {
    if (!seen.has(r.file)) {
      seen.add(r.file);
      unique.push({ file: r.file, action: r.action ?? "modified" });
    }
  }

  const created = unique.filter((u) => u.action === "created");
  const modified = unique.filter((u) => u.action === "modified");
  const read = unique.filter((u) => u.action === "read");

  const FileList = ({ items, label, color }: { items: typeof unique; label: string; color: string }) => {
    if (items.length === 0) return null;
    return (
      <div className="flex items-start gap-2 min-w-0">
        <span className={`text-[10px] font-semibold uppercase tracking-wider shrink-0 mt-px ${color}`}>
          {label}
        </span>
        <div className="flex flex-wrap gap-x-2 gap-y-1 min-w-0">
          {items.map((u) => (
            <span
              key={u.file}
              className="font-mono text-[11px] text-primary/70 bg-muted/30 px-1.5 py-0.5 rounded break-all"
            >
              {u.file}
            </span>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className="border border-border/30 rounded-md bg-muted/10 p-3 mb-2 flex flex-col gap-2">
      <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
        Files &middot; {unique.length}
      </span>
      <FileList items={modified} label="Modified" color="text-yellow-500/80" />
      <FileList items={created} label="Created" color="text-green-500/80" />
      <FileList items={read} label="Read" color="text-blue-500/80" />
    </div>
  );
}

/** Trail-based narrative of the job. */
function TrailStory({ jobId }: { jobId: string }) {
  const setStory = useStore((s) => s.setStory);

  const [regenerating, setRegenerating] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const story = useStore(selectJobStory(jobId));

  const fetchStory = useCallback(
    async (regen = false) => {
      setFetching(true);
      setError(null);
      try {
        const res = await fetchJobStory(jobId, regen);
        if (res.blocks && res.blocks.length > 0) {
          setStory(jobId, res);
          setPending(false);
        } else if (res.pending) {
          // Story is being generated — poll again shortly
          setPending(true);
          if (pollTimer.current) clearTimeout(pollTimer.current);
          pollTimer.current = setTimeout(() => fetchStory(), 4000);
        }
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Failed to load story";
        setError(msg);
      } finally {
        setFetching(false);
      }
    },
    [jobId, setStory],
  );

  // Cleanup poll timer on unmount
  useEffect(() => {
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, []);

  // Fetch story on mount
  useEffect(() => {
    if (!story) {
      fetchStory();
    }
  }, [jobId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRegenerate = useCallback(() => {
    setRegenerating(true);
    setError(null);
    fetchStory(true).finally(() => setRegenerating(false));
  }, [fetchStory]);

  const loading = (fetching || pending) && !story;
  const hasStory = story && story.blocks.length > 0;

  const controls = (
    <div className="flex items-center justify-end mt-3 pt-2 border-t border-border/30">
      <button
        type="button"
        disabled={regenerating}
        onClick={handleRegenerate}
        className="flex items-center gap-1 text-[10px] text-muted-foreground/60 hover:text-muted-foreground transition-colors"
      >
        <RefreshCw size={10} className={cn(regenerating && "animate-spin")} />
        {regenerating ? "Regenerating\u2026" : "Regenerate"}
      </button>
    </div>
  );

  if (loading) {
    return (
      <div className="rounded-lg border border-border bg-card p-4 h-full flex flex-col justify-center">
        <div className="flex flex-col items-center justify-center gap-2">
          <Spinner size="md" />
          {pending && (
            <span className="text-xs text-muted-foreground">Generating story…</span>
          )}
        </div>
        {controls}
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-border bg-card p-4 h-full flex flex-col justify-center">
        <div className="flex items-center gap-2 justify-center text-sm text-muted-foreground">
          <AlertTriangle size={16} className="text-yellow-400" />
          <span>{error}</span>
        </div>
        {controls}
      </div>
    );
  }

  if (!hasStory) {
    return (
      <div className="rounded-lg border border-border bg-card p-4 h-full flex flex-col justify-center">
        <div className="flex flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
          <p>No story available yet.</p>
          <button
            type="button"
            disabled={regenerating}
            onClick={handleRegenerate}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
          >
            <RefreshCw size={12} className={cn(regenerating && "animate-spin")} />
            {regenerating ? "Generating\u2026" : "Try generating"}
          </button>
        </div>
        {controls}
      </div>
    );
  }

  return (
    <div className="overflow-hidden min-w-0 h-full flex">
      {/* Sidebar — timeline-style navigation */}
      <StoryTimelineSidebar blocks={story.blocks} scrollRef={scrollRef} />
      {/* Story content */}
      <div ref={scrollRef} className="flex-1 min-w-0 overflow-y-auto px-6 py-4">
        <StoryTOC blocks={story.blocks} />
        <div className="text-sm text-foreground/80 leading-relaxed flex flex-col gap-3 min-w-0 break-words">
          {story.blocks.map((block: StoryBlock, i: number) => {
            if (block.type === "heading" && block.text) {
              const level = block.level ?? 2;
              return (
                <div key={`h-${i}`} id={`story-heading-${i}`}>
                  {level <= 2 ? (
                    <h4
                      className={cn(
                        "text-sm font-semibold text-foreground/90 pt-4 pb-1",
                        i > 0 && "border-t border-border/20 mt-2",
                      )}
                    >
                      {block.text}
                    </h4>
                  ) : (
                    <h5 className="text-[13px] font-medium text-foreground/80 pt-2 pb-0.5">
                      {block.text}
                    </h5>
                  )}
                </div>
              );
            }
            if ((block.type === "narrative" || block.type === "beat") && block.text) {
              return (
                <React.Fragment key={`t-${i}`}>
                  {renderParagraphs(block.text, `t-${i}`)}
                </React.Fragment>
              );
            }
            if (block.type === "reference" && block.file && block.snippet) {
              return <DiffCard key={`r-${i}`} file={block.file} snippet={block.snippet} />;
            }
            return null;
          })}
        </div>
        {controls}
      </div>
    </div>
  );
}

/** Timeline-style sidebar — mirrors the ActivityTimeline visual language. */
function StoryTimelineSidebar({
  blocks,
  scrollRef,
}: {
  blocks: StoryBlock[];
  scrollRef: React.RefObject<HTMLDivElement | null>;
}) {
  // Build hierarchy: ## sections with ### children
  const sections: Array<{
    idx: number;
    text: string;
    children: Array<{ idx: number; text: string }>;
  }> = [];
  for (const [i, b] of blocks.entries()) {
    if (b.type !== "heading" || !b.text) continue;
    const text = b.text;
    const level = b.level ?? 2;
    if (level <= 2) {
      sections.push({ idx: i, text, children: [] });
    } else {
      const last = sections[sections.length - 1];
      if (last) last.children.push({ idx: i, text });
    }
  }

  const [activeIdx, setActiveIdx] = useState<number | null>(null);

  useEffect(() => {
    const container = scrollRef.current;
    if (!container || sections.length === 0) return;

    const allHeadingIds = sections.flatMap((s) => [
      s.idx,
      ...s.children.map((c) => c.idx),
    ]);

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            const id = entry.target.getAttribute("id");
            if (id) {
              setActiveIdx(parseInt(id.replace("story-heading-", ""), 10));
            }
            break;
          }
        }
      },
      { root: container, rootMargin: "-10% 0px -80% 0px", threshold: 0 },
    );

    for (const idx of allHeadingIds) {
      const el = container.querySelector(`#story-heading-${idx}`);
      if (el) observer.observe(el);
    }

    return () => observer.disconnect();
  }, [sections, scrollRef]);

  if (sections.length < 2) return null;

  const scrollTo = (idx: number) => {
    const el = scrollRef.current?.querySelector(`#story-heading-${idx}`);
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  // Determine which section is "active" (contains the active heading)
  const activeSectionIdx = sections.findIndex((s) =>
    s.idx === activeIdx || s.children.some((c) => c.idx === activeIdx),
  );

  return (
    <nav className="w-52 shrink-0 border-r border-border/30 overflow-y-auto py-3 px-3 hidden lg:block">
      <div className="flex items-center gap-1.5 mb-3 px-1">
        <BookOpen size={13} className="text-muted-foreground" />
        <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Story</span>
      </div>
      <div className="space-y-0.5">
        {sections.map((section, si) => {
          const isSectionActive = si === activeSectionIdx;
          return (
            <div key={section.idx}>
              {/* Section header — like an ActivitySection */}
              <button
                type="button"
                onClick={() => scrollTo(section.idx)}
                className={cn(
                  "flex items-center gap-2 w-full text-left py-1.5 px-1 rounded-sm transition-colors",
                  isSectionActive
                    ? "text-foreground"
                    : "text-muted-foreground hover:text-foreground/80 hover:bg-accent/50",
                )}
              >
                <span
                  className={cn(
                    "w-2 h-2 rounded-full shrink-0",
                    isSectionActive ? "bg-blue-400" : "bg-muted-foreground/40",
                  )}
                />
                <span
                  className={cn(
                    "text-[12px] leading-snug truncate",
                    isSectionActive ? "font-semibold" : "font-medium",
                  )}
                  title={section.text}
                >
                  {section.text}
                </span>
              </button>
              {/* Sub-headings — like steps within an activity */}
              {section.children.length > 0 && isSectionActive && (
                <div className="ml-[11px] pl-2 border-l-2 border-border space-y-0.5 pb-1">
                  {section.children.map((child) => {
                    const isChildActive = child.idx === activeIdx;
                    return (
                      <button
                        key={child.idx}
                        type="button"
                        onClick={() => scrollTo(child.idx)}
                        className={cn(
                          "flex items-start gap-1.5 w-full text-left py-1 px-1 rounded-sm transition-colors",
                          isChildActive
                            ? "text-foreground bg-primary/5"
                            : "text-muted-foreground/70 hover:text-foreground/70 hover:bg-accent/50",
                        )}
                      >
                        <span
                          className={cn(
                            "w-1.5 h-1.5 rounded-full shrink-0 mt-[5px]",
                            isChildActive ? "bg-blue-400" : "bg-muted-foreground/30",
                          )}
                        />
                        <span
                          className={cn(
                            "text-[11px] leading-snug",
                            isChildActive && "font-medium text-foreground/80",
                          )}
                          title={child.text}
                        >
                          {child.text}
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </nav>
  );
}

export function StorySubView({ jobId }: StorySubViewProps) {
  return (
    <div className="h-full overflow-hidden">
      <TrailStory jobId={jobId} />
    </div>
  );
}
