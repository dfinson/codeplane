import { useEffect, useState, useCallback } from "react";
import { Trash2, Plus, Save } from "lucide-react";
import { toast } from "sonner";
import {
  fetchSettings, updateSettings,
  fetchRepos, unregisterRepo,
} from "../api/client";
import type { Settings } from "../api/types";
import { AddRepoModal } from "./AddRepoModal";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Textarea } from "./ui/textarea";
import { Spinner } from "./ui/spinner";
import { ConfirmDialog } from "./ui/confirm-dialog";

const PERMISSION_MODES = [
  { value: "auto", label: "Full Auto" },
  { value: "approval_required", label: "Review & Approve" },
  { value: "read_only", label: "Observe Only" },
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

function NumberField({ label, value, onChange, min, max, description, placeholder }: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  description?: string;
  placeholder?: string;
}) {
  const [raw, setRaw] = useState(String(value));

  useEffect(() => {
    setRaw(String(value));
  }, [value]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const str = e.target.value.replace(/[^0-9]/g, "");
    setRaw(str);
    if (str !== "") {
      const num = parseInt(str, 10);
      if (!isNaN(num)) {
        onChange(num);
      }
    }
  };

  const handleBlur = () => {
    if (raw === "" || isNaN(parseInt(raw, 10))) {
      setRaw(String(value));
      return;
    }
    const num = parseInt(raw, 10);
    const clamped = Math.max(min ?? 0, Math.min(max ?? Infinity, num));
    setRaw(String(clamped));
    onChange(clamped);
  };

  return (
    <div className="flex flex-col gap-1.5">
      <Label>{label}</Label>
      <Input
        type="text"
        inputMode="numeric"
        pattern="[0-9]*"
        value={raw}
        onChange={handleChange}
        onBlur={handleBlur}
        className="w-32"
        placeholder={placeholder}
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
  const [removeRepoTarget, setRemoveRepoTarget] = useState<string | null>(null);

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
                  onClick={() => setRemoveRepoTarget(r)}
                  className="sm:opacity-0 sm:group-hover:opacity-100 transition-opacity p-1 rounded text-red-400 hover:text-red-300 hover:bg-red-400/10"
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
            placeholder="5"
            description="Maximum number of agent jobs that can run simultaneously."
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
            placeholder="30"
            description="Artifacts older than this are automatically deleted."
          />
          <NumberField
            label="Max Artifact Size (MB)"
            value={settings.maxArtifactSizeMb}
            onChange={(v) => patch({ maxArtifactSizeMb: v })}
            min={1}
            max={10000}
            placeholder="500"
            description="Maximum size for individual job artifacts."
          />
          <NumberField
            label="Auto-archive (days)"
            value={settings.autoArchiveDays}
            onChange={(v) => patch({ autoArchiveDays: v })}
            min={1}
            max={365}
            placeholder="30"
            description="Jobs older than this are automatically archived."
          />
        </div>
      </div>

      {/* Verification */}
      <div className="rounded-lg border border-border bg-card p-5">
        <p className="text-sm font-semibold mb-4">Verification</p>
        <p className="text-xs text-muted-foreground mb-4">
          Global defaults for post-completion verification. Individual jobs can override these.
        </p>
        <div className="flex flex-col gap-4">
          <div className="flex gap-6">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.verify}
                onChange={(e) => patch({ verify: e.target.checked })}
                className="rounded border-border"
              />
              <span className="text-sm">Verify (run tests/lint)</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.selfReview}
                onChange={(e) => patch({ selfReview: e.target.checked })}
                className="rounded border-border"
              />
              <span className="text-sm">Self-review (review diff)</span>
            </label>
          </div>
          <NumberField
            label="Max Verify Turns"
            value={settings.maxTurns}
            onChange={(v) => patch({ maxTurns: v })}
            min={1}
            max={10}
            placeholder="3"
            description="Maximum number of verify loop iterations."
          />
          <div className="flex flex-col gap-1.5">
            <Label>Verify Prompt</Label>
            <Textarea
              placeholder="You are a helpful coding assistant..."
              value={settings.verifyPrompt}
              onChange={(e) => patch({ verifyPrompt: e.target.value })}
              rows={4}
              autoResize
            />
            <p className="text-xs text-muted-foreground">Instructions prepended to every verification session. Leave empty to use the default.</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Self-Review Prompt</Label>
            <Textarea
              placeholder="Review the diff for potential issues..."
              value={settings.selfReviewPrompt}
              onChange={(e) => patch({ selfReviewPrompt: e.target.value })}
              rows={4}
              autoResize
            />
            <p className="text-xs text-muted-foreground">Instructions prepended to every self-review session. Leave empty to use the default.</p>
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={!!removeRepoTarget}
        onClose={() => setRemoveRepoTarget(null)}
        onConfirm={async () => {
          if (removeRepoTarget) await handleRemoveRepo(removeRepoTarget);
          setRemoveRepoTarget(null);
        }}
        title="Remove Repository?"
        description={removeRepoTarget ? `${removeRepoTarget} will be unregistered from CodePlane. The repository itself won't be deleted.` : ""}
        confirmLabel="Remove"
      />

    </div>
  );
}
