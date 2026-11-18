import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../../api/client", () => ({
  copyRecipeRevision: vi.fn(),
  createRecipeDraft: vi.fn(),
  fetchRecipe: vi.fn(),
  fetchRecipeRevisions: vi.fn(),
  fetchRecipes: vi.fn(),
  publishRecipe: vi.fn(),
  saveRecipeDraft: vi.fn(),
  validateRecipe: vi.fn(),
  createJob: vi.fn(),
}));

import {
  createJob,
  createRecipeDraft,
  fetchRecipe,
  fetchRecipeRevisions,
  fetchRecipes,
  publishRecipe,
  saveRecipeDraft,
  validateRecipe,
} from "../../api/client";
import type { RecipeDraft, RecipeRevision } from "../../api/types";
import { RecipeDefinitionEditor } from "../RecipeDefinitionEditor";

const now = "2026-11-15T22:00:00Z";
let savedDraft: RecipeDraft | null = null;

function renderEditor(path = "/recipes") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/recipes/:definitionId?"
          element={<RecipeDefinitionEditor />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  savedDraft = null;
  vi.mocked(fetchRecipes).mockResolvedValue({
    items: [],
    nextCursor: null,
    highWater: 0,
  });
  vi.mocked(fetchRecipeRevisions).mockResolvedValue({
    items: [],
    nextCursor: null,
    highWater: 0,
  });
  vi.mocked(fetchRecipe).mockImplementation(async () => {
    if (!savedDraft) throw new Error("Draft not saved");
    return savedDraft;
  });
  vi.mocked(createRecipeDraft).mockImplementation(async (definition) => {
    savedDraft = {
      id: "draft-1",
      version: 1,
      definition,
      createdAt: now,
      updatedAt: now,
    };
    return savedDraft;
  });
  vi.mocked(saveRecipeDraft).mockImplementation(
    async (id, expectedVersion, definition) => ({
      id,
      version: expectedVersion + 1,
      definition,
      createdAt: now,
      updatedAt: now,
    }),
  );
  vi.mocked(validateRecipe).mockResolvedValue({ valid: true, issues: [] });
  vi.mocked(publishRecipe).mockImplementation(async (id, expectedVersion) => {
    if (
      !savedDraft ||
      savedDraft.id !== id ||
      savedDraft.version !== expectedVersion
    )
      throw new Error("Unexpected publication version");
    const definition = savedDraft.definition;
    savedDraft = { ...savedDraft, version: expectedVersion + 1 };
    return {
      id: "published-1",
      definitionId: id,
      revision: 1,
      definition,
      blocks: {},
      checksum: "b".repeat(64),
      publishedAt: now,
    };
  });
});

