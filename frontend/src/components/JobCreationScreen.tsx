import { useEffect, useRef, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown, ChevronRight, PlaneTakeoff, Plus } from "lucide-react";
import { toast } from "sonner";
import { createJob, fetchRepos, fetchModels, fetchSDKs, fetchSettings, fetchRepoDetail, suggestNames } from "../api/client";
import type { PermissionMode, SDKInfo } from "../api/types";
import { PromptWithVoice } from "./VoiceButton";
import { AddRepoModal } from "./AddRepoModal";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Switch } from "./ui/switch";
import { Combobox } from "./ui/combobox";
import { Tooltip } from "./ui/tooltip";

function sdkStatusDescription(sdk: SDKInfo): string | undefined {
  if (!sdk.enabled) return sdk.hint || "Not installed";
  if (sdk.status === "not_configured") return sdk.hint || "Not authenticated";
  return undefined;
}

function pickDefaultModelId(models: Array<{ value: string; isDefault: boolean }>): string | null {
  const flagged = models.find((item) => item.isDefault);
  return flagged?.value ?? models[0]?.value ?? null;
}

export function JobCreationScreen() {
  const navigate = useNavigate();
  const [repos, setRepos] = useState<{ value: string; label: string }[]>([]);
  const [repo, setRepo] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [baseRef, setBaseRef] = useState("");
  const [baseRefEdited, setBaseRefEdited] = useState(false);
  const [branch, setBranch] = useState("");
  const [branchEdited, setBranchEdited] = useState(false);
  const [model, setModel] = useState<string | null>(null);
  const [models, setModels] = useState<{ value: string; label: string }[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [addRepoOpen, setAddRepoOpen] = useState(false);
  const [permissionMode, setPermissionMode] = useState<PermissionMode>("approval_required");
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [sdk, setSdk] = useState<string>("copilot");
  const [sdks, setSdks] = useState<SDKInfo[]>([]);
  const [defaultSdk, setDefaultSdk] = useState<string>("copilot");
  const [verify, setVerify] = useState(false);
  const [selfReview, setSelfReview] = useState(false);
  const [branchSuggesting, setBranchSuggesting] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const branchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadModels = useCallback((sdkId: string) => {
    setModelsLoading(true);
    fetchModels(sdkId)
      .then((m) => {
        const mapped = m
          .map((x) => ({
            value: String(x.id ?? x.name ?? ""),
            label: String(x.name ?? x.id ?? "unknown"),
            isDefault: Boolean(
              (typeof x.default === "boolean" && x.default) ||
              (typeof x.isDefault === "boolean" && x.isDefault) ||
              (typeof x.is_default === "boolean" && x.is_default),
            ),
          }))
          .filter((x) => x.value);
        setModels(mapped.map(({ value, label }) => ({ value, label })));
        setModel((prev) => {
          if (prev && mapped.some((item) => item.value === prev)) return prev;
          return pickDefaultModelId(mapped);
        });
      })
      .catch((err) => {
        console.error("Failed to fetch models", err);
        setModels([]);
        setModel(null);
      })
      .finally(() => setModelsLoading(false));
  }, []);

  useEffect(() => {
    fetchSettings()
      .then((settings) => {
        setPermissionMode(settings.permissionMode as PermissionMode);
        setVerify(settings.verify);
        setSelfReview(settings.selfReview);
        setSettingsLoaded(true);
      })
      .catch(() => {
        toast.error("Failed to load settings");
        setSettingsLoaded(true); // fall back to hardcoded defaults so the form is usable
      });
    fetchRepos()
      .then((r) => {
        const items = r.items.map((p) => ({ value: p, label: p.split("/").pop() ?? p }));
        setRepos(items);
        setRepo((prev) => prev ?? items[0]?.value ?? null);
      })
      .catch(() => toast.error("Failed to load repos"));
    fetchSDKs()
      .then((r) => {
        setSdks(r.sdks);
        setDefaultSdk(r.default);
        setSdk(r.default);
        loadModels(r.default);
      })
      .catch((err) => console.error("Failed to fetch SDKs", err));
  }, [loadModels]);

  useEffect(() => {
    if (branchEdited) return;
    if (branchDebounceRef.current) clearTimeout(branchDebounceRef.current);
    if (!prompt.trim()) {
      setBranch("");
      return;
    }
    branchDebounceRef.current = setTimeout(() => {
      setBranchSuggesting(true);
      suggestNames(prompt)
        .then((names) => {
          if (!branchEdited) setBranch(names.branchName);
        })
        .catch(() => {
          // silently ignore — user can type a branch name manually
        })
        .finally(() => setBranchSuggesting(false));
    }, 1500);
    return () => {
      if (branchDebounceRef.current) clearTimeout(branchDebounceRef.current);
    };
  }, [prompt, branchEdited]);

  useEffect(() => {
    if (!repo || baseRefEdited) return;
    fetchRepoDetail(repo)
      .then((detail) => {
        if (!baseRefEdited) setBaseRef(detail.currentBranch ?? detail.baseBranch ?? "");
      })
      .catch(() => {
        // silently ignore — user can type a base ref manually
      });
  }, [repo, baseRefEdited]);

  const handleSdkChange = useCallback((newSdk: string | null) => {
    const resolved = newSdk ?? defaultSdk;
    setSdk(resolved);
    setModel(null);
    loadModels(resolved);
  }, [defaultSdk, loadModels]);

  const validateField = useCallback((field: string, value: string) => {
    setErrors(prev => {
      const next = { ...prev };
      if (field === "prompt" && !value.trim()) {
        next.prompt = "A prompt is required";
      } else {
        delete next[field];
      }
      return next;
    });
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
      });
      toast.success(`Job ${result.id} created`);
      navigate(`/jobs/${result.id}`);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setSubmitting(false);
    }
  }, [repo, prompt, baseRef, branch, model, navigate, permissionMode, sdk, defaultSdk, verify, selfReview]);

  const enabledSdks = sdks.filter((s) => s.enabled);
  const showSdkSelector = enabledSdks.length > 1;
  const currentSdkInfo = sdks.find((s) => s.id === sdk);
  const sdkNotReady = currentSdkInfo && currentSdkInfo.status !== "ready";

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

          <PromptWithVoice
            value={prompt}
            onChange={setPrompt}
            error={errors.prompt}
            onBlur={(e) => validateField("prompt", e.target.value)}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                e.preventDefault();
                handleSubmit();
              }
            }}
          />

          <div className="flex flex-col gap-1.5">
            <Label>Permission Mode</Label>
            <div className={`flex gap-2 transition-opacity ${!settingsLoaded ? "opacity-50 pointer-events-none" : ""}`}>
              {(
                [
                  { value: "auto", label: "Full Auto", title: "Approve all operations within the worktree silently" },
                  { value: "approval_required", label: "Review & Approve", title: "Require approval for writes, shell commands, and URL fetches" },
                  { value: "read_only", label: "Observe Only", title: "Deny all writes and mutations" },
                ] as { value: PermissionMode; label: string; title: string }[]
              ).map(({ value, label, title }) => (
                <Tooltip key={value} content={title}>
                  <button
                    type="button"
                    onClick={() => setPermissionMode(value)}
                    className={`flex-1 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                      permissionMode === value
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border bg-transparent text-muted-foreground hover:text-foreground hover:border-foreground/40"
                    }`}
                  >
                    {label}
                  </button>
                </Tooltip>
              ))}
            </div>
          </div>

          {showSdkSelector && (
            <Combobox
              label="Agent SDK"
              placeholder="Select SDK…"
              items={enabledSdks.map((s) => ({
                value: s.id,
                label: s.name,
                disabled: s.status !== "ready",
                description: sdkStatusDescription(s),
              }))}
              value={sdk}
              onChange={handleSdkChange}
            />
          )}

          {sdkNotReady && (
            <p className="text-xs text-amber-600 dark:text-amber-400 -mt-1">
              {currentSdkInfo.hint || `${currentSdkInfo.name} is not authenticated.`}
            </p>
          )}

          <Combobox
            label="Model"
            placeholder={modelsLoading ? "Loading…" : models.length === 0 ? "No models available" : "Select model…"}
            items={models}
            value={model}
            onChange={setModel}
          />

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
              <div className="flex flex-col gap-1.5">
                <Label>Base Reference</Label>
                <Input
                  placeholder="e.g., main"
                  value={baseRef}
                  onChange={(e) => {
                    setBaseRef(e.currentTarget.value);
                    setBaseRefEdited(e.currentTarget.value !== "");
                  }}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label>Branch Name</Label>
                <div className="relative">
                  <Input
                    placeholder={branchSuggesting ? "Generating…" : "Auto-generated if empty"}
                    value={branch}
                    onChange={(e) => {
                      setBranch(e.currentTarget.value);
                      setBranchEdited(e.currentTarget.value !== "");
                    }}
                  />
                </div>
              </div>

              <hr className="border-border" />

              <div className="flex flex-col gap-2">
                <Label className="text-xs text-muted-foreground">Post-completion</Label>
                <label className="flex items-center justify-between gap-3 cursor-pointer">
                  <div className="flex flex-col gap-0.5">
                    <span className="text-sm font-medium">Verify</span>
                    <span className="text-xs text-muted-foreground">Run tests & lint</span>
                  </div>
                  <Switch
                    checked={verify}
                    onCheckedChange={setVerify}
                  />
                </label>
                <label className="flex items-center justify-between gap-3 cursor-pointer">
                  <div className="flex flex-col gap-0.5">
                    <span className="text-sm font-medium">Self-review</span>
                    <span className="text-xs text-muted-foreground">Review diff for issues</span>
                  </div>
                  <Switch
                    checked={selfReview}
                    onCheckedChange={setSelfReview}
                  />
                </label>
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
              <PlaneTakeoff size={16} />
              Create Job
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
