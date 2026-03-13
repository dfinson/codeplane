import { useCallback, useEffect, useState } from "react";
import {
  fetchGlobalConfig,
  updateGlobalConfig,
  fetchRepos,
  registerRepo,
  unregisterRepo,
  cleanupWorktrees,
} from "../api/client";

export function SettingsScreen() {
  const [configYaml, setConfigYaml] = useState("");
  const [savedYaml, setSavedYaml] = useState("");
  const [repos, setRepos] = useState<string[]>([]);
  const [newRepo, setNewRepo] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    try {
      const [configRes, reposRes] = await Promise.all([
        fetchGlobalConfig(),
        fetchRepos(),
      ]);
      setConfigYaml(configRes.config_yaml);
      setSavedYaml(configRes.config_yaml);
      setRepos(reposRes.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load settings");
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const handleSaveConfig = async () => {
    setError(null);
    setStatus(null);
    try {
      const res = await updateGlobalConfig(configYaml);
      setSavedYaml(res.config_yaml);
      setStatus("Configuration saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save config");
    }
  };

  const handleAddRepo = async () => {
    if (!newRepo.trim()) return;
    setError(null);
    setStatus(null);
    try {
      await registerRepo(newRepo.trim());
      setNewRepo("");
      const reposRes = await fetchRepos();
      setRepos(reposRes.items);
      setStatus("Repository added.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add repo");
    }
  };

  const handleRemoveRepo = async (repoPath: string) => {
    setError(null);
    setStatus(null);
    try {
      await unregisterRepo(repoPath);
      const reposRes = await fetchRepos();
      setRepos(reposRes.items);
      setStatus("Repository removed.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove repo");
    }
  };

  const handleCleanup = async () => {
    setError(null);
    setStatus(null);
    try {
      const res = await cleanupWorktrees();
      setStatus(`Cleanup complete — ${res.removed} worktree(s) removed.`);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Cleanup failed",
      );
    }
  };

  const configDirty = configYaml !== savedYaml;

  return (
    <div className="settings-screen">
      <h1>Settings</h1>

      {error && <div className="settings-error">{error}</div>}
      {status && <div className="settings-status">{status}</div>}

      <section className="settings-section">
        <h2>Global Configuration</h2>
        <textarea
          className="settings-config-editor"
          value={configYaml}
          onChange={(e) => setConfigYaml(e.target.value)}
          spellCheck={false}
          rows={20}
        />
        <button
          onClick={() => void handleSaveConfig()}
          disabled={!configDirty}
        >
          Save Config
        </button>
      </section>

      <section className="settings-section">
        <h2>Repositories</h2>
        <div className="settings-repo-add">
          <input
            type="text"
            value={newRepo}
            onChange={(e) => setNewRepo(e.target.value)}
            placeholder="Local path or remote URL"
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleAddRepo();
            }}
          />
          <button onClick={() => void handleAddRepo()}>Add</button>
        </div>
        <ul className="settings-repo-list">
          {repos.map((repo) => (
            <li key={repo}>
              <span>{repo}</span>
              <button onClick={() => void handleRemoveRepo(repo)}>
                Remove
              </button>
            </li>
          ))}
          {repos.length === 0 && (
            <li className="settings-repo-empty">
              No repositories registered.
            </li>
          )}
        </ul>
      </section>

      <section className="settings-section">
        <h2>Maintenance</h2>
        <button onClick={() => void handleCleanup()}>
          Clean Up Worktrees
        </button>
      </section>
    </div>
  );
}
