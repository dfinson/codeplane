import { useRef, useEffect } from "react";
import { useTowerStore, selectJobTranscript } from "../store";

export function TranscriptPanel({ jobId }: { jobId: string }) {
  const transcript = useTowerStore(selectJobTranscript(jobId));
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new entries
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript.length]);

  return (
    <div className="panel">
      <div className="panel__header">
        <span>Transcript</span>
        <span style={{ fontSize: 11 }}>{transcript.length} messages</span>
      </div>
      <div className="panel__body">
        {transcript.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state__text">No transcript entries yet</div>
          </div>
        ) : (
          transcript.map((entry) => (
            <div key={`${entry.jobId}-${entry.seq}`} className="transcript-entry">
              <div
                className={`transcript-entry__role transcript-entry__role--${entry.role}`}
              >
                {entry.role}
              </div>
              <div className="transcript-entry__content">{entry.content}</div>
              <div className="transcript-entry__time">
                {new Date(entry.timestamp).toLocaleTimeString()}
              </div>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
