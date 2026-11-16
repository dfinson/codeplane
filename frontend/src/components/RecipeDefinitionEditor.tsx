import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ChevronRight, Copy, Plus, Trash2 } from "lucide-react";
import {
  copyRecipeRevision,
  createRecipeDraft,
  fetchRecipe,
  fetchRecipeRevisions,
  fetchRecipes,
  publishRecipe,
  saveRecipeDraft,
  validateRecipe,
} from "../api/client";
import type {
  RecipeCapability,
  RecipeCommandArgument,
  RecipeDefinition,
  RecipeDraft,
  RecipeDraftList,
  RecipeInput,
  RecipeOutput,
  RecipeParameter,
  RecipeRevision,
  RecipeRevisionList,
  RecipeRoute,
  RecipeRunner,
  RecipeStep,
  RecipeValidationResult,
  RecipeValueType,
} from "../api/types";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

const inputStyle =
  "w-full min-w-0 rounded-md border border-input bg-background px-3 py-2 text-sm";
const valueTypes: readonly RecipeValueType[] = [
  "string",
  "integer",
  "number",
  "boolean",
  "object",
  "array",
  "code_ref",
];
const capabilities: readonly RecipeCapability[] = [
  "read",
  "write",
  "shell",
  "delegate",
  "external_access",
];

function route(terminal: "succeeded" | "failed" = "succeeded"): RecipeRoute {
  return { terminal, nextSteps: [] };
}

function newRunner(kind: RecipeRunner["kind"]): RecipeRunner {
  switch (kind) {
    case "agent":
      return { kind, model: null, provider: null, session: "new", maxTurns: 1 };
    case "command":
      return { kind, argv: [], timeoutSeconds: 300, reconciles: [] };
    case "human":
      return { kind, proposedAction: "", decisions: ["approved", "rejected"] };
    case "block":
      return { kind, revisionId: "" };
  }
}

function newStep(id: string, title: string): RecipeStep {
  return {
    id,
    title,
    instructions: "",
    runner: newRunner("agent"),
    capabilities: ["read"],
    workspace: { mode: "snapshot", key: "job", sourceInput: null },
    inputs: {},
    outputs: {},
    dependsOn: [],
    routes: { succeeded: route(), failed: route("failed") },
    retry: { maxAttempts: 1, retryOn: [], exhaustion: route("failed") },
  };
}

function starterDefinition(): RecipeDefinition {
  const context = newStep("context", "Context brief");
  context.instructions =
    "Read the relevant code and instructions. Return a concise, source-backed brief. Do not edit files or run commands.";
  context.inputs = {
    request: {
      type: "string",
      optional: false,
      binding: { source: "parameter", parameter: "request" },
    },
  };
  context.outputs = {
    brief: {
      type: "string",
      description: "Required source-backed context text.",
    },
  };
  context.routes = {
    succeeded: { nextSteps: ["implement"] },
    failed: { nextSteps: ["implement"] },
  };
  const implement = newStep("implement", "Implement");
  implement.instructions =
    "Implement the Job request using the received context. Retain the summary and exact resulting code reference.";
  implement.capabilities = ["read", "write", "shell", "external_access"];
  implement.workspace = { mode: "mutable", key: "job", sourceInput: null };
  implement.inputs = {
    request: {
      type: "string",
      optional: false,
      binding: { source: "parameter", parameter: "request" },
    },
    brief: {
      type: "string",
      optional: true,
      binding: { source: "output", stepId: "context", output: "brief" },
      degradedReason:
        "If context failed, continue from the Job request while retaining the missing or degraded brief receipt.",
    },
  };
  implement.outputs = {
    summary: { type: "string", description: "What was implemented." },
    code: {
      type: "code_ref",
      description: "The exact resulting workspace and commit.",
    },
  };
  implement.dependsOn = [
    { stepId: "context", outcomes: ["succeeded", "failed"] },
  ];
  implement.routes = {
    succeeded: { nextSteps: ["verify"] },
    failed: route("failed"),
  };
  const verify = newStep("verify", "Verify");
  verify.runner = {
    kind: "command",
    argv: ["git", "show", "--check", { input: "code", field: "commit" }],
    timeoutSeconds: 300,
    reconciles: [],
  };
  verify.instructions =
    "Run the declared check against the Implement output. Retain the command, working directory, code reference, exit status and output.";
  verify.capabilities = ["read", "write", "shell", "external_access"];
  verify.workspace = { mode: "mutable", key: "job", sourceInput: "code" };
  verify.inputs = {
    code: {
      type: "code_ref",
      optional: false,
      binding: { source: "output", stepId: "implement", output: "code" },
    },
  };
  verify.outputs = {
    result: {
      type: "object",
      description:
        "Captured command identity, exit status, stdout, stderr and code reference.",
    },
  };
  verify.dependsOn = [{ stepId: "implement", outcomes: ["succeeded"] }];
  verify.routes = {
    succeeded: { nextSteps: ["review"] },
    failed: { nextSteps: ["review"] },
  };
  const review = newStep("review", "Human review");
  review.runner = {
    kind: "human",
    proposedAction:
      "Review this exact code and check evidence before continuing to delivery review. This does not merge or deliver anything.",
    decisions: ["approved", "rejected"],
  };
  review.capabilities = [];
  review.workspace = { mode: "none", key: "job", sourceInput: null };
  review.inputs = {
    result: {
      type: "object",
      optional: true,
      binding: { source: "output", stepId: "verify", output: "result" },
      degradedReason:
        "A known nonzero check result is diagnostic evidence, not a passing check. Missing or uncertain command capture blocks this route.",
    },
  };
  review.dependsOn = [{ stepId: "verify", outcomes: ["succeeded", "failed"] }];
  review.routes = { approved: route(), rejected: route("failed") };
  return {
    name: "Implementation with command check",
    description:
      "An explicit command-check definition. Compatibility agent verification remains a separate choice.",
    kind: "recipe",
    parameters: {
      request: {
        type: "string",
        description: "The Job request.",
        required: true,
      },
    },
    entrySteps: ["context"],
    steps: [context, implement, verify, review],
    outputs: {},
  };
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      role="group"
      aria-label={label}
      className="grid gap-2 border-b border-border py-[11px] last:border-0 sm:grid-cols-[155px_minmax(0,1fr)] sm:gap-[18px]"
    >
      <span className="text-[13px] text-muted-foreground">{label}</span>
      <div className="min-w-0 space-y-2">{children}</div>
    </div>
  );
}

function ValueTypeSelect({
  value,
  onChange,
  label,
}: {
  value: RecipeValueType;
  onChange: (value: RecipeValueType) => void;
  label: string;
}) {
  return (
    <select
      aria-label={label}
      value={value}
      className={inputStyle}
      onChange={(event) => {
        const selected = valueTypes.find((item) => item === event.target.value);
        if (selected) onChange(selected);
      }}
    >
      {valueTypes.map((item) => (
        <option key={item}>{item}</option>
      ))}
    </select>
  );
}