describe("RecipeDefinitionEditor", () => {
  it("saves an editable definition without launching or publishing work", async () => {
    let finishRefresh!: (value: RecipeDraft) => void;
    vi.mocked(fetchRecipe).mockReturnValueOnce(
      new Promise<RecipeDraft>((resolve) => { finishRefresh = resolve; }),
    );
    renderEditor();
    await screen.findByRole("heading", { name: "Step definition" });
    const previousVerify = screen.getByRole("button", { name: /^Verify / });
    fireEvent.change(screen.getByLabelText("Step instructions"), {
      target: {
        value:
          "Read only the requested files and return their exact source references.",
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await screen.findByText("Loading recipe...");
    expect(previousVerify).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Step instructions")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Verify / })).not.toBeInTheDocument();
    expect(fetchRecipe).toHaveBeenCalledWith("draft-1");
    const receipt = savedDraft;
    if (!receipt) throw new Error("Expected the saved draft receipt");
    await act(async () => { finishRefresh(receipt); });
    await screen.findByText("Draft version 1");
    expect(createRecipeDraft).toHaveBeenCalledOnce();
    const saved = vi.mocked(createRecipeDraft).mock.calls[0]?.[0];
    expect(
      saved?.steps?.find((step) => step.id === "context")?.instructions,
    ).toBe(
      "Read only the requested files and return their exact source references.",
    );
    expect(screen.getByLabelText("Step instructions")).toHaveValue(
      "Read only the requested files and return their exact source references.",
    );
    expect(saveRecipeDraft).not.toHaveBeenCalled();
    expect(publishRecipe).not.toHaveBeenCalled();
    expect(createJob).not.toHaveBeenCalled();
    const verify = screen.getByRole("button", { name: /^Verify / });
    expect(verify).toBeEnabled();
    fireEvent.click(verify);
    expect(await screen.findByLabelText("Command arguments")).toHaveValue(
      JSON.stringify(
        ["git", "show", "--check", { input: "code", field: "commit" }],
        null,
        2,
      ),
    );
    expect(verify).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByLabelText("Step model")).not.toBeInTheDocument();
    expect(publishRecipe).not.toHaveBeenCalled();
    expect(createJob).not.toHaveBeenCalled();
  });

  it("retains the successful publication receipt after canonical new-draft navigation", async () => {
    renderEditor();
    await screen.findByRole("heading", { name: "Step definition" });
    fireEvent.click(screen.getByRole("button", { name: "Publish revision" }));
    const receipt = await screen.findByLabelText("Publication receipt");
    await screen.findByText("Draft version 2");
    expect(receipt).toHaveTextContent("Published revision 1");
    expect(screen.getByLabelText("Published revision")).toHaveValue(
      "published-1",
    );
    expect(publishRecipe).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "New draft" }));
    await waitFor(() =>
      expect(
        screen.queryByLabelText("Publication receipt"),
      ).not.toBeInTheDocument(),
    );
  });

  it("keeps publish success visible when refresh fails and retries only the read", async () => {
    vi.mocked(fetchRecipe).mockRejectedValueOnce(new Error("Refresh offline"));
    renderEditor();
    await screen.findByRole("heading", { name: "Step definition" });
    fireEvent.click(screen.getByRole("button", { name: "Publish revision" }));
    expect(
      await screen.findByLabelText("Publication receipt"),
    ).toHaveTextContent("Published revision 1");
    expect(
      await screen.findByText(
        /Publication succeeded. Draft\/history refresh failed: Refresh offline/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Publish revision" }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Retry publication read" }),
    );
    await screen.findByText("Draft version 2");
    expect(screen.getByLabelText("Published revision")).toHaveValue(
      "published-1",
    );
    expect(publishRecipe).toHaveBeenCalledOnce();
    expect(createRecipeDraft).toHaveBeenCalledOnce();
    expect(saveRecipeDraft).not.toHaveBeenCalled();
  });

  it("round-trips multiline and backslash arguments with an actual typed input reference", async () => {
    const argv = [
      "runner",
      "first line\nsecond line",
      "\\@literal",
      { input: "code", field: "commit" as const },
    ];
    savedDraft = {
      id: "loaded",
      version: 1,
      createdAt: now,
      updatedAt: now,
      definition: {
        name: "Exact command",
        description: "",
        kind: "recipe",
        entrySteps: ["check"],
        steps: [
          {
            id: "check",
            title: "Check",
            instructions: "Run the exact command.",
            runner: { kind: "command", argv, timeoutSeconds: 300 },
          },
        ],
      },
    };
    renderEditor("/recipes/loaded");
    const input = await screen.findByLabelText("Command arguments");
    expect(input).toHaveValue(JSON.stringify(argv, null, 2));
    fireEvent.change(input, {
      target: { value: JSON.stringify(argv, null, 4) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(saveRecipeDraft).toHaveBeenCalledOnce());
    const runner =
      vi.mocked(saveRecipeDraft).mock.calls[0]?.[2].steps?.[0]?.runner;
    expect(runner?.kind).toBe("command");
    if (runner?.kind !== "command") throw new Error("Expected command runner");
    expect(runner.argv).toEqual(argv);
  });

  it("retains structurally invalid argv edits without dispatch or an event-handler exception", async () => {
    renderEditor();
    await screen.findByRole("heading", { name: "Step definition" });
    fireEvent.click(screen.getByRole("button", { name: /^Verify / }));
    const input = screen.getByLabelText("Command arguments");
    const invalid = '["runner", {"unexpected": true}]';
    fireEvent.change(input, { target: { value: invalid } });
    expect(input).toHaveValue(invalid);
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Publish revision" }),
    ).toBeDisabled();
    expect(createRecipeDraft).not.toHaveBeenCalled();
  });

  it("uses actionable server validation and does not publish an invalid graph", async () => {
    vi.mocked(validateRecipe).mockResolvedValue({
      valid: false,
      issues: [
        {
          path: "steps.verify.inputs.code",
          code: "unknown_output",
          message: "Bind an existing code output.",
        },
      ],
    });
    renderEditor();
    await screen.findByRole("heading", { name: "Step definition" });
    fireEvent.click(screen.getByRole("button", { name: /^Implement / }));
    fireEvent.click(screen.getByRole("button", { name: "Remove output code" }));
    fireEvent.click(screen.getByRole("button", { name: "Publish revision" }));
    const issue = await screen.findByRole("button", {
      name: /Bind an existing code output/,
    });
    expect(createRecipeDraft).not.toHaveBeenCalled();
    expect(publishRecipe).not.toHaveBeenCalled();
    fireEvent.click(issue);
    expect(screen.getByLabelText("Step name")).toHaveValue("Verify");
  });

  it("retains invalid literal edits across step selection and blocks publication", async () => {
    renderEditor();
    await screen.findByRole("heading", { name: "Step definition" });
    fireEvent.change(screen.getByLabelText("request binding source"), {
      target: { value: "literal" },
    });
    fireEvent.change(screen.getByLabelText("request literal JSON"), {
      target: { value: "{broken" },
    });
    expect(
      screen.getByRole("button", { name: "Publish revision" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /^Verify / }));
    fireEvent.click(screen.getByRole("button", { name: /^Context brief / }));
    expect(screen.getByLabelText("request literal JSON")).toHaveValue(
      "{broken",
    );
    fireEvent.change(screen.getByLabelText("request literal JSON"), {
      target: { value: '"Exact literal"' },
    });
    expect(screen.getByRole("button", { name: "Save draft" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(createRecipeDraft).toHaveBeenCalledOnce());
    expect(
      vi.mocked(createRecipeDraft).mock.calls[0]?.[0].steps?.[0]?.inputs
        ?.request?.binding,
    ).toEqual({
      source: "literal",
      value: "Exact literal",
    });
  });

  it("inserts a real pinned block revision and exposes its unbound parameters", async () => {
    const block: RecipeDraft = {
      id: "verification",
      version: 2,
      createdAt: now,
      updatedAt: now,
      definition: {
        name: "Verification block",
        description: "A pinned check",
        kind: "block",
        entrySteps: ["check"],
        parameters: {
          code: {
            type: "code_ref",
            description: "Code under test",
            required: true,
          },
        },
        steps: [
          {
            id: "check",
            title: "Check",
            instructions: "Run the declared check.",
            runner: {
              kind: "command",
              argv: ["git", "show"],
              timeoutSeconds: 300,
            },
            capabilities: ["read", "shell"],
            outputs: {
              result: { type: "object", description: "Actual result" },
            },
            routes: {
              succeeded: { terminal: "succeeded" },
              failed: { terminal: "failed" },
            },
          },
        ],
        outputs: {
          result: {
            type: "object",
            description: "Actual result",
            binding: { source: "output", stepId: "check", output: "result" },
          },
        },
      },
    };
    const revision: RecipeRevision = {
      id: "revision-4",
      definitionId: block.id,
      revision: 4,
      definition: block.definition,
      blocks: {},
      checksum: "a".repeat(64),
      publishedAt: now,
    };
    vi.mocked(fetchRecipes).mockResolvedValue({
      items: [block],
      nextCursor: null,
      highWater: 1,
    });
    vi.mocked(fetchRecipeRevisions).mockImplementation(async (id) => ({
      items: id === block.id ? [revision] : [],
      nextCursor: null,
      highWater: 1,
    }));
    renderEditor();
    await screen.findByRole("option", { name: "Verification block / block" });
    fireEvent.click(
      screen.getByRole("button", { name: "Add step / insert block" }),
    );
    fireEvent.change(screen.getByLabelText("Runner kind"), {
      target: { value: "block" },
    });
    fireEvent.change(screen.getByLabelText("Browse reusable blocks"), {
      target: { value: block.id },
    });
    await screen.findByRole("option", { name: /Revision 4/ });
    fireEvent.change(screen.getByLabelText("Block revision"), {
      target: { value: revision.id },
    });
    expect(screen.getByLabelText("Pinned block revision ID")).toHaveValue(
      revision.id,
    );
    expect(screen.getByLabelText("code binding source")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(createRecipeDraft).toHaveBeenCalledOnce());
    const composed = vi
      .mocked(createRecipeDraft)
      .mock.calls[0]?.[0].steps?.find((step) => step.runner.kind === "block");
    expect(composed?.runner).toEqual({
      kind: "block",
      revisionId: revision.id,
    });
    expect(composed?.inputs?.code?.binding).toBeNull();
    expect(createJob).not.toHaveBeenCalled();
  });
});
