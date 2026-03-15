import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown, ChevronRight, Rocket, Plus } from "lucide-react";
import { toast } from "sonner";
import { createJob, fetchRepos, fetchModels } from "../api/client";
import type { PermissionMode, CompletionStrategy } from "../api/types";
import { PromptWithVoice } from "./VoiceButton";
import { AddRepoModal } from "./AddRepoModal";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Combobox } from "./ui/combobox";

export function JobCreationScreen() {
  const navigate = useNavigate();
  const [repos, setRepos] = useState<{ value: string; label: string }[]>([]);
  const [repo, setRepo] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [baseRef, setBaseRef] = useState("");
  const [branch, setBranch] = useState("");
  const [model, setModel] = useState<string | null>(null);
  const [models, setModels] = useState<{ value: string; label: string }[]>([]);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [addRepoOpen, setAddRepoOpen] = useState(false);
  const [permissionMode, setPermissionMode] = useState<PermissionMode>("supervised");
  const [completionStrategy, setCompletionStrategy] = useState<CompletionStrategy>("auto_merge");

  useEffect(() => {
    fetchRepos()
      .then((r) => {
        const items = r.items.map((p) => ({ value: p, label: p.split("/").pop() ?? p }));
        setRepos(items);
        setRepo((prev) => prev ?? items[0]?.value ?? null);
      })
      .catch(() => toast.error("Failed to load repos"));
    fetchModels()
      .then((m) => {
        setModels(
          m
            .map((x) => ({
              value: String(x.id ?? x.name ?? ""),
              label: String(x.name ?? x.id ?? "unknown"),
            }))
            .filter((x) => x.value),
        );
      })
      .catch(() => {});
  }, []);

  const handleSubmit = useCallback(async () => {
    if (!repo || !prompt.trim()) return;
    setSubmitting(true);
    try {
      const result = await createJob({
        repo,
        prompt: prompt.trim(),
        base_ref: baseRef || undefined,
        branch: branch || undefined,
        permission_mode: permissionMode,
        model: model || undefined,
        completion_strategy: completionStrategy,
      });
      toast.success(`Job ${result.id} created`);
      navigate(`/jobs/${result.id}`);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setSubmitting(false);
    }
  }, [repo, prompt, baseRef, branch, model, navigate, permissionMode, completionStrategy]);

  return (
    <div className="max-w-xl mx-auto">
      <h3 className="text-lg font-semibold text-foreground mb-4">New Job</h3>

      <div className="rounded-lg border border-border bg-card p-5">
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:gap-2">
            <Combobox
              label="Repository"
              placeholder="Select a repository…"
              items={repos}
              value={repo}
              onChange={setRepo}
              className="flex-1"
            />
            <Button
              size="sm"
              variant="outline"
              onClick={() => setAddRepoOpen(true)}
              className="mb-px shrink-0"
            >
              <Plus size={14} />
              Add
            </Button>
          </div>

          <AddRepoModal
            opened={addRepoOpen}
            onClose={() => setAddRepoOpen(false)}
            onAdded={(path) => {
              const label = path.split("/").pop() ?? path;
              setRepos((prev) => {
                if (prev.some((r) => r.value === path)) return prev;
                return [...prev, { value: path, label }];
              });
              setRepo(path);
            }}
          />

          <PromptWithVoice value={prompt} onChange={setPrompt} />

          <div className="flex flex-col gap-1.5">
            <Label>Permission Mode</Label>
            <div className="flex gap-2">
              {(
                [
                  { value: "auto", label: "Auto", title: "Approve all operations silently" },
                  { value: "supervised", label: "Supervised", title: "Block on writes and URL fetches only — shell and reads auto-approved" },
                  { value: "readonly", label: "Read-only", title: "Deny writes and shell commands" },
                ] as { value: PermissionMode; label: string; title: string }[]
              ).map(({ value, label, title }) => (
                <button
                  key={value}
                  type="button"
                  title={title}
                  onClick={() => setPermissionMode(value)}
                  className={`flex-1 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                    permissionMode === value
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-transparent text-muted-foreground hover:text-foreground hover:border-foreground/40"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>On Completion</Label>
            <div className="flex gap-2">
              {(
                [
                  { value: "auto_merge", label: "Auto Merge", title: "Merge changes back to the base branch automatically" },
                  { value: "pr_only", label: "PR Only", title: "Create a pull request instead of merging directly" },
                  { value: "manual", label: "Manual", title: "Leave changes in the worktree for manual resolution" },
                ] as { value: CompletionStrategy; label: string; title: string }[]
              ).map(({ value, label, title }) => (
                <button
                  key={value}
                  type="button"
                  title={title}
                  onClick={() => setCompletionStrategy(value)}
                  className={`flex-1 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                    completionStrategy === value
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-transparent text-muted-foreground hover:text-foreground hover:border-foreground/40"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <hr className="border-border" />

          <button
            type="button"
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors w-fit"
          >
            {showAdvanced ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            Advanced options
          </button>

          {showAdvanced && (
            <div className="flex flex-col gap-3">
              {models.length > 0 && (
                <Combobox
                  label="Model"
                  placeholder="Default (auto)"
                  items={models}
                  value={model}
                  onChange={setModel}
                  clearable
                />
              )}
              <div className="flex flex-col gap-1.5">
                <Label>Base Reference</Label>
                <Input
                  placeholder="e.g., main"
                  value={baseRef}
                  onChange={(e) => setBaseRef(e.currentTarget.value)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label>Branch Name</Label>
                <Input
                  placeholder="Auto-generated if empty"
                  value={branch}
                  onChange={(e) => setBranch(e.currentTarget.value)}
                />
              </div>
            </div>
          )}

          <div className="flex justify-end gap-2 mt-1">
            <Button variant="ghost" onClick={() => navigate("/")}>
              Cancel
            </Button>
            <Button
              disabled={!repo || !prompt.trim()}
              loading={submitting}
              onClick={handleSubmit}
            >
              <Rocket size={16} />
              Create Job
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