function commandArguments(value: unknown): (string | RecipeCommandArgument)[] {
  if (!Array.isArray(value))
    throw new Error("Command arguments must be a JSON array.");
  return value.map((item: unknown) => {
    if (typeof item === "string") return item;
    if (
      !item ||
      typeof item !== "object" ||
      Array.isArray(item) ||
      !("input" in item) ||
      typeof item.input !== "string" ||
      Object.keys(item).some((key) => key !== "input" && key !== "field")
    )
      throw new Error(
        'Each argument must be a literal string or an {"input": "...", "field": "..."} reference.',
      );
    if (!("field" in item)) return { input: item.input };
    if (
      item.field !== null &&
      item.field !== "commit" &&
      item.field !== "workspace"
    ) {
      throw new Error("A code reference field must be commit or workspace.");
    }
    return { input: item.input, field: item.field };
  });
}

function finiteJson(value: unknown): boolean {
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(finiteJson);
  if (value && typeof value === "object")
    return Object.values(value).every(finiteJson);
  return true;
}

export function RecipeDefinitionEditor() {
  const { definitionId } = useParams<{ definitionId?: string }>();
  const navigate = useNavigate();
  const [library, setLibrary] = useState<RecipeDraftList | null>(null);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [draft, setDraft] = useState<RecipeDraft | null>(null);
  const [definition, setDefinition] = useState<RecipeDefinition | null>(null);
  const [revisions, setRevisions] = useState<RecipeRevisionList | null>(null);
  const [publishedId, setPublishedId] = useState("");
  const [publication, setPublication] = useState<RecipeRevision | null>(null);
  const [publicationReadError, setPublicationReadError] = useState<
    string | null
  >(null);
  const [copyName, setCopyName] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [validation, setValidation] = useState<RecipeValidationResult | null>(
    null,
  );
  const [jsonBuffers, setJsonBuffers] = useState<Record<string, string>>({});
  const [jsonErrors, setJsonErrors] = useState<Record<string, string>>({});
  const [newInput, setNewInput] = useState("");
  const [newOutput, setNewOutput] = useState("");
  const [newParameter, setNewParameter] = useState("");
  const [newExport, setNewExport] = useState("");
  const [blockRevisions, setBlockRevisions] =
    useState<RecipeRevisionList | null>(null);
  const [blockDefinition, setBlockDefinition] = useState("");
  const owner = useRef(0);
  const selectedRef = useRef("");
  const blockRequest = useRef(0);
  const libraryRequest = useRef(0);
  const revisionRequest = useRef(0);
  const publicationRef = useRef<RecipeRevision | null>(null);

  const selectStep = useCallback((id: string) => {
    selectedRef.current = id;
    setSelectedId(id);
    setNewInput("");
    setNewOutput("");
    setBlockRevisions(null);
    setBlockDefinition("");
    blockRequest.current += 1;
  }, []);

  const adoptDraft = useCallback(
    (value: RecipeDraft | null) => {
      const next = value?.definition ?? starterDefinition();
      setDraft(value);
      setDefinition(next);
      setDirty(false);
      setValidation(null);
      setJsonBuffers({});
      setJsonErrors({});
      setCopyName(`${next.name} copy`);
      selectStep(
        next.steps?.some((step) => step.id === selectedRef.current)
          ? selectedRef.current
          : (next.steps?.[0]?.id ?? ""),
      );
    },
    [selectStep],
  );

  const loadLibrary = useCallback(async (after = 0, highWater?: number) => {
    const request = ++libraryRequest.current;
    try {
      const result = await fetchRecipes(after, highWater);
      if (libraryRequest.current !== request) return;
      setLibrary((previous) => ({
        ...result,
        items: after
          ? [...(previous?.items ?? []), ...result.items]
          : result.items,
      }));
      setLibraryError(null);
    } catch (failure) {
      if (libraryRequest.current === request)
        setLibraryError(
          failure instanceof Error ? failure.message : String(failure),
        );
    }
  }, []);

  useEffect(() => {
    void loadLibrary();
    return () => {
      libraryRequest.current += 1;
    };
  }, [loadLibrary]);

  useEffect(() => {
    const request = ++owner.current;
    const retained =
      publicationRef.current?.definitionId === definitionId
        ? publicationRef.current
        : null;
    setError(null);
    if (!retained) {
      publicationRef.current = null;
      setPublication(null);
    }
    setPublishedId(retained?.id ?? "");
    setPublicationReadError(null);
    setRevisions(null);
    revisionRequest.current += 1;
    if (!definitionId) {
      adoptDraft(null);
      setBusy(false);
      return () => {
        owner.current += 1;
      };
    }
    setDefinition(retained?.definition ?? null);
    setBusy(true);
    Promise.all([fetchRecipe(definitionId), fetchRecipeRevisions(definitionId)])
      .then(([loaded, history]) => {
        if (owner.current !== request) return;
        if (
          loaded.id !== definitionId ||
          history.items.some((item) => item.definitionId !== definitionId)
        ) {
          throw new Error(
            "Recipe response belongs to another definition. Reload this definition.",
          );
        }
        adoptDraft(loaded);
        setRevisions(history);
      })
      .catch((failure) => {
        if (owner.current === request) {
          const message =
            failure instanceof Error ? failure.message : String(failure);
          if (retained) setPublicationReadError(message);
          else setError(message);
        }
      })
      .finally(() => {
        if (owner.current === request) setBusy(false);
      });
    return () => {
      owner.current += 1;
    };
  }, [definitionId, adoptDraft]);

  async function refreshPublication(receipt: RecipeRevision): Promise<boolean> {
    const generation = owner.current;
    const request = ++revisionRequest.current;
    const isCurrent = () =>
      owner.current === generation &&
      revisionRequest.current === request &&
      publicationRef.current?.id === receipt.id;
    setBusy(true);
    setPublicationReadError(null);
    try {
      const [current, history] = await Promise.all([
        fetchRecipe(receipt.definitionId),
        fetchRecipeRevisions(receipt.definitionId),
      ]);
      if (!isCurrent()) return false;
      if (
        current.id !== receipt.definitionId ||
        history.items.some((item) => item.definitionId !== receipt.definitionId)
      ) {
        throw new Error("Draft refresh returned another definition.");
      }
      adoptDraft(current);
      setRevisions(history);
      setPublishedId(receipt.id);
      if (!definitionId)
        navigate(`/recipes/${encodeURIComponent(receipt.definitionId)}`, {
          replace: true,
        });
      return true;
    } catch (failure) {
      if (isCurrent())
        setPublicationReadError(
          failure instanceof Error ? failure.message : String(failure),
        );
      return false;
    } finally {
      if (isCurrent()) setBusy(false);
    }
  }

  function change(next: RecipeDefinition) {
    setDefinition(next);
    setDirty(true);
    setValidation(null);
    setError(null);
  }

  function updateStep(next: RecipeStep) {
    if (definition)
      change({
        ...definition,
        steps: (definition.steps ?? []).map((item) =>
          item.id === next.id ? next : item,
        ),
      });
  }

  function forgetJson(prefix: string) {
    const matches = (key: string) =>
      key === prefix ||
      key.startsWith(prefix.endsWith(".") ? prefix : `${prefix}.`);
    setJsonBuffers((values) =>
      Object.fromEntries(
        Object.entries(values).filter(([key]) => !matches(key)),
      ),
    );
    setJsonErrors((values) =>
      Object.fromEntries(
        Object.entries(values).filter(([key]) => !matches(key)),
      ),
    );
  }

  function jsonField(
    key: string,
    value: unknown,
    onChange: (value: unknown) => void,
    label: string,
  ) {
    return (
      <div>
        <textarea
          aria-label={label}
          rows={3}
          className={`${inputStyle} font-mono text-xs`}
          value={jsonBuffers[key] ?? JSON.stringify(value ?? null, null, 2)}
          onChange={(event) => {
            const text = event.target.value;
            setDirty(true);
            setJsonBuffers((values) => ({ ...values, [key]: text }));
            try {
              const parsed: unknown = JSON.parse(text);
              if (!finiteJson(parsed))
                throw new Error("JSON numbers must be finite.");
              setJsonErrors((values) => {
                const next = { ...values };
                delete next[key];
                return next;
              });
              onChange(parsed);
            } catch (failure) {
              setJsonErrors((values) => ({
                ...values,
                [key]:
                  failure instanceof Error ? failure.message : String(failure),
              }));
            }
          }}
        />
        {jsonErrors[key] && (
          <p role="alert" className="text-xs text-red-500">
            {jsonErrors[key]}
          </p>
        )}
      </div>
    );
  }

  async function persist(publish: boolean) {
    if (
      !definition ||
      busy ||
      publicationReadError ||
      Object.keys(jsonErrors).length
    )
      return;
    const request = owner.current;
    revisionRequest.current += 1;
    setBusy(true);
    setError(null);
    try {
      if (publish) {
        const result = await validateRecipe(definition);
        if (owner.current !== request) return;
        setValidation(result);
        if (!result.valid) return;
      }
      const saved = draft
        ? await saveRecipeDraft(draft.id, draft.version, definition)
        : await createRecipeDraft(definition);
      if (owner.current !== request) return;
      setDraft(saved);
      setDefinition(saved.definition);
      setDirty(false);
      if (publish) {
        const revision = await publishRecipe(saved.id, saved.version);
        if (owner.current !== request) return;
        if (revision.definitionId !== saved.id)
          throw new Error(
            "Publication response belongs to another definition.",
          );
        publicationRef.current = revision;
        setPublication(revision);
        setPublishedId(revision.id);
        void loadLibrary();
        await refreshPublication(revision);
        return;
      }
      void loadLibrary();
      if (!definitionId)
        navigate(`/recipes/${encodeURIComponent(saved.id)}`, { replace: true });
    } catch (failure) {
      if (owner.current === request)
        setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      if (owner.current === request) setBusy(false);
    }
  }

  async function checkDefinition() {
    if (!definition || busy || Object.keys(jsonErrors).length) return;
    const request = owner.current;
    setBusy(true);
    try {
      const result = await validateRecipe(definition);
      if (owner.current === request) setValidation(result);
    } catch (failure) {
      if (owner.current === request)
        setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      if (owner.current === request) setBusy(false);
    }
  }

  async function copyPublished() {
    if (!publishedId || !copyName.trim() || busy) return;
    const request = owner.current;
    setBusy(true);
    try {
      const copied = await copyRecipeRevision(publishedId, copyName.trim());
      if (owner.current !== request) return;
      void loadLibrary();
      navigate(`/recipes/${encodeURIComponent(copied.id)}`);
    } catch (failure) {
      if (owner.current === request)
        setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      if (owner.current === request) setBusy(false);
    }
  }

  function chooseDefinition(id: string) {
    if (dirty || Object.keys(jsonErrors).length) {
      setError(
        "Save your changes or explicitly discard them before switching definitions.",
      );
      return;
    }
    navigate(id ? `/recipes/${encodeURIComponent(id)}` : "/recipes");
    if (!id && !definitionId) {
      owner.current += 1;
      publicationRef.current = null;
      setPublication(null);
      setPublicationReadError(null);
      setPublishedId("");
      setRevisions(null);
      adoptDraft(null);
    }
  }

  async function browseBlock(id: string, after = 0, highWater?: number) {
    if (!after) {
      setBlockDefinition(id);
      setBlockRevisions(null);
    }
    const request = ++blockRequest.current;
    const stepId = selectedRef.current;
    if (!id) return;
    try {
      const versions = await fetchRecipeRevisions(id, after, highWater);
      if (
        versions.items.some(
          (item) =>
            item.definitionId !== id || item.definition.kind !== "block",
        )
      ) {
        throw new Error(
          "Block response does not belong to the selected reusable definition.",
        );
      }
      if (blockRequest.current === request && selectedRef.current === stepId) {
        setBlockRevisions((previous) => ({
          ...versions,
          items: after
            ? [...(previous?.items ?? []), ...versions.items]
            : versions.items,
        }));
      }
    } catch (failure) {
      if (blockRequest.current === request)
        setError(failure instanceof Error ? failure.message : String(failure));
    }
  }

  function pinBlock(revision: RecipeRevision, step: RecipeStep) {
    const inputs: Record<string, RecipeInput> = {};
    for (const [name, parameter] of Object.entries(
      revision.definition.parameters ?? {},
    )) {
      if (parameter.required)
        inputs[name] = { type: parameter.type, optional: false, binding: null };
    }
    const outputs: Record<string, RecipeOutput> = {};
    for (const [name, output] of Object.entries(
      revision.definition.outputs ?? {},
    )) {
      outputs[name] = { type: output.type, description: output.description };
    }
    const required = [
      ...new Set(
        (revision.definition.steps ?? []).flatMap(
          (item) => item.capabilities ?? [],
        ),
      ),
    ];
    updateStep({
      ...step,
      runner: { kind: "block", revisionId: revision.id },
      inputs,
      outputs,
      capabilities: required,
      workspace: {
        mode: required.includes("write") ? "mutable" : "snapshot",
        key: "job",
        sourceInput: null,
      },
    });
  }

  const steps = definition?.steps ?? [];
  const selected = steps.find((item) => item.id === selectedId);
  const invalidJson = Object.keys(jsonErrors).length > 0;
  const currentPublication =
    publication?.definitionId === (draft?.id ?? definitionId)
      ? publication
      : null;
  const errorBanner = error && (
    <div
      role="alert"
      className="break-words rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm"
    >
      {error}
    </div>
  );

  if (!definition)
    return (
      <div className="mx-auto max-w-3xl space-y-3 p-4">
        {busy ? <p role="status">Loading recipe...</p> : errorBanner}
        {!busy && (
          <Button variant="secondary" onClick={() => navigate("/recipes")}>
            Open recipe library
          </Button>
        )}
      </div>
    );

  return (
    <div className="mx-auto max-w-[1200px] space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="break-words text-xl font-semibold">
            {definition.name || "Untitled recipe"}
          </h1>
          <p className="mt-1 text-[13px] text-muted-foreground">
            {definition.description || "Configure work, not an execution."}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Active runs keep their pinned definitions.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {publicationReadError
              ? "Published; draft refresh needed"
              : draft
                ? `Draft version ${draft.version}`
                : "New draft"}
            {dirty ? " / unsaved" : ""}
          </span>
          <Button
            variant="outline"
            disabled={busy || invalidJson || Boolean(publicationReadError)}
            onClick={() => void persist(false)}
          >
            Save draft
          </Button>
          <Button
            disabled={busy || invalidJson || Boolean(publicationReadError)}
            onClick={() => void persist(true)}
          >
            Publish revision
          </Button>
        </div>
      </header>
      {currentPublication && (
        <div
          role="status"
          aria-label="Publication receipt"
          className="rounded-md border border-primary/30 bg-primary/5 p-3 text-sm"
        >
          Published revision {currentPublication.revision}. Checksum{" "}
          {currentPublication.checksum.slice(0, 12)}.
        </div>
      )}
      {currentPublication && publicationReadError && (
        <div
          role="alert"
          className="space-y-2 rounded-md border border-border p-3 text-sm"
        >
          <p>
            Publication succeeded. Draft/history refresh failed:{" "}
            {publicationReadError}
          </p>
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => void refreshPublication(currentPublication)}
          >
            Retry publication read
          </Button>
        </div>
      )}
      {errorBanner}
      {error && (dirty || invalidJson) && (
        <Button
          variant="secondary"
          disabled={busy || Boolean(publicationReadError)}
          onClick={() => {
            adoptDraft(draft);
            setError(null);
          }}
        >
          Discard local edits
        </Button>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="Recipe library"
          className={`${inputStyle} sm:max-w-md`}
          value={draft?.id ?? ""}
          disabled={busy}
          onChange={(event) => chooseDefinition(event.target.value)}
        >
          <option value="">New definition</option>
          {draft && !library?.items.some((item) => item.id === draft.id) && (
            <option value={draft.id}>{draft.definition.name}</option>
          )}
          {library?.items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.definition.name} / {item.definition.kind}
            </option>
          ))}
        </select>
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() => chooseDefinition("")}
        >
          <Plus size={14} /> New draft
        </Button>
        {library?.nextCursor != null && (
          <Button
            variant="ghost"
            onClick={() =>
              void loadLibrary(library.nextCursor ?? 0, library.highWater)
            }
          >
            More definitions
          </Button>
        )}
        {libraryError && (
          <div role="alert" className="text-xs text-red-500">
            {libraryError}{" "}
            <button className="underline" onClick={() => void loadLibrary()}>
              Retry library read
            </button>
          </div>
        )}
      </div>
      <fieldset
        disabled={busy || Boolean(publicationReadError)}
        className="min-w-0 space-y-4 [&_input]:min-w-0 [&_.font-mono]:break-all"
      >
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_12rem]">
          <Input
            aria-label="Recipe name"
            value={definition.name}
            onChange={(event) =>
              change({ ...definition, name: event.target.value })
            }
          />
          <select
            aria-label="Definition kind"
            className={inputStyle}
            value={definition.kind}
            onChange={(event) => {
              if (
                event.target.value === "recipe" ||
                event.target.value === "block"
              )
                change({ ...definition, kind: event.target.value });
            }}
          >
            <option value="recipe">Recipe</option>
            <option value="block">Reusable block</option>
          </select>
        </div>
        <Input
          aria-label="Recipe description"
          value={definition.description}
          onChange={(event) =>
            change({ ...definition, description: event.target.value })
          }
        />
        <div className="grid items-start gap-4 md:grid-cols-[270px_minmax(0,1fr)]">
          <aside className="min-w-0 rounded-lg border border-border bg-card p-3">
            <h2 className="mb-3 text-sm font-semibold">Recipe steps</h2>
            <div className="space-y-2">
              {steps.map((step) => (
                <button
                  key={step.id}
                  type="button"
                  aria-pressed={selectedId === step.id}
                  className={`w-full rounded-md border p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${selectedId === step.id ? "border-primary bg-primary/5" : "border-border hover:bg-accent/30"}`}
                  onClick={() => selectStep(step.id)}
                >
                  <span className="block truncate text-[13px] font-semibold">
                    {step.title}
                  </span>
                  <span className="mt-1 block text-xs text-muted-foreground">
                    {step.runner.kind} /{" "}
                    {(step.capabilities ?? []).join(", ") || "decision only"}
                  </span>
                  <span className="mt-1 block break-words text-[11px] text-muted-foreground">
                    {Object.values(step.inputs ?? {})
                      .flatMap((input) =>
                        input.binding?.source === "output"
                          ? [`${input.binding.stepId}.${input.binding.output}`]
                          : [],
                      )
                      .join(", ") || "No earlier step inputs"}
                  </span>
                </button>
              ))}
            </div>
            <Button
              className="mt-3 w-full"
              variant="secondary"
              onClick={() => {
                const added = newStep(
                  `step_${crypto.randomUUID()}`,
                  "New step",
                );
                change({ ...definition, steps: [...steps, added] });
                selectStep(added.id);
              }}
            >
              <Plus size={13} /> Add step / insert block
            </Button>
          </aside>
          <section className="min-w-0 rounded-lg border border-border bg-card p-4">
            <h2 className="mb-2 text-base font-semibold">Step definition</h2>
            {!selected ? (
              <p className="text-sm text-muted-foreground">
                Add or select a step.
              </p>
            ) : (
              <>
                <Row label="Step name and kind">
                  <Input
                    aria-label="Step name"
                    value={selected.title}
                    onChange={(event) =>
                      updateStep({ ...selected, title: event.target.value })
                    }
                  />
                  <select
                    aria-label="Runner kind"
                    className={inputStyle}
                    value={selected.runner.kind}
                    onChange={(event) => {
                      const kind = event.target.value;
                      if (
                        kind !== "agent" &&
                        kind !== "command" &&
                        kind !== "human" &&
                        kind !== "block"
                      )
                        return;
                      forgetJson(`${selected.id}.argv`);
                      updateStep({
                        ...selected,
                        runner: newRunner(kind),
                        capabilities:
                          kind === "human"
                            ? []
                            : kind === "command"
                              ? ["read", "shell"]
                              : ["read"],
                        workspace: {
                          mode: kind === "human" ? "none" : "snapshot",
                          key: "job",
                          sourceInput: null,
                        },
                        routes:
                          kind === "human"
                            ? { approved: route(), rejected: route("failed") }
                            : { succeeded: route(), failed: route("failed") },
                        retry: {
                          maxAttempts: 1,
                          retryOn: [],
                          exhaustion: route("failed"),
                        },
                      });
                    }}
                  >
                    {["agent", "command", "human", "block"].map((kind) => (
                      <option key={kind}>{kind}</option>
                    ))}
                  </select>
                  <p className="break-all font-mono text-[11px] text-muted-foreground">
                    Stable ID: {selected.id}
                  </p>
                </Row>
                <Row label="Instructions">
                  <textarea
                    aria-label="Step instructions"
                    rows={3}
                    className={inputStyle}
                    value={selected.instructions}
                    onChange={(event) =>
                      updateStep({
                        ...selected,
                        instructions: event.target.value,
                      })
                    }
                  />
                </Row>
                {selected.runner.kind === "agent" && (
                  <Row label="Model and session">
                    <Input
                      aria-label="Step model"
                      placeholder="Inherit configured model at launch"
                      value={selected.runner.model ?? ""}
                      onChange={(event) => {
                        if (selected.runner.kind === "agent")
                          updateStep({
                            ...selected,
                            runner: {
                              ...selected.runner,
                              model: event.target.value || null,
                            },
                          });
                      }}
                    />
                    <Input
                      aria-label="Step provider"
                      placeholder="Inherit Job provider"
                      value={selected.runner.provider ?? ""}
                      onChange={(event) => {
                        if (selected.runner.kind === "agent")
                          updateStep({
                            ...selected,
                            runner: {
                              ...selected.runner,
                              provider: event.target.value || null,
                            },
                          });
                      }}
                    />
                    <select
                      aria-label="Session policy"
                      className={inputStyle}
                      value={selected.runner.session}
                      onChange={(event) => {
                        if (
                          selected.runner.kind === "agent" &&
                          (event.target.value === "new" ||
                            event.target.value === "reuse")
                        )
                          updateStep({
                            ...selected,
                            runner: {
                              ...selected.runner,
                              session: event.target.value,
                            },
                          });
                      }}
                    >
                      <option value="new">Isolated step session</option>
                      <option value="reuse">Reuse this step's session</option>
                    </select>
                    <Input
                      aria-label="Maximum agent turns"
                      type="number"
                      min={1}
                      max={100}
                      value={selected.runner.maxTurns}
                      onChange={(event) => {
                        if (selected.runner.kind === "agent")
                          updateStep({
                            ...selected,
                            runner: {
                              ...selected.runner,
                              maxTurns: Number(event.target.value),
                            },
                          });
                      }}
                    />
                  </Row>
                )}
                {selected.runner.kind === "command" && (
                  <Row label="Command runner">
                    {jsonField(
                      `${selected.id}.argv`,
                      selected.runner.argv ?? [],
                      (value) => {
                        const argv = commandArguments(value);
                        if (selected.runner.kind === "command")
                          updateStep({
                            ...selected,
                            runner: {
                              ...selected.runner,
                              argv,
                            },
                          });
                      },
                      "Command arguments",
                    )}
                    <p className="text-xs text-muted-foreground">
                      JSON array of exact argument strings and typed input
                      references, for example{" "}
                      {'{"input":"code","field":"commit"}'}. Newlines and
                      backslashes remain inside their argument. No shell
                      interpolation or agent session is created.
                    </p>
                    <Input
                      aria-label="Command timeout seconds"
                      type="number"
                      min={1}
                      max={3600}
                      value={selected.runner.timeoutSeconds}
                      onChange={(event) => {
                        if (selected.runner.kind === "command")
                          updateStep({
                            ...selected,
                            runner: {
                              ...selected.runner,
                              timeoutSeconds: Number(event.target.value),
                            },
                          });
                      }}
                    />
                    <Input
                      aria-label="Reconciled writer step IDs"
                      placeholder="Explicit reconciliation: comma-separated writer IDs"
                      value={(selected.runner.reconciles ?? []).join(", ")}
                      onChange={(event) => {
                        if (selected.runner.kind === "command")
                          updateStep({
                            ...selected,
                            runner: {
                              ...selected.runner,
                              reconciles: event.target.value
                                .split(",")
                                .map((item) => item.trim())
                                .filter(Boolean),
                            },
                          });
                      }}
                    />
                  </Row>
                )}
                {selected.runner.kind === "human" && (
                  <Row label="Human authority">
                    <textarea
                      aria-label="Proposed action"
                      rows={3}
                      className={inputStyle}
                      value={selected.runner.proposedAction}
                      onChange={(event) => {
                        if (selected.runner.kind === "human")
                          updateStep({
                            ...selected,
                            runner: {
                              ...selected.runner,
                              proposedAction: event.target.value,
                            },
                          });
                      }}
                    />
                    <p className="text-xs text-muted-foreground">
                      Approval binds the exact inputs, action and next step.
                      Rejection or stale evidence never authorizes that action.
                    </p>
                  </Row>
                )}
                {selected.runner.kind === "block" && (
                  <Row label="Pinned reusable block">
                    <Input
                      aria-label="Pinned block revision ID"
                      value={selected.runner.revisionId}
                      onChange={(event) =>
                        updateStep({
                          ...selected,
                          runner: {
                            kind: "block",
                            revisionId: event.target.value,
                          },
                        })
                      }
                    />
                    <select
                      aria-label="Browse reusable blocks"
                      className={inputStyle}
                      value={blockDefinition}
                      onChange={(event) => void browseBlock(event.target.value)}
                    >
                      <option value="">Browse a saved block...</option>
                      {library?.items
                        .filter((item) => item.definition.kind === "block")
                        .map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.definition.name}
                          </option>
                        ))}
                    </select>
                    {blockRevisions && (
                      <select
                        aria-label="Block revision"
                        className={inputStyle}
                        value={selected.runner.revisionId}
                        onChange={(event) => {
                          const revision = blockRevisions.items.find(
                            (item) => item.id === event.target.value,
                          );
                          if (revision) pinBlock(revision, selected);
                        }}
                      >
                        <option value="">Choose published revision...</option>
                        {blockRevisions.items.map((item) => (
                          <option key={item.id} value={item.id}>
                            Revision {item.revision} /{" "}
                            {item.checksum.slice(0, 12)}
                          </option>
                        ))}
                      </select>
                    )}
                    {blockRevisions?.nextCursor != null && (
                      <Button
                        variant="ghost"
                        onClick={() =>
                          void browseBlock(
                            blockDefinition,
                            blockRevisions.nextCursor ?? 0,
                            blockRevisions.highWater,
                          )
                        }
                      >
                        More block revisions
                      </Button>
                    )}
                    <p className="text-xs text-muted-foreground">
                      Bind required parameters below. Nested definitions remain
                      pinned; copying never carries approvals or completed
                      state.
                    </p>
                  </Row>
                )}
                <Row label="Capabilities">
                  <div className="flex flex-wrap gap-3">
                    {capabilities.map((capability) => (
                      <label
                        key={capability}
                        className="flex items-center gap-1.5 text-xs"
                      >
                        <input
                          type="checkbox"
                          disabled={selected.runner.kind === "human"}
                          checked={
                            selected.capabilities?.includes(capability) ?? false
                          }
                          onChange={(event) =>
                            updateStep({
                              ...selected,
                              capabilities: event.target.checked
                                ? [...(selected.capabilities ?? []), capability]
                                : selected.capabilities?.filter(
                                    (item) => item !== capability,
                                  ),
                            })
                          }
                        />
                        {capability.replace(/_/g, " ")}
                      </label>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Requests are intersected with Job policy and actual adapter
                    support. Model names do not grant permissions; command
                    authorization still applies.
                  </p>
                </Row>
                <Row label="Workspace policy">
                  <select
                    aria-label="Workspace policy"
                    className={inputStyle}
                    value={selected.workspace?.mode ?? "snapshot"}
                    onChange={(event) => {
                      const mode = event.target.value;
                      if (
                        mode === "none" ||
                        mode === "snapshot" ||
                        mode === "mutable" ||
                        mode === "isolated"
                      )
                        updateStep({
                          ...selected,
                          workspace: {
                            ...(selected.workspace ?? { key: "job" }),
                            mode,
                          },
                        });
                    }}
                  >
                    {["none", "snapshot", "mutable", "isolated"].map((mode) => (
                      <option key={mode}>{mode}</option>
                    ))}
                  </select>
                  <Input
                    aria-label="Workspace key"
                    value={selected.workspace?.key ?? "job"}
                    onChange={(event) =>
                      updateStep({
                        ...selected,
                        workspace: {
                          ...(selected.workspace ?? { mode: "snapshot" }),
                          key: event.target.value,
                        },
                      })
                    }
                  />
                  <Input
                    aria-label="Workspace code input"
                    placeholder="Pinned Job workspace, or name a required code_ref input"
                    value={selected.workspace?.sourceInput ?? ""}
                    onChange={(event) =>
                      updateStep({
                        ...selected,
                        workspace: {
                          ...(selected.workspace ?? {
                            mode: "snapshot",
                            key: "job",
                          }),
                          sourceInput: event.target.value || null,
                        },
                      })
                    }
                  />
                </Row>
                <Row label="Input bindings">
                  {Object.entries(selected.inputs ?? {}).map(
                    ([name, input]) => (
                      <div
                        key={name}
                        className="space-y-2 rounded-md border border-border bg-background p-2"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono text-xs">{name}</span>
                          <button
                            aria-label={`Remove input ${name}`}
                            onClick={() => {
                              const inputs = { ...selected.inputs };
                              delete inputs[name];
                              forgetJson(`${selected.id}.${name}`);
                              updateStep({ ...selected, inputs });
                            }}
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                        <ValueTypeSelect
                          label={`${name} input type`}
                          value={input.type}
                          onChange={(type) =>
                            updateStep({
                              ...selected,
                              inputs: {
                                ...selected.inputs,
                                [name]: { ...input, type },
                              },
                            })
                          }
                        />
                        <select
                          aria-label={`${name} binding source`}
                          className={inputStyle}
                          value={input.binding?.source ?? ""}
                          onChange={(event) => {
                            forgetJson(`${selected.id}.${name}`);
                            const source = event.target.value;
                            const binding: RecipeInput["binding"] =
                              source === "parameter"
                                ? { source, parameter: "" }
                                : source === "output"
                                  ? { source, stepId: "", output: "" }
                                  : source === "literal"
                                    ? { source, value: "" }
                                    : null;
                            updateStep({
                              ...selected,
                              inputs: {
                                ...selected.inputs,
                                [name]: { ...input, binding },
                              },
                            });
                          }}
                        >
                          <option value="">Unbound</option>
                          <option value="parameter">Recipe parameter</option>
                          <option value="output">Exact step output</option>
                          <option value="literal">Literal JSON value</option>
                        </select>
                        {input.binding?.source === "parameter" && (
                          <select
                            aria-label={`${name} parameter`}
                            className={inputStyle}
                            value={input.binding.parameter}
                            onChange={(event) =>
                              updateStep({
                                ...selected,
                                inputs: {
                                  ...selected.inputs,
                                  [name]: {
                                    ...input,
                                    binding: {
                                      source: "parameter",
                                      parameter: event.target.value,
                                    },
                                  },
                                },
                              })
                            }
                          >
                            <option value="">Select parameter...</option>
                            {Object.keys(definition.parameters ?? {}).map(
                              (key) => (
                                <option key={key}>{key}</option>
                              ),
                            )}
                          </select>
                        )}
                        {input.binding?.source === "output" && (
                          <div className="space-y-2">
                            <select
                              aria-label={`${name} producing step`}
                              className={inputStyle}
                              value={input.binding.stepId}
                              onChange={(event) =>
                                updateStep({
                                  ...selected,
                                  inputs: {
                                    ...selected.inputs,
                                    [name]: {
                                      ...input,
                                      binding: {
                                        source: "output",
                                        stepId: event.target.value,
                                        output: "",
                                      },
                                    },
                                  },
                                })
                              }
                            >
                              <option value="">Select producing step...</option>
                              {steps
                                .filter((item) => item.id !== selected.id)
                                .map((item) => (
                                  <option key={item.id} value={item.id}>
                                    {item.title} / {item.id}
                                  </option>
                                ))}
                            </select>
                            <Input
                              aria-label={`${name} output name`}
                              placeholder="Exact declared output name"
                              value={input.binding.output}
                              onChange={(event) => {
                                if (input.binding?.source === "output")
                                  updateStep({
                                    ...selected,
                                    inputs: {
                                      ...selected.inputs,
                                      [name]: {
                                        ...input,
                                        binding: {
                                          ...input.binding,
                                          output: event.target.value,
                                        },
                                      },
                                    },
                                  });
                              }}
                            />
                          </div>
                        )}
                        {input.binding?.source === "literal" &&
                          jsonField(
                            `${selected.id}.${name}`,
                            input.binding.value,
                            (value) =>
                              updateStep({
                                ...selected,
                                inputs: {
                                  ...selected.inputs,
                                  [name]: {
                                    ...input,
                                    binding: { source: "literal", value },
                                  },
                                },
                              }),
                            `${name} literal JSON`,
                          )}
                        <label className="flex items-center gap-2 text-xs">
                          <input
                            type="checkbox"
                            checked={input.optional}
                            onChange={(event) =>
                              updateStep({
                                ...selected,
                                inputs: {
                                  ...selected.inputs,
                                  [name]: {
                                    ...input,
                                    optional: event.target.checked,
                                  },
                                },
                              })
                            }
                          />{" "}
                          Explicit optional / degraded input
                        </label>
                        {input.optional && (
                          <Input
                            aria-label={`${name} degraded reason`}
                            value={input.degradedReason ?? ""}
                            placeholder="Why a missing or failed diagnostic input is permitted"
                            onChange={(event) =>
                              updateStep({
                                ...selected,
                                inputs: {
                                  ...selected.inputs,
                                  [name]: {
                                    ...input,
                                    degradedReason: event.target.value,
                                  },
                                },
                              })
                            }
                          />
                        )}
                      </div>
                    ),
                  )}
                  <div className="flex gap-2">
                    <Input
                      aria-label="New input name"
                      value={newInput}
                      onChange={(event) => setNewInput(event.target.value)}
                    />
                    <Button
                      variant="secondary"
                      disabled={
                        !newInput.trim() ||
                        newInput.trim() in (selected.inputs ?? {})
                      }
                      onClick={() => {
                        updateStep({
                          ...selected,
                          inputs: {
                            ...selected.inputs,
                            [newInput.trim()]: {
                              type: "string",
                              binding: null,
                              optional: false,
                            },
                          },
                        });
                        setNewInput("");
                      }}
                    >
                      <Plus size={13} /> Input
                    </Button>
                  </div>
                </Row>
                <Row label="Output contract">
                  {Object.entries(selected.outputs ?? {}).map(
                    ([name, output]) => (
                      <div
                        key={name}
                        className="space-y-2 rounded-md border border-border bg-background p-2"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-mono text-xs">{name}</span>
                          <button
                            aria-label={`Remove output ${name}`}
                            onClick={() => {
                              const outputs = { ...selected.outputs };
                              delete outputs[name];
                              updateStep({ ...selected, outputs });
                            }}
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                        <ValueTypeSelect
                          label={`${name} output type`}
                          value={output.type}
                          onChange={(type) =>
                            updateStep({
                              ...selected,
                              outputs: {
                                ...selected.outputs,
                                [name]: { ...output, type },
                              },
                            })
                          }
                        />
                        <Input
                          aria-label={`${name} output description`}
                          value={output.description}
                          onChange={(event) =>
                            updateStep({
                              ...selected,
                              outputs: {
                                ...selected.outputs,
                                [name]: {
                                  ...output,
                                  description: event.target.value,
                                },
                              },
                            })
                          }
                        />
                      </div>
                    ),
                  )}
                  <div className="flex gap-2">
                    <Input
                      aria-label="New output name"
                      value={newOutput}
                      onChange={(event) => setNewOutput(event.target.value)}
                    />
                    <Button
                      variant="secondary"
                      disabled={
                        !newOutput.trim() ||
                        newOutput.trim() in (selected.outputs ?? {})
                      }
                      onClick={() => {
                        updateStep({
                          ...selected,
                          outputs: {
                            ...selected.outputs,
                            [newOutput.trim()]: {
                              type: "string",
                              description: "",
                            },
                          },
                        });
                        setNewOutput("");
                      }}
                    >
                      <Plus size={13} /> Output
                    </Button>
                  </div>
                </Row>
                <Row label="Dependencies and joins">
                  <label className="flex items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={
                        definition.entrySteps?.includes(selected.id) ?? false
                      }
                      onChange={(event) =>
                        change({
                          ...definition,
                          entrySteps: event.target.checked
                            ? [...(definition.entrySteps ?? []), selected.id]
                            : definition.entrySteps?.filter(
                                (id) => id !== selected.id,
                              ),
                        })
                      }
                    />{" "}
                    Entry step
                  </label>
                  {steps
                    .filter((item) => item.id !== selected.id)
                    .map((item) => {
                      const dependency = selected.dependsOn?.find(
                        (entry) => entry.stepId === item.id,
                      );
                      return (
                        <div key={item.id} className="space-y-1">
                          <label className="flex items-center gap-2 text-xs">
                            <input
                              type="checkbox"
                              checked={Boolean(dependency)}
                              onChange={(event) =>
                                updateStep({
                                  ...selected,
                                  dependsOn: event.target.checked
                                    ? [
                                        ...(selected.dependsOn ?? []),
                                        {
                                          stepId: item.id,
                                          outcomes: [
                                            item.runner.kind === "human"
                                              ? "approved"
                                              : "succeeded",
                                          ],
                                        },
                                      ]
                                    : selected.dependsOn?.filter(
                                        (entry) => entry.stepId !== item.id,
                                      ),
                                })
                              }
                            />{" "}
                            {item.title} / {item.id}
                          </label>
                          {dependency && (
                            <Input
                              aria-label={`Required outcomes from ${item.id}`}
                              value={(
                                dependency.outcomes ?? ["succeeded"]
                              ).join(", ")}
                              onChange={(event) =>
                                updateStep({
                                  ...selected,
                                  dependsOn: selected.dependsOn?.map((entry) =>
                                    entry.stepId === item.id
                                      ? {
                                          ...entry,
                                          outcomes: event.target.value
                                            .split(",")
                                            .map((value) => value.trim())
                                            .filter(Boolean),
                                        }
                                      : entry,
                                  ),
                                })
                              }
                            />
                          )}
                        </div>
                      );
                    })}
                  <p className="text-xs text-muted-foreground">
                    All declared dependencies must match. A failed or uncertain
                    producer does not satisfy a successful join.
                  </p>
                </Row>
                <Row label="Outcome routes">
                  {Object.entries(selected.routes ?? {}).map(
                    ([outcome, target]) => (
                      <div key={outcome} className="space-y-1">
                        <label className="text-xs font-medium">{outcome}</label>
                        <select
                          aria-label={`${outcome} route kind`}
                          className={inputStyle}
                          value={target.terminal ?? "steps"}
                          onChange={(event) => {
                            const terminal = event.target.value;
                            if (terminal === "steps")
                              updateStep({
                                ...selected,
                                routes: {
                                  ...selected.routes,
                                  [outcome]: { nextSteps: [] },
                                },
                              });
                            else if (
                              terminal === "succeeded" ||
                              terminal === "failed" ||
                              terminal === "canceled"
                            )
                              updateStep({
                                ...selected,
                                routes: {
                                  ...selected.routes,
                                  [outcome]: { terminal, nextSteps: [] },
                                },
                              });
                          }}
                        >
                          <option value="steps">Route to steps / fanout</option>
                          <option value="succeeded">End successfully</option>
                          <option value="failed">End failed</option>
                          <option value="canceled">End canceled</option>
                        </select>
                        {!target.terminal && (
                          <Input
                            aria-label={`${outcome} next steps`}
                            placeholder="Comma-separated stable step IDs"
                            value={(target.nextSteps ?? []).join(", ")}
                            onChange={(event) =>
                              updateStep({
                                ...selected,
                                routes: {
                                  ...selected.routes,
                                  [outcome]: {
                                    nextSteps: event.target.value
                                      .split(",")
                                      .map((value) => value.trim())
                                      .filter(Boolean),
                                  },
                                },
                              })
                            }
                          />
                        )}
                      </div>
                    ),
                  )}
                </Row>
                <Row label="Bounded retry">
                  <Input
                    aria-label="Maximum total attempts"
                    type="number"
                    min={1}
                    max={20}
                    disabled={selected.runner.kind === "human"}
                    value={selected.retry?.maxAttempts ?? 1}
                    onChange={(event) =>
                      updateStep({
                        ...selected,
                        retry: {
                          ...(selected.retry ?? {
                            retryOn: [],
                            exhaustion: route("failed"),
                          }),
                          maxAttempts: Number(event.target.value),
                        },
                      })
                    }
                  />
                  <label className="flex items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      disabled={selected.runner.kind === "human"}
                      checked={
                        selected.retry?.retryOn?.includes("failed") ?? false
                      }
                      onChange={(event) =>
                        updateStep({
                          ...selected,
                          retry: {
                            ...(selected.retry ?? {
                              maxAttempts: 1,
                              exhaustion: route("failed"),
                            }),
                            retryOn: event.target.checked ? ["failed"] : [],
                          },
                        })
                      }
                    />{" "}
                    Retry known failure, never uncertain effects
                  </label>
                  <Input
                    aria-label="Exhaustion route"
                    placeholder="Successor IDs, or blank to end failed"
                    value={(selected.retry?.exhaustion?.nextSteps ?? []).join(
                      ", ",
                    )}
                    onChange={(event) => {
                      const nextSteps = event.target.value
                        .split(",")
                        .map((value) => value.trim())
                        .filter(Boolean);
                      updateStep({
                        ...selected,
                        retry: {
                          ...(selected.retry ?? {
                            maxAttempts: 1,
                            retryOn: [],
                          }),
                          exhaustion: nextSteps.length
                            ? { nextSteps }
                            : route("failed"),
                        },
                      });
                    }}
                  />
                </Row>
                <Button
                  variant="ghost"
                  className="mt-3"
                  onClick={() => {
                    forgetJson(`${selected.id}.`);
                    change({
                      ...definition,
                      steps: steps.filter((item) => item.id !== selected.id),
                      entrySteps: definition.entrySteps?.filter(
                        (id) => id !== selected.id,
                      ),
                    });
                    selectStep(
                      steps.find((item) => item.id !== selected.id)?.id ?? "",
                    );
                  }}
                >
                  <Trash2 size={13} /> Remove step
                </Button>
              </>
            )}
          </section>
        </div>
        <details className="rounded-lg border border-border bg-card p-4">
          <summary className="cursor-pointer text-sm font-semibold">
            Recipe inputs and reusable block outputs
          </summary>
          <div className="mt-3 space-y-3">
            {Object.entries(definition.parameters ?? {}).map(
              ([name, parameter]) => (
                <div
                  key={name}
                  className="space-y-2 rounded-md border border-border p-3"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs">{name}</span>
                    <button
                      aria-label={`Remove parameter ${name}`}
                      onClick={() => {
                        const parameters = { ...definition.parameters };
                        delete parameters[name];
                        forgetJson(`parameter.${name}`);
                        change({ ...definition, parameters });
                      }}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                  <ValueTypeSelect
                    label={`${name} parameter type`}
                    value={parameter.type}
                    onChange={(type) =>
                      change({
                        ...definition,
                        parameters: {
                          ...definition.parameters,
                          [name]: { ...parameter, type },
                        },
                      })
                    }
                  />
                  <Input
                    aria-label={`${name} parameter description`}
                    value={parameter.description}
                    onChange={(event) =>
                      change({
                        ...definition,
                        parameters: {
                          ...definition.parameters,
                          [name]: {
                            ...parameter,
                            description: event.target.value,
                          },
                        },
                      })
                    }
                  />
                  <label className="flex items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={parameter.required}
                      onChange={(event) => {
                        if (event.target.checked)
                          forgetJson(`parameter.${name}`);
                        change({
                          ...definition,
                          parameters: {
                            ...definition.parameters,
                            [name]: {
                              ...parameter,
                              required: event.target.checked,
                              default: event.target.checked
                                ? null
                                : parameter.default,
                            },
                          },
                        });
                      }}
                    />{" "}
                    Required at launch
                  </label>
                  {!parameter.required &&
                    jsonField(
                      `parameter.${name}`,
                      parameter.default,
                      (value) =>
                        change({
                          ...definition,
                          parameters: {
                            ...definition.parameters,
                            [name]: { ...parameter, default: value },
                          },
                        }),
                      `${name} default JSON`,
                    )}
                </div>
              ),
            )}
            <div className="flex gap-2">
              <Input
                aria-label="New parameter name"
                value={newParameter}
                onChange={(event) => setNewParameter(event.target.value)}
              />
              <Button
                variant="secondary"
                disabled={
                  !newParameter.trim() ||
                  newParameter.trim() in (definition.parameters ?? {})
                }
                onClick={() => {
                  const parameter: RecipeParameter = {
                    type: "string",
                    required: true,
                    description: "",
                  };
                  change({
                    ...definition,
                    parameters: {
                      ...definition.parameters,
                      [newParameter.trim()]: parameter,
                    },
                  });
                  setNewParameter("");
                }}
              >
                <Plus size={13} /> Parameter
              </Button>
            </div>
            {Object.entries(definition.outputs ?? {}).map(([name, output]) => (
              <div
                key={name}
                className="space-y-2 rounded-md border border-border p-3"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs">Export: {name}</span>
                  <button
                    aria-label={`Remove export ${name}`}
                    onClick={() => {
                      const outputs = { ...definition.outputs };
                      delete outputs[name];
                      change({ ...definition, outputs });
                    }}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
                <ValueTypeSelect
                  label={`${name} export type`}
                  value={output.type}
                  onChange={(type) =>
                    change({
                      ...definition,
                      outputs: {
                        ...definition.outputs,
                        [name]: { ...output, type },
                      },
                    })
                  }
                />
                <Input
                  aria-label={`${name} export step`}
                  placeholder="Producing step ID"
                  value={output.binding.stepId}
                  onChange={(event) =>
                    change({
                      ...definition,
                      outputs: {
                        ...definition.outputs,
                        [name]: {
                          ...output,
                          binding: {
                            ...output.binding,
                            stepId: event.target.value,
                          },
                        },
                      },
                    })
                  }
                />
                <Input
                  aria-label={`${name} export output`}
                  placeholder="Declared output name"
                  value={output.binding.output}
                  onChange={(event) =>
                    change({
                      ...definition,
                      outputs: {
                        ...definition.outputs,
                        [name]: {
                          ...output,
                          binding: {
                            ...output.binding,
                            output: event.target.value,
                          },
                        },
                      },
                    })
                  }
                />
              </div>
            ))}
            <div className="flex gap-2">
              <Input
                aria-label="New export name"
                value={newExport}
                onChange={(event) => setNewExport(event.target.value)}
              />
              <Button
                variant="secondary"
                disabled={
                  !newExport.trim() ||
                  newExport.trim() in (definition.outputs ?? {})
                }
                onClick={() => {
                  change({
                    ...definition,
                    outputs: {
                      ...definition.outputs,
                      [newExport.trim()]: {
                        type: "string",
                        description: "",
                        binding: { source: "output", stepId: "", output: "" },
                      },
                    },
                  });
                  setNewExport("");
                }}
              >
                <Plus size={13} /> Export
              </Button>
            </div>
          </div>
        </details>
      </fieldset>
      <div className="space-y-3 rounded-lg border border-border bg-card p-4">
        <Button
          variant="secondary"
          disabled={busy || invalidJson}
          onClick={() => void checkDefinition()}
        >
          Validate definition
        </Button>
        {invalidJson && (
          <div role="alert" className="break-words text-sm text-red-500">
            {Object.entries(jsonErrors).map(([key, message]) => (
              <p key={key}>
                {key}: {message}
              </p>
            ))}
          </div>
        )}
        {validation && (
          <div
            role={validation.valid ? "status" : "alert"}
            className="space-y-1 text-sm"
          >
            {validation.valid
              ? "Definition valid. Saving or publishing does not execute it."
              : validation.issues.map((issue, index) => (
                  <button
                    key={`${issue.path}-${index}`}
                    className="block w-full break-words text-left text-red-500"
                    onClick={() => {
                      const id = issue.path.split(".")[1];
                      if (
                        issue.path.startsWith("steps.") &&
                        steps.some((step) => step.id === id) &&
                        id
                      )
                        selectStep(id);
                    }}
                  >
                    <ChevronRight size={12} className="inline" /> {issue.path}:{" "}
                    {issue.message}
                  </button>
                ))}
          </div>
        )}
        {(revisions || currentPublication) && (
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="Published revision"
              className={`${inputStyle} sm:max-w-xs`}
              value={publishedId}
              onChange={(event) => setPublishedId(event.target.value)}
            >
              <option value="">Choose published revision...</option>
              {currentPublication &&
                !revisions?.items.some(
                  (item) => item.id === currentPublication.id,
                ) && (
                  <option value={currentPublication.id}>
                    Revision {currentPublication.revision} /{" "}
                    {currentPublication.checksum.slice(0, 12)}
                  </option>
                )}
              {revisions?.items.map((item) => (
                <option key={item.id} value={item.id}>
                  Revision {item.revision} / {item.checksum.slice(0, 12)}
                </option>
              ))}
            </select>
            <Input
              aria-label="Copied definition name"
              className="sm:max-w-xs"
              value={copyName}
              onChange={(event) => setCopyName(event.target.value)}
            />
            <Button
              variant="secondary"
              disabled={
                busy || !publishedId || !copyName.trim() || dirty || invalidJson
              }
              onClick={() => void copyPublished()}
            >
              <Copy size={13} /> Copy to independent draft
            </Button>
            {revisions?.nextCursor != null && draft && (
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  const request = owner.current;
                  const pageRequest = ++revisionRequest.current;
                  void fetchRecipeRevisions(
                    draft.id,
                    revisions.nextCursor ?? 0,
                    revisions.highWater,
                  )
                    .then((page) => {
                      if (
                        owner.current === request &&
                        revisionRequest.current === pageRequest
                      )
                        setRevisions((previous) => ({
                          ...page,
                          items: [...(previous?.items ?? []), ...page.items],
                        }));
                    })
                    .catch((failure) => {
                      if (
                        owner.current === request &&
                        revisionRequest.current === pageRequest
                      )
                        setError(
                          failure instanceof Error
                            ? failure.message
                            : String(failure),
                        );
                    });
                }}
              >
                More revisions
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
