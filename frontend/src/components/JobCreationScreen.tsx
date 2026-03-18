import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown, ChevronRight, PlaneTakeoff, Plus } from "lucide-react";
import { toast } from "sonner";
import { createJob, fetchRepos, fetchModels, fetchSDKs, fetchSettings } from "../api/client";
import type { PermissionMode, SDKInfo } from "../api/types";
import { PromptWithVoice } from "./VoiceButton";
import { AddRepoModal } from "./AddRepoModal";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Switch } from "./ui/switch";
import { Textarea } from "./ui/textarea";
import { Combobox } from "./ui/combobox";

const FALLBACK_VERIFY_PROMPT =
  "Before this task is complete: identify and run this project's test suite, " +
  "linter, and type checker. If anything fails, fix it and re-run until " +
  "everything passes. Assume that any failure is caused by your changes — " +
  "do not dismiss failures as pre-existing or flaky. Also check that you " +
  "haven't made unrelated changes outside the scope of the original task; " +
  "revert any that you find. Report what you ran and the results.";

const FALLBACK_SELF_REVIEW_PROMPT =
  "Review the changes you just made. Look at the full diff. Check for: " +
  "missed edge cases, incomplete implementations, leftover debug code, " +
  "broken imports, dead code, backwards-compatibility shims or fallback " +
  "paths that may no longer be needed, and inconsistencies with the " +
  "surrounding codebase. If you find issues, fix them.";

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
  const [permissionMode, setPermissionMode] = useState<PermissionMode>("approval_required");
  const [sdk, setSdk] = useState<string>("copilot");
  const [sdks, setSdks] = useState<SDKInfo[]>([]);
  const [defaultSdk, setDefaultSdk] = useState<string>("copilot");
  const [verify, setVerify] = useState(false);
  const [selfReview, setSelfReview] = useState(false);
  const [maxTurns, setMaxTurns] = useState<string>("");
  const [verifyPrompt, setVerifyPrompt] = useState("");
  const [selfReviewPrompt, setSelfReviewPrompt] = useState("");
  const [defaultVerifyPrompt, setDefaultVerifyPrompt] = useState(FALLBACK_VERIFY_PROMPT);
  const [defaultSelfReviewPrompt, setDefaultSelfReviewPrompt] = useState(FALLBACK_SELF_REVIEW_PROMPT);

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
    fetchSettings()
      .then((s) => {
        if (s.verifyPrompt) setDefaultVerifyPrompt(s.verifyPrompt);
        if (s.selfReviewPrompt) setDefaultSelfReviewPrompt(s.selfReviewPrompt);
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
        sdk: sdk !== defaultSdk ? sdk : undefined,
        verify: verify ?? undefined,
        self_review: selfReview ?? undefined,
        max_turns: maxTurns ? parseInt(maxTurns, 10) || undefined : undefined,
        verify_prompt: verifyPrompt || undefined,
        self_review_prompt: selfReviewPrompt || undefined,
      });
      toast.success(`Job ${result.id} created`);
      navigate(`/jobs/${result.id}`);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setSubmitting(false);
    }
  }, [repo, prompt, baseRef, branch, model, navigate, permissionMode, sdk, defaultSdk, verify, selfReview, maxTurns, verifyPrompt, selfReviewPrompt, defaultVerifyPrompt, defaultSelfReviewPrompt]);

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

              <hr className="border-border" />
              <p className="text-xs font-medium text-muted-foreground">Verification</p>

              <label className="flex items-center justify-between gap-3 cursor-pointer">
                <span className="text-sm">Verify (run tests/lint after completion)</span>
                <Switch
                  checked={verify}
                  onCheckedChange={(checked) => {
                    setVerify(checked);
                    if (checked && !verifyPrompt) setVerifyPrompt(defaultVerifyPrompt);
                  }}
                />
              </label>

              <label className="flex items-center justify-between gap-3 cursor-pointer">
                <span className="text-sm">Self-review (review diff for issues)</span>
                <Switch
                  checked={selfReview}
                  onCheckedChange={(checked) => {
                    setSelfReview(checked);
                    if (checked && !selfReviewPrompt) setSelfReviewPrompt(defaultSelfReviewPrompt);
                  }}
                />
              </label>

              {(verify || selfReview) && (
                <div className="flex flex-col gap-1.5">
                  <Label>Max Verify Turns</Label>
                  <Input
                    type="number"
                    placeholder="Default (from settings)"
                    value={maxTurns}
                    onChange={(e) => setMaxTurns(e.target.value)}
                    min={1}
                    max={10}
                    className="w-32"
                  />
                </div>
              )}

              {verify && (
                <div className="flex flex-col gap-1.5">
                  <Label>Verify Prompt</Label>
                  <Textarea
                    placeholder={defaultVerifyPrompt}
                    value={verifyPrompt}
                    onChange={(e) => setVerifyPrompt(e.target.value)}
                    rows={3}
                  />
                </div>
              )}

              {selfReview && (
                <div className="flex flex-col gap-1.5">
                  <Label>Self-Review Prompt</Label>
                  <Textarea
                    placeholder={defaultSelfReviewPrompt}
                    value={selfReviewPrompt}
                    onChange={(e) => setSelfReviewPrompt(e.target.value)}
                    rows={3}
                  />
                </div>
              )}
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
