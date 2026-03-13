import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { createJob, fetchRepos } from "../api/client";
import { VoiceButton } from "./VoiceButton";

export function JobCreationScreen() {
  const navigate = useNavigate();
  const [repos, setRepos] = useState<string[]>([]);
  const [repo, setRepo] = useState("");
  const [prompt, setPrompt] = useState("");
  const [baseRef, setBaseRef] = useState("");
  const [branch, setBranch] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchRepos()
      .then((result) => {
        setRepos(result.items);
        if (result.items.length > 0) {
          setRepo((prev) => prev || (result.items[0] ?? ""));
        }
      })
      .catch(() => {
        // Cannot load repos — user can still type manually
      });
  }, []);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!repo || !prompt.trim()) return;
      setSubmitting(true);
      setError(null);
      try {
        const result = await createJob({
          repo,
          prompt: prompt.trim(),
          base_ref: baseRef || undefined,
          branch: branch || undefined,
        });
        navigate(`/jobs/${result.id}`);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to create job");
      } finally {
        setSubmitting(false);
      }
    },
    [repo, prompt, baseRef, branch, navigate],
  );

  return (
    <div className="create-job">
      <h2 className="create-job__title">New Job</h2>
      <form className="create-job__form" onSubmit={handleSubmit}>
        {error && (
          <div
            style={{
              background: "#3d0d0d",
              border: "1px solid var(--color-error)",
              borderRadius: "var(--radius-md)",
              padding: "8px 12px",
              marginBottom: 16,
              fontSize: 13,
              color: "var(--color-error)",
            }}
          >
            {error}
          </div>
        )}

        <div className="form-field">
          <label className="form-label" htmlFor="repo">
            Repository
          </label>
          <select
            id="repo"
            className="form-input"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            required
          >
            <option value="" disabled>
              Select a repository…
            </option>
            {repos.map((r) => (
              <option key={r} value={r}>
                {r.split("/").pop() ?? r}
              </option>
            ))}
          </select>
        </div>

        <div className="form-field">
          <label className="form-label" htmlFor="prompt">
            Prompt
          </label>
          <textarea
            id="prompt"
            className="form-textarea"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Describe the task for the coding agent…"
            required
          />
          <VoiceButton
            onTranscript={(text) => setPrompt((prev) => (prev ? `${prev} ${text}` : text))}
            disabled={submitting}
          />
        </div>

        <div className="create-job__advanced">
          <button
            type="button"
            className="create-job__advanced-toggle"
            onClick={() => setShowAdvanced(!showAdvanced)}
          >
            {showAdvanced ? "▾" : "▸"} Advanced Options
          </button>

          {showAdvanced && (
            <>
              <div className="form-field">
                <label className="form-label" htmlFor="baseRef">
                  Base Reference
                </label>
                <input
                  id="baseRef"
                  type="text"
                  className="form-input"
                  value={baseRef}
                  onChange={(e) => setBaseRef(e.target.value)}
                  placeholder="e.g., main (defaults to repo's base_branch)"
                />
              </div>

              <div className="form-field">
                <label className="form-label" htmlFor="branch">
                  Branch Name
                </label>
                <input
                  id="branch"
                  type="text"
                  className="form-input"
                  value={branch}
                  onChange={(e) => setBranch(e.target.value)}
                  placeholder="Auto-generated if empty"
                />
              </div>
            </>
          )}
        </div>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 16 }}>
          <button
            type="button"
            className="btn"
            onClick={() => navigate("/")}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="btn btn--primary"
            disabled={submitting || !repo || !prompt.trim()}
          >
            {submitting ? "Creating…" : "Create Job"}
          </button>
        </div>
      </form>
    </div>
  );
}
