import { useEffect, useRef, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown, ChevronRight, PlaneTakeoff, Plus } from "lucide-react";
import { toast } from "sonner";
import { createJob, fetchRepos, fetchModels, fetchSDKs } from "../api/client";
import type { PermissionMode, SDKInfo } from "../api/types";
import { PromptWithVoice } from "./VoiceButton";
import { AddRepoModal } from "./AddRepoModal";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Switch } from "./ui/switch";
import { Combobox } from "./ui/combobox";

function slugifyPrompt(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9\s-]/g, "")
    .split(/\s+/)
    .slice(0, 6)
    .join("-")
    .replace(/-+/g, "-")
    .slice(0, 50);
}

export function JobCreationScreen() {
  const navigate = useNavigate();
  const [repos, setRepos] = useState<{ value: string; label: string }[]>([]);
  const [repo, setRepo] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [baseRef, setBaseRef] = useState("");
  const [branch, setBranch] = useState("");
  const [branchEdited, setBranchEdited] = useState(false);
  const [model, setModel] = useState<string | null>(null);
  const [models, setModels] = useState<{ value: string; label: string }[]>([]);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [addRepoOpen, setAddRepoOpen] = useState(false);
  const [permissionMode, setPermissionMode] = useState<PermissionMode>("approval_required");
  const [sdk, setSdk] = useState<string>("copilot");
  const [sdks, setSdks] = useState<SDKInfo[]>([]);
  const [defaultSdk, setDefaultSdk] = useState<string>("copilot");
  const [verify, setVerify] = useState(false);
  const [selfReview, setSelfReview] = useState(false);
  const branchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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
    fetchSDKs()
      .then((r) => {
        setSdks(r.sdks);
        setDefaultSdk(r.default);
        setSdk(r.default);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (branchEdited) return;
    if (branchDebounceRef.current) clearTimeout(branchDebounceRef.current);
    branchDebounceRef.current = setTimeout(() => {
      setBranch(prompt.trim() ? slugifyPrompt(prompt) : "");
    }, 1500);
    return () => {
      if (branchDebounceRef.current) clearTimeout(branchDebounceRef.current);
    };
  }, [prompt, branchEdited]);

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
        sdk: sdk !== defaultSdk ? sdk : undefined,
        verify: verify ?? undefined,
        self_review: selfReview ?? undefined,
      });
      toast.success(`Job ${result.id} created`);
      navigate(`/jobs/${result.id}`);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setSubmitting(false);
    }
  }, [repo, prompt, baseRef, branch, model, navigate, permissionMode, sdk, defaultSdk, verify, selfReview]);

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
                  { value: "auto", label: "Full Auto", title: "Approve all operations within the worktree silently" },
                  { value: "approval_required", label: "Review & Approve", title: "Require approval for writes, shell commands, and URL fetches" },
                  { value: "read_only", label: "Observe Only", title: "Deny all writes and mutations" },
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
              {sdks.filter((s) => s.enabled).length > 1 && (
                <Combobox
                  label="Agent SDK"
                  placeholder="Select SDK…"
                  items={sdks
                    .filter((s) => s.enabled)
                    .map((s) => ({ value: s.id, label: s.name }))}
                  value={sdk}
                  onChange={(v) => setSdk(v ?? defaultSdk)}
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
                  onChange={(e) => {
                    setBranch(e.currentTarget.value);
                    setBranchEdited(e.currentTarget.value !== "");
                  }}
                />
              </div>

              <hr className="border-border" />
              <p className="text-xs font-medium text-muted-foreground">Verification</p>

              <label className="flex items-center justify-between gap-3 cursor-pointer">
                <span className="text-sm">Verify (run tests/lint after completion)</span>
                <Switch
                  checked={verify}
                  onCheckedChange={setVerify}
                />
              </label>

              <label className="flex items-center justify-between gap-3 cursor-pointer">
                <span className="text-sm">Self-review (review diff for issues)</span>
                <Switch
                  checked={selfReview}
                  onCheckedChange={setSelfReview}
                />
              </label>
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
              <PlaneTakeoff size={16} />
              Create Job
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
