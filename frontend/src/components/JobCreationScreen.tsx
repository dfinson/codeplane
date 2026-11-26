import { useEffect, useLayoutEffect, useRef, useState, useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ChevronDown, ChevronRight, PlaneTakeoff } from "lucide-react";
import { toast } from "sonner";
import { createJob, fetchProjects, fetchRepoDetail, suggestNames, fetchModelComparison, warmUtilitySession, releaseUtilitySession, fetchPolicySettings } from "../api/client";
import type { ProjectResponse, SDKInfo, RecipeSelection } from "../api/types";
import { useStore } from "../store";
import { PromptWithVoice } from "./VoiceButton";
import { SidecarPicker } from "./SidecarPicker";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Combobox } from "./ui/combobox";
import { Tooltip } from "./ui/tooltip";
import { pathBasename } from "../lib/paths";
import { RecipeLaunchPicker } from "./RecipeLaunchPicker";

interface RepoOption {
  value: string;
  label: string;
  projectId: string;
  projectName: string;
}

function sdkStatusDescription(sdk: SDKInfo): string | undefined {
  if (!sdk.enabled) return sdk.hint || "Not installed";
  if (sdk.status === "not_configured") return sdk.hint || "Not authenticated";
  return undefined;
}

export function JobCreationScreen() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const requestedProjectId = searchParams.get("projectId");
  const requestedRepo = searchParams.get("repo");

  // SDK + model data from the central store
  const sdks = useStore((s) => s.sdks);
  const defaultSdk = useStore((s) => s.defaultSdk);
  const sdksLoading = useStore((s) => s.sdksLoading);
  const modelsBySdk = useStore((s) => s.modelsBySdk);
  const defaultModelBySdk = useStore((s) => s.defaultModelBySdk);
  const modelsLoadingBySdk = useStore((s) => s.modelsLoadingBySdk);
  const loadModelsForSdk = useStore((s) => s.loadModelsForSdk);

  const [repos, setRepos] = useState<RepoOption[]>([]);
  const [repo, setRepo] = useState<string | null>(null);
  const [scopedProject, setScopedProject] = useState<ProjectResponse | null>(null);
  const [repoLocked, setRepoLocked] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [baseRef, setBaseRef] = useState("");
  const [baseRefEdited, setBaseRefEdited] = useState(false);
  const [branch, setBranch] = useState("");
  const [branchEdited, setBranchEdited] = useState(false);
  const [model, setModel] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [preset, setPreset] = useState<"autonomous" | "supervised" | "locked">("supervised");
  const [mode, setMode] = useState<"standard" | "plan">("standard");
  const [recipeSelection, setRecipeSelection] = useState<RecipeSelection | null>(null);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [sdk, setSdk] = useState<string | null>(null);
  const [branchSuggesting, setBranchSuggesting] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [voiceState, setVoiceState] = useState<"idle" | "recording" | "transcribing">("idle");
  const [selectedSidecars, setSelectedSidecars] = useState<string[]>([]);
  const [inlineSidecars, setInlineSidecars] = useState<string[]>([]);
  const branchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const suggestedNamesRef = useRef<{ prompt: string; title: string; description: string; worktreeName: string } | null>(null);
  const sessionTokenRef = useRef<string | null>(null);
  const jobCreatedRef = useRef(false);
  const submittingRef = useRef(false);
  const generation = useRef(0);

  // Pre-warm a sidecar session when the panel mounts; release on unmount if unused
  useEffect(() => {
    let canceled = false;
    warmUtilitySession()
      .then((token) => {
        if (canceled) {
          releaseUtilitySession(token).catch(() => {});
        } else {
          sessionTokenRef.current = token;
        }
      })
      .catch(() => {}); // non-fatal — job creation works without a pre-warmed session
    return () => {
      canceled = true;
      const token = sessionTokenRef.current;
      if (token && !jobCreatedRef.current) {
        releaseUtilitySession(token).catch(() => {});
      }
    };
  }, []);

  // Resolve the active SDK — default to what the store says once it's loaded
  const activeSdk = sdk ?? defaultSdk ?? "copilot";
  const models = modelsBySdk[activeSdk] ?? [];
  // Show loading only while SDKs are fetching OR while this SDK's model list is actively fetching.
  // Avoids "stuck loading" if the SDK fetch fails (defaultSdk stays null) or if models were never
  // requested for this SDK (modelsLoadingBySdk entry is undefined → falsy → not loading).
  const modelsLoading = sdksLoading || modelsLoadingBySdk[activeSdk] === true;

  // Sync the selected model whenever the active SDK's model list becomes available
  useEffect(() => {
    const sdkModels = modelsBySdk[activeSdk];
    if (sdkModels === undefined) return;
    setModel((prev) => {
      if (prev && sdkModels.some((m) => m.value === prev)) return prev;
      return defaultModelBySdk[activeSdk] ?? null;
    });
  }, [activeSdk, modelsBySdk, defaultModelBySdk]);

  useEffect(() => {
    let cancelled = false;
    fetchPolicySettings()
      .then((policy) => {
        if (cancelled) return;
        const p = policy.config.preset;
        if (p === "autonomous" || p === "supervised" || p === "locked") {
          setPreset(p);
        }
        setSettingsLoaded(true);
      })
      .catch(() => {
        if (cancelled) return;
        toast.error("Failed to load policy settings");
        setSettingsLoaded(true); // fall back to hardcoded defaults so the form is usable
      });
    fetchProjects()
      .then((r) => {
        if (cancelled) return;
        const scoped = requestedProjectId
          ? r.items.find((project) => project.id === requestedProjectId) ?? null
          : null;
        const sourceProjects = scoped ? [scoped] : r.items;
        const items = sourceProjects.flatMap((project) =>
          project.repoPaths.map((path) => ({
            value: path,
            label: `${project.name} · ${pathBasename(path) || path}`,
            projectId: project.id,
            projectName: project.name,
          })),
        );
        const singleScopedRepo = scoped?.repoPaths.length === 1 ? scoped.repoPaths[0] : null;
        const requestedRepoValue = requestedRepo && items.some((item) => item.value === requestedRepo)
          ? requestedRepo
          : null;

        setScopedProject(scoped);
        setRepoLocked(Boolean(singleScopedRepo));
        setRepos(items);
        setRepo((prev) => {
          if (singleScopedRepo) return singleScopedRepo;
          if (requestedRepoValue) return requestedRepoValue;
          return prev && items.some((item) => item.value === prev) ? prev : null;
        });
      })
      .catch(() => {
        if (!cancelled) toast.error("Failed to load Projects");
      });
    return () => { cancelled = true; };
  }, [requestedProjectId, requestedRepo]);

  useEffect(() => {
    if (branchEdited) return;
    if (branchDebounceRef.current) clearTimeout(branchDebounceRef.current);
    if (!prompt.trim()) {
      setBranch("");
      suggestedNamesRef.current = null;
      return;
    }
    let cancelled = false;
    branchDebounceRef.current = setTimeout(() => {
      setBranchSuggesting(true);
      suggestNames(prompt)
        .then((names) => {
          if (!cancelled) {
            setBranch(names.branchName);
            suggestedNamesRef.current = { prompt, title: names.title, description: names.description, worktreeName: names.worktreeName };
          }
        })
        .catch(() => {
          // silently ignore — user can type a branch name manually
          if (!cancelled) suggestedNamesRef.current = null;
        })
        .finally(() => { if (!cancelled) setBranchSuggesting(false); });
    }, 800);
    return () => {
      cancelled = true;
      if (branchDebounceRef.current) clearTimeout(branchDebounceRef.current);
    };
  }, [prompt, branchEdited]);

  useEffect(() => {
    if (!repo || baseRefEdited) return;
    let cancelled = false;
    fetchRepoDetail(repo)
      .then((detail) => {
        if (!cancelled) setBaseRef((detail.currentBranch !== "HEAD" ? detail.currentBranch : null) ?? detail.baseBranch ?? "");
      })
      .catch(() => {
        toast.warning("Could not fetch repo details — set Base Reference manually if needed.");
      });
    return () => { cancelled = true; };
  }, [repo, baseRefEdited]);

  const handleSdkChange = useCallback((newSdk: string | null) => {
    const resolved = newSdk ?? defaultSdk ?? activeSdk;
    setSdk(resolved);
    setModel(null);
    loadModelsForSdk(resolved);
  }, [defaultSdk, activeSdk, loadModelsForSdk]);

  // Pre-launch model hint — show avg cost/duration for selected model + repo
  const [modelHint, setModelHint] = useState<string | null>(null);
  useEffect(() => {
    if (!model || !repo) { setModelHint(null); return; }
    let cancelled = false;
    fetchModelComparison(30, repo)
      .then((data) => {
        if (cancelled) return;
        const row = data.models.find((m) => m.model === model);
        if (row && row.jobCount >= 3 && row.avgCost != null && row.avgCost >= 0.01) {
          const cost = row.avgCost < 1 ? `$${row.avgCost.toFixed(3)}` : `$${row.avgCost.toFixed(2)}`;
          const mins = Math.round(row.avgDurationMs / 60_000);
          const time = mins < 1 ? `${Math.round(row.avgDurationMs / 1000)}s` : `${mins}m`;
          setModelHint(`Avg ${cost}/job, ${time} — based on ${row.jobCount} recent jobs in this repo`);
        } else {
          setModelHint(null);
        }
      })
      .catch(() => { if (!cancelled) setModelHint(null); });
    return () => { cancelled = true; };
  }, [model, repo]);

  const selectedProjectId = repos.find((item) => item.value === repo)?.projectId ?? scopedProject?.id ?? null;
  const formIdentity = JSON.stringify([repo, prompt, selectedProjectId, preset, mode, activeSdk, model, recipeSelection]);
  useLayoutEffect(() => {
    generation.current += 1;
    return () => { generation.current += 1; };
  }, [formIdentity]);

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
    if (!repo || !prompt.trim() || !recipeSelection || voiceState !== "idle" || submittingRef.current) return;
    submittingRef.current = true;
    const operationGeneration = generation.current;
    const isCurrent = () => generation.current === operationGeneration;
    setSubmitting(true);
    try {
      // Ensure LLM naming is available before submitting.
      // If suggest-names hasn't returned yet, call it now and wait.
      let cached = suggestedNamesRef.current?.prompt === prompt ? suggestedNamesRef.current : null;
      if (!cached) {
        try {
          const names = await suggestNames(prompt);
          if (!isCurrent()) return;
          cached = { prompt, title: names.title, description: names.description, worktreeName: names.worktreeName };
          suggestedNamesRef.current = cached;
          if (!branchEdited) setBranch(names.branchName);
        } catch {
          toast.error("Failed to generate job name — please try again");
          return;
        }
      }
      if (!isCurrent()) return;
      const result = await createJob({
        repo,
        prompt: prompt.trim(),
        baseRef: baseRef || undefined,
        branch: branch || undefined,
        title: cached.title,
        description: cached.description,
        worktreeName: cached.worktreeName,
        preset: preset,
        model: model || undefined,
        sdk: activeSdk !== defaultSdk ? activeSdk : undefined,
        sessionToken: sessionTokenRef.current ?? undefined,
        projectId: selectedProjectId ?? undefined,
        mode: mode !== "standard" ? mode : undefined,
        recipe: recipeSelection,
      });
      jobCreatedRef.current = true;
      toast.success(`Job ${result.id} created`);
      if (isCurrent()) navigate(`/jobs/${result.id}`);
    } catch (e) {
      if (isCurrent()) toast.error(String(e));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }, [repo, prompt, voiceState, baseRef, branch, branchEdited, model, navigate, preset, mode, activeSdk, defaultSdk, selectedProjectId, recipeSelection]);

  const enabledSdks = sdks.filter((s) => s.enabled);
  const showSdkSelector = enabledSdks.length > 1;
  const currentSdkInfo = sdks.find((s) => s.id === activeSdk);
  const sdkNotReady = currentSdkInfo && currentSdkInfo.status !== "ready";

  return (
    <div className="max-w-3xl mx-auto">
      <h3 className="text-lg font-semibold text-foreground mb-4">New Job</h3>

      <div className="rounded-lg border border-border bg-card p-5">
        <fieldset disabled={submitting} className="flex min-w-0 flex-col gap-4">
          <RecipeLaunchPicker planFirst={mode === "plan"} onChange={setRecipeSelection} />
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:gap-2">
            <Combobox
              label="Repository"
              placeholder="Select a repository…"
              items={repos}
              value={repo}
              onChange={(newRepo) => {
                if (repoLocked) return;
                setRepo(newRepo);
                setBaseRef("");
                setBaseRefEdited(false);
              }}
              className="flex-1"
              disabled={repoLocked}
            />
          </div>
          {scopedProject && (
            <p className="text-xs text-muted-foreground">
              Creating this Job in Project <span className="font-medium text-foreground">{scopedProject.name}</span>.
            </p>
          )}
          {repoLocked ? (
            <p className="text-xs text-muted-foreground">
              This Project has one repository, so CodePlane preselected it.
            </p>
          ) : !repo && repos.length > 0 ? (
            <p className="text-xs text-muted-foreground">
              Choose the repository this Job should modify. CodePlane will not select one automatically.
            </p>
          ) : null}

          <PromptWithVoice
            value={prompt}
            onChange={setPrompt}
            error={errors.prompt}
            onStateChange={setVoiceState}
            onBlur={(e) => validateField("prompt", e.target.value)}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                e.preventDefault();
                handleSubmit();
              }
            }}
          />

          <div className="flex flex-col gap-1.5">
            <Label>Preset</Label>
            <div className={`flex gap-2 transition-opacity ${!settingsLoaded ? "opacity-50 pointer-events-none" : ""}`}>
              {(
                [
                  { value: "autonomous" as const, label: "Autonomous", tip: "Network actions need approval. Everything else runs." },
                  { value: "supervised" as const, label: "Supervised", tip: "Network and irreversible actions need approval. Reversible local-only actions run." },
                  { value: "locked" as const, label: "Locked", tip: "Reversible local-only actions run with a rollback snapshot. Everything else needs approval." },
                ]
              ).map(({ value, label, tip }) => (
                <Tooltip key={value} content={tip}>
                  <button
                    type="button"
                    onClick={() => setPreset(value)}
                    className={`flex-1 rounded-md border px-3 py-1.5 sm:py-1.5 min-h-[44px] sm:min-h-0 text-xs font-medium transition-colors ${
                      preset === value
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border bg-transparent text-muted-foreground hover:text-foreground hover:border-foreground/40"
                    }`}
                  >
                    {label}
                  </button>
                </Tooltip>
              ))}
            </div>
            <p className="text-xs text-muted-foreground leading-snug">
              {preset === "autonomous" && "Network actions need approval. Everything else runs."}
              {preset === "supervised" && "Network and irreversible actions need approval. Reversible local-only actions run."}
              {preset === "locked" && "Reversible local-only actions run with a rollback snapshot. Everything else needs approval."}
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Execution Mode</Label>
            <div className="flex gap-2">
              {(
                [
                  { value: "standard" as const, label: "Standard", tip: "The configured default recipe runs context and implementation steps." },
                  { value: "plan" as const, label: "Plan First", tip: "The configured default recipe requires approval of the exact plan before implementation." },
                ]
              ).map(({ value, label, tip }) => (
                <Tooltip key={value} content={tip}>
                  <button
                    type="button"
                    onClick={() => setMode(value)}
                    className={`flex-1 rounded-md border px-3 py-1.5 sm:py-1.5 min-h-[44px] sm:min-h-0 text-xs font-medium transition-colors ${
                      mode === value
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border bg-transparent text-muted-foreground hover:text-foreground hover:border-foreground/40"
                    }`}
                  >
                    {label}
                  </button>
                </Tooltip>
              ))}
            </div>
            <p className="text-xs text-muted-foreground leading-snug">
              {mode === "standard" && "The configured default recipe runs context and implementation steps. Custom published recipes retain their own graph."}
              {mode === "plan" && "The configured default recipe requires an exact plan approval before implementation. Custom published recipes retain their own gates."}
            </p>
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
              value={activeSdk}
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
          {modelHint && (
            <p className="text-[11px] text-muted-foreground -mt-1">{modelHint}</p>
          )}

          <hr className="border-border" />

          <button
            type="button"
            onClick={() => setShowAdvanced(!showAdvanced)}
            aria-expanded={showAdvanced}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors w-fit"
          >
            {showAdvanced ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            Advanced options
          </button>

          {showAdvanced && (
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <Tooltip content="The branch or commit to base the agent's work on (defaults to the repo's current HEAD)">
                  <Label className="cursor-help w-fit">Base Reference</Label>
                </Tooltip>
                <Input
                  placeholder="e.g., main"
                  value={baseRef}
                  onChange={(e) => {
                    setBaseRef(e.currentTarget.value);
                    setBaseRefEdited(true);
                  }}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Tooltip content="Name for the working branch the agent creates — auto-generated from your prompt if left empty">
                  <Label className="cursor-help w-fit">Branch Name</Label>
                </Tooltip>
                <div className="relative">
                  <Input
                    placeholder={branchSuggesting ? "Generating…" : "Auto-generated if empty"}
                    value={branch}
                    onChange={(e) => {
                      setBranch(e.currentTarget.value);
                      setBranchEdited(true);
                    }}
                  />
                </div>
              </div>

              <hr className="border-border" />

              <SidecarPicker
                selected={selectedSidecars}
                onSelectionChange={setSelectedSidecars}
                onInlineDefinitions={setInlineSidecars}
                inlineDefinitions={inlineSidecars}
              />

            </div>
          )}

          <div className="flex justify-end gap-2 mt-1">
            <Button variant="ghost" onClick={() => navigate("/")}>
              Cancel
            </Button>
            <Button
              disabled={!repo || !prompt.trim() || !recipeSelection || voiceState !== "idle" || !!sdkNotReady}
              loading={submitting}
              onClick={handleSubmit}
            >
              <PlaneTakeoff size={16} />
              {submitting && !suggestedNamesRef.current ? "Naming…" : "Create Job"}
            </Button>
          </div>
        </fieldset>
      </div>
    </div>
  );
}
