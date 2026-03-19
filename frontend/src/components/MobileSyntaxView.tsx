import { useRef, useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

const MOBILE_MAX_LINES = 2000;

interface MobileSyntaxViewProps {
  content: string;
  language: string;
}

export default function MobileSyntaxView({ content, language }: MobileSyntaxViewProps) {
  const [showFull, setShowFull] = useState(false);
  const prevContentRef = useRef(content);

  if (prevContentRef.current !== content) {
    prevContentRef.current = content;
    if (showFull) {
      setShowFull(false);
    }
  }

  const lines = content.split("\n");
  const truncated = !showFull && lines.length > MOBILE_MAX_LINES;
  const displayContent = truncated ? lines.slice(0, MOBILE_MAX_LINES).join("\n") : content;

  return (
    <div className="overflow-auto h-full">
      <SyntaxHighlighter
        language={language}
        style={oneDark}
        customStyle={{
          margin: 0,
          padding: "1rem",
          background: "transparent",
          fontSize: "12px",
          lineHeight: "1.5",
        }}
        showLineNumbers
        lineNumberStyle={{ minWidth: "2.5em", paddingRight: "1em", color: "rgba(255,255,255,0.3)" }}
      >
        {displayContent}
      </SyntaxHighlighter>
      {truncated && (
        <div className="sticky bottom-0 flex justify-center py-3 bg-gradient-to-t from-card via-card to-transparent">
          <button
            onClick={() => setShowFull(true)}
            className="px-4 py-2 rounded-md bg-accent text-sm font-medium text-foreground hover:bg-accent/80 transition-colors"
          >
            Show all {lines.length.toLocaleString()} lines ({Math.round(content.length / 1024)}KB)
          </button>
        </div>
      )}
    </div>
  );
}