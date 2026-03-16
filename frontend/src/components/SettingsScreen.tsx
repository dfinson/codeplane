import { useEffect, useState, useCallback } from "react";
import { Trash2, Plus, Wrench, Save } from "lucide-react";
import { toast } from "sonner";
import {
  fetchSettings, updateSettings,
  fetchRepos, unregisterRepo,
  cleanupWorktrees,
} from "../api/client";
import type { Settings } from "../api/types";
import { AddRepoModal } from "./AddRepoModal";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Spinner } from "./ui/spinner";

const PERMISSION_MODES = [
  { value: "permissive", label: "Permissive" },
  { value: "auto", label: "Auto-approve" },
  { value: "supervised", label: "Supervised" },
];

const COMPLETION_STRATEGIES = [
  { value: "auto_merge", label: "Auto Merge" },
  { value: "pr_only", label: "PR Only" },
  { value: "manual", label: "Manual" },
];

function SelectField({ label, value, options, onChange, description }: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
  description?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label>{label}</Label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
    </div>
  );
}

function ToggleField({ label, checked, onChange, description }: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  description?: string;
}) {
  return (
    <label className="flex items-start gap-3 cursor-pointer">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`mt-0.5 relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${checked ? "bg-primary" : "bg-muted"}`}
      >
        <span className={`pointer-events-none block h-4 w-4 rounded-full bg-background shadow-lg ring-0 transition-transform ${checked ? "translate-x-4" : "translate-x-0"}`} />
      </button>
      <div className="flex flex-col">
        <span className="text-sm font-medium leading-none">{label}</span>
        {description && <span className="text-xs text-muted-foreground mt-0.5">{description}</span>}
      </div>
    </label>
  );
}

function NumberField({ label, value, onChange, min, max, description }: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  description?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label>{label}</Label>
      <Input
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        min={min}
        max={max}
        className="w-32"
      />
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
    </div>
  );
}

export function SettingsScreen() {
  const [loading, setLoading] = useState(true);
  const [repos, setRepos] = useState<string[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [saved, setSaved] = useState<Settings | null>(null);
  const [saving, setSaving] = useState(false);
  const [addRepoOpen, setAddRepoOpen] = useState(false);

  useEffect(() => {
    Promise.all([fetchSettings(), fetchRepos()])
      .then(([s, reposRes]) => {
        setSettings(s);
        setSaved(s);
        setRepos(reposRes.items);
      })
      .catch(() => toast.error("Failed to load settings"))
      .finally(() => setLoading(false));
  }, []);

  const dirty = settings !== null && saved !== null && JSON.stringify(settings) !== JSON.stringify(saved);

  const handleSave = useCallback(async () => {
    if (!settings) return;
    setSaving(true);
    try {
      const res = await updateSettings(settings);
      setSettings(res);
      setSaved(res);
      toast.success("Settings saved");
    } catch (e) {
      toast.error(String(e));
    } finally {
      setSaving(false);
    }
  }, [settings]);

  const handleReset = useCallback(() => {
    if (saved) setSettings(saved);
  }, [saved]);

  const patch = useCallback((partial: Partial<Settings>) => {
    setSettings((prev) => prev ? { ...prev, ...partial } : prev);
  }, []);

  const handleRepoAdded = useCallback((path: string) => {
    setRepos((prev) => (prev.includes(path) ? prev : [...prev, path]));
  }, []);

  const handleRemoveRepo = useCallback(async (path: string) => {
    try {
      await unregisterRepo(path);
      setRepos((prev) => prev.filter((r) => r !== path));
      toast.success("Repository removed");
    } catch (e) {
      toast.error(String(e));
    }
  }, []);

  const handleCleanup = useCallback(async () => {
    try {
      const res = await cleanupWorktrees();
      toast.success(`Cleaned up ${res.removed} worktree(s)`);
    } catch (e) {
      toast.error(String(e));
    }
  }, []);

  if (loading || !settings) {
    return (
      <div className="flex justify-center py-20">
        <Spinner size="lg" />
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto flex flex-col gap-5">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">Settings</h3>
        {dirty && (
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={handleReset}>Reset</Button>
            <Button size="sm" onClick={handleSave} loading={saving}>
              <Save size={14} />
              Save
            </Button>
          </div>
        )}
      </div>

      {/* Repositories */}
      <div className="rounded-lg border border-border bg-card p-5">
        <div className="flex items-center justify-between mb-4">
          <span className="text-sm font-semibold">Repositories ({repos.length})</span>
          <Button size="sm" onClick={() => setAddRepoOpen(true)}>
            <Plus size={14} />
            Add Repository
          </Button>
        </div>

        <AddRepoModal
          opened={addRepoOpen}
          onClose={() => setAddRepoOpen(false)}
          onAdded={handleRepoAdded}
        />

        {repos.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-4">No repositories registered</p>
        ) : (
          <div className="flex flex-col gap-1">
            {repos.map((r) => (
              <div
                key={r}
                className="flex items-center justify-between px-3 py-2 rounded-md hover:bg-accent group"
              >
                <span className="text-sm font-mono text-muted-foreground truncate flex-1" title={r}>{r}</span>
                <button
                  type="button"
                  onClick={() => handleRemoveRepo(r)}
                  className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded text-red-400 hover:text-red-300 hover:bg-red-400/10"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Runtime */}
      <div className="rounded-lg border border-border bg-card p-5">
        <p className="text-sm font-semibold mb-4">Runtime</p>
        <div className="grid gap-4 sm:grid-cols-2">
          <NumberField
            label="Max Concurrent Jobs"
            value={settings.maxConcurrentJobs}
            onChange={(v) => patch({ maxConcurrentJobs: v })}
            min={1}
            max={10}
            description="How many agent jobs can run simultaneously"
          />
          <SelectField
            label="Permission Mode"
            value={settings.permissionMode}
            options={PERMISSION_MODES}
            onChange={(v) => patch({ permissionMode: v })}
            description="Default approval policy for new jobs"
          />
        </div>
      </div>

      {/* Completion */}
      <div className="rounded-lg border border-border bg-card p-5">
        <p className="text-sm font-semibold mb-4">Completion</p>
        <div className="flex flex-col gap-4">
          <SelectField
            label="Strategy"
            value={settings.completionStrategy}
            options={COMPLETION_STRATEGIES}
            onChange={(v) => patch({ completionStrategy: v })}
            description="What happens when a job finishes successfully"
          />
          <div className="flex flex-col gap-3">
            <ToggleField
              label="Auto-push"
              checked={settings.autoPush}
              onChange={(v) => patch({ autoPush: v })}
              description="Push branch to remote after completion"
            />
            <ToggleField
              label="Clean up worktree"
              checked={settings.cleanupWorktree}
              onChange={(v) => patch({ cleanupWorktree: v })}
              description="Remove worktree directory after merge"
            />
            <ToggleField
              label="Delete branch after merge"
              checked={settings.deleteBranchAfterMerge}
              onChange={(v) => patch({ deleteBranchAfterMerge: v })}
              description="Delete the feature branch once merged"
            />
          </div>
        </div>
      </div>

      {/* Retention */}
      <div className="rounded-lg border border-border bg-card p-5">
        <p className="text-sm font-semibold mb-4">Retention</p>
        <div className="grid gap-4 sm:grid-cols-2">
          <NumberField
            label="Artifact Retention (days)"
            value={settings.artifactRetentionDays}
            onChange={(v) => patch({ artifactRetentionDays: v })}
            min={1}
            max={365}
          />
          <NumberField
            label="Max Artifact Size (MB)"
            value={settings.maxArtifactSizeMb}
            onChange={(v) => patch({ maxArtifactSizeMb: v })}
            min={1}
            max={10000}
          />
          <NumberField
            label="Auto-archive (days)"
            value={settings.autoArchiveDays}
            onChange={(v) => patch({ autoArchiveDays: v })}
            min={1}
            max={365}
            description="Archive completed jobs after this many days"
          />
        </div>
      </div>

      {/* Maintenance */}
      <div className="rounded-lg border border-border bg-card p-5">
        <p className="text-sm font-semibold mb-3">Maintenance</p>
        <Button variant="outline" size="sm" onClick={handleCleanup}>
          <Wrench size={14} />
          Clean Up Worktrees
        </Button>
      </div>
    </div>
  );
}
