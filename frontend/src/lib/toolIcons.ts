/**
 * Tool name → icon resolution.
 *
 * Maps tool names into categories. Most categories resolve to an inlined
 * VS Code codicon; three categories (warning, todo, web) resolve to lucide-react
 * icons which are already used throughout the app.
 */

import { TriangleAlert, ListChecks, Globe, type LucideIcon } from "lucide-react";
import type { CodiconName } from "../components/ui/codicon";

export type ToolIconDef =
  | { kind: "codicon"; name: CodiconName }
  | { kind: "lucide"; icon: LucideIcon };

type ToolCategory = "terminal" | "file-read" | "file-write" | "search" | "agent" | "warning" | "todo" | "web" | "other";

const CATEGORY_MAP: Record<string, ToolCategory> = {
  // ---- terminal -----------------------------------------------------------
  bash: "terminal",
  run_in_terminal: "terminal",
  get_terminal_output: "terminal",
  Bash: "terminal",
  // ---- file-read ----------------------------------------------------------
  read_file: "file-read",
  list_dir: "file-read",
  view: "file-read",
  Read: "file-read",
  LS: "file-read",
  NotebookRead: "file-read",
  // ---- file-write ---------------------------------------------------------
  create_file: "file-write",
  replace_string_in_file: "file-write",
  multi_replace_string_in_file: "file-write",
  str_replace_based_edit_tool: "file-write",
  str_replace_editor: "file-write",
  edit: "file-write",
  Edit: "file-write",
  MultiEdit: "file-write",
  insert_edit_into_file: "file-write",
  write: "file-write",
  Write: "file-write",
  NotebookEdit: "file-write",
  // ---- search -------------------------------------------------------------
  grep_search: "search",
  semantic_search: "search",
  file_search: "search",
  glob: "search",
  grep: "search",
  Glob: "search",
  Grep: "search",
  // ---- web ----------------------------------------------------------------
  fetch_webpage: "web",
  web_search: "web",
  WebFetch: "web",
  WebSearch: "web",
  // ---- agent / sub-tasks --------------------------------------------------
  runSubagent: "agent",
  search_subagent: "agent",
  skill: "agent",
  Task: "agent",
  task: "agent",
  // ---- warnings -----------------------------------------------------------
  get_errors: "warning",
  // ---- todo ---------------------------------------------------------------
  manage_todo_list: "todo",
  TodoRead: "todo",
  TodoWrite: "todo",
};

const CATEGORY_ICON: Record<ToolCategory, ToolIconDef> = {
  terminal: { kind: "codicon", name: "terminal" },
  "file-read": { kind: "codicon", name: "file-code" },
  "file-write": { kind: "codicon", name: "edit" },
  search: { kind: "codicon", name: "search" },
  agent: { kind: "codicon", name: "robot" },
  warning: { kind: "lucide", icon: TriangleAlert },
  todo: { kind: "lucide", icon: ListChecks },
  web: { kind: "lucide", icon: Globe },
  other: { kind: "codicon", name: "circle-small-filled" },
};

export function resolveToolIcon(toolName?: string): ToolIconDef {
  if (!toolName) return { kind: "codicon", name: "circle-small-filled" };
  // Strip MCP server prefix (e.g. "github/search_code" → "search_code")
  const name = toolName.includes("/") ? toolName.split("/").pop()! : toolName;
  return CATEGORY_ICON[CATEGORY_MAP[name] ?? "other"];
}
