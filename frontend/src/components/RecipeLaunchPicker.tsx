import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import { fetchRecipes, fetchRecipeRevisions, resolveDefaultRecipe } from "../api/client";
import type { RecipeDraftList, RecipeRevision, RecipeRevisionList, RecipeSelection, RecipeValueType } from "../api/types";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

type ParameterValue = NonNullable<RecipeSelection["parameters"]>[string];

function matches(value: unknown, type: RecipeValueType): value is ParameterValue {
  if (type === "string") return typeof value === "string";
  if (type === "boolean") return typeof value === "boolean";
  if (type === "integer") return typeof value === "number" && Number.isSafeInteger(value);
  if (type === "number") return typeof value === "number" && Number.isFinite(value);
  if (type === "array") return Array.isArray(value);
  if (type === "object") return value !== null && typeof value === "object" && !Array.isArray(value);
  return value !== null && typeof value === "object" && "workspace" in value && typeof value.workspace === "string" &&
    value.workspace.length > 0 && "commit" in value && typeof value.commit === "string" && /^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$/.test(value.commit);
}

export function RecipeLaunchPicker({ planFirst, onChange }: {
  planFirst: boolean;
  onChange: (selection: RecipeSelection | null) => void;
}) {
  const [definitionId, setDefinitionId] = useState("");
  const [revision, setRevision] = useState<RecipeRevision | null>(null);
  const [drafts, setDrafts] = useState<RecipeDraftList | null>(null);
  const [revisions, setRevisions] = useState<RecipeRevisionList | null>(null);
  const [draftPage, setDraftPage] = useState<{ after: number; highWater?: number }>({ after: 0 });
  const [revisionPage, setRevisionPage] = useState<{ after: number; highWater?: number }>({ after: 0 });
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false);
  useLayoutEffect(() => {
    if (!definitionId) {
      setRevision(null);
      onChange(null);
    }
  }, [definitionId, planFirst, onChange]);
  useEffect(() => {
    let disposed = false;
    setListError(null);
    fetchRecipes(draftPage.after, draftPage.highWater).then((page) => {
      if (!disposed) setDrafts(page);
    }).catch((cause: unknown) => { if (!disposed) setListError(String(cause)); });
    return () => { disposed = true; };
  }, [draftPage, refresh]);
  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setError(null);
    if (!definitionId) {
      setRevision(null);
      onChange(null);
      resolveDefaultRecipe(planFirst).then((value) => {
        if (!disposed) { setRevision(value); setValues({}); }
      }).catch((cause: unknown) => { if (!disposed) setError(String(cause)); })
        .finally(() => { if (!disposed) setLoading(false); });
    } else {
      fetchRecipeRevisions(definitionId, revisionPage.after, revisionPage.highWater).then((page) => {
        if (!disposed) setRevisions(page);
      }).catch((cause: unknown) => { if (!disposed) setError(String(cause)); })
        .finally(() => { if (!disposed) setLoading(false); });
    }
    return () => { disposed = true; };
  }, [definitionId, planFirst, revisionPage, refresh, onChange]);
  const parsed = useMemo(() => {
    const parameters: NonNullable<RecipeSelection["parameters"]> = {};
    const errors: Record<string, string> = {};
    for (const [name, parameter] of Object.entries(revision?.definition.parameters ?? {})) {
      if (name === "request" && parameter.type === "string") continue;
      const raw = values[name];
      if (raw === undefined) {
        if (parameter.required !== false) errors[name] = `Supply ${parameter.type}.`;
        continue;
      }
      let value: unknown = raw;
      if (parameter.type !== "string") {
        try { value = JSON.parse(raw); }
        catch { errors[name] = `Enter valid ${parameter.type} JSON.`; continue; }
      }
      if (!matches(value, parameter.type)) errors[name] = `Expected ${parameter.type}.`;
      else parameters[name] = value;
    }
    return { parameters, errors };
  }, [revision, values]);
  useLayoutEffect(() => {
    onChange(revision && Object.keys(parsed.errors).length === 0 ? { revisionId: revision.id, parameters: parsed.parameters } : null);
  }, [revision, parsed, onChange]);
  function chooseDefinition(id: string) {
    onChange(null);
    setRevision(null);
    setValues({});
    setRevisions(null);
    setRevisionPage({ after: 0 });
    setDefinitionId(id);
  }
  return <section aria-label="Launch recipe" className="space-y-3 rounded-lg border border-primary/30 bg-primary/5 p-4">
    <p className="text-xs font-medium text-primary">Recipe / pinned at launch</p>
    <label className="block text-xs text-muted-foreground">Recipe
      <select aria-label="Launch recipe definition" className="mt-1 w-full rounded-md border border-input bg-background p-2 text-sm text-foreground" value={definitionId} onChange={(event) => chooseDefinition(event.target.value)}>
        <option value="">Configured default</option>
        {definitionId && !drafts?.items.some((item) => item.id === definitionId) && <option value={definitionId}>{revision?.definition.name ?? "Selected recipe"}</option>}
        {drafts?.items.filter((item) => item.definition.kind !== "block").map((item) => <option key={item.id} value={item.id}>{item.definition.name}</option>)}
      </select>
    </label>
    {drafts?.nextCursor != null && <Button variant="outline" size="sm" onClick={() => setDraftPage({ after: drafts.nextCursor!, highWater: drafts.highWater })}>More recipes</Button>}
    {draftPage.after > 0 && <Button variant="outline" size="sm" onClick={() => setDraftPage({ after: 0, highWater: draftPage.highWater })}>First recipes</Button>}
    {definitionId && <label className="block text-xs text-muted-foreground">Published revision
      <select aria-label="Launch recipe revision" className="mt-1 w-full rounded-md border border-input bg-background p-2 text-sm text-foreground" value={revision?.id ?? ""} onChange={(event) => {
        onChange(null);
        setValues({});
        setRevision(revisions?.items.find((item) => item.id === event.target.value) ?? null);
      }}>
        <option value="">Select an immutable revision</option>
        {revision && !revisions?.items.some((item) => item.id === revision.id) && <option value={revision.id}>Revision {revision.revision}</option>}
        {revisions?.items.map((item) => <option key={item.id} value={item.id}>Revision {item.revision} / {item.publishedAt}</option>)}
      </select>
    </label>}
    {definitionId && revisions?.nextCursor != null && <Button variant="outline" size="sm" onClick={() => setRevisionPage({ after: revisions.nextCursor!, highWater: revisions.highWater })}>More revisions</Button>}
    {definitionId && revisions?.items.length === 0 && !loading && <p className="text-sm">This recipe has no published revisions. Save and publish a valid definition before launch.</p>}
    {loading && <p role="status" className="text-xs text-muted-foreground">Resolving recipe...</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {listError && <p role="alert" className="text-sm text-destructive">Recipe list: {listError}</p>}
    {(error || listError) && <Button variant="outline" size="sm" onClick={() => setRefresh((value) => value + 1)}>Retry recipe read</Button>}
    {revision && <>
      <p className="text-sm font-semibold">{revision.definition.name} / revision {revision.revision}</p>
      {definitionId && <p className="text-xs text-muted-foreground">This published graph controls planning and gates. Standard / Plan First only changes the configured default recipe.</p>}
      {Object.entries(revision.definition.parameters ?? {}).map(([name, parameter]) => name === "request" && parameter.type === "string" ?
        <p key={name} className="text-xs text-muted-foreground">The Job request supplies the required request input.</p> :
        <label key={name} className="block space-y-1 text-xs text-muted-foreground">{name} ({parameter.type}{parameter.required === false ? ", optional" : ", required"})
          <Input aria-label={`Recipe input ${name}`} value={values[name] ?? ""} placeholder={parameter.description || (parameter.type === "string" ? "Text" : "JSON value")} onChange={(event) => setValues((current) => ({ ...current, [name]: event.target.value }))} />
          {parsed.errors[name] && <span className="block text-destructive">{parsed.errors[name]}</span>}
        </label>
      )}
      <details className="rounded-md border border-primary/20 p-3">
        <summary className="cursor-pointer text-sm font-medium">Resolved steps, inputs and capabilities</summary>
        <div className="mt-3 space-y-3">
          {revision.definition.steps?.map((step) => <div key={step.id} className="space-y-1 rounded-md bg-card p-3">
            <p className="text-sm font-medium">{step.title} / {step.runner.kind}</p>
            <p className="text-xs text-muted-foreground">Capabilities: {step.capabilities?.join(", ") || "none"} / workspace: {step.workspace?.mode ?? "snapshot"}</p>
            <p className="whitespace-pre-wrap text-xs">{step.instructions}</p>
            <pre className="whitespace-pre-wrap break-words text-xs">{JSON.stringify({ runner: step.runner, inputs: step.inputs, outputs: step.outputs, dependsOn: step.dependsOn, routes: step.routes, retry: step.retry }, null, 2)}</pre>
          </div>)}
          {Object.values(revision.blocks).map((block) => <details key={block.revisionId} className="rounded-md border border-border p-3"><summary className="cursor-pointer text-sm">Pinned block {block.definition.name} / revision {block.revision}</summary><pre className="mt-2 whitespace-pre-wrap break-words text-xs">{JSON.stringify(block, null, 2)}</pre></details>)}
        </div>
      </details>
    </>}
  </section>;
}
