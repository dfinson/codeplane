import { useEffect, useState } from "react";
import { useSSE } from "./hooks/useSSE";
import { useTowerStore, selectConnectionStatus } from "./store";

export function App() {
  const [health, setHealth] = useState<{ status: string; version: string } | null>(null);
  const connectionStatus = useTowerStore(selectConnectionStatus);

  // Mount global SSE connection
  useSSE();

  useEffect(() => {
    fetch("/api/health")
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", padding: "2rem" }}>
      <h1>Tower</h1>
      <p>Control tower for coding agents</p>
      {health ? (
        <p>
          Backend: {health.status} (v{health.version})
        </p>
      ) : (
        <p>Backend: connecting…</p>
      )}
      <p>SSE: {connectionStatus}</p>
    </div>
  );
}
