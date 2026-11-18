import { Tooltip } from "../ui/tooltip";
import { type AnalyticsTools } from "../../api/client";
import { formatDuration } from "./helpers";

// ---------------------------------------------------------------------------
// Tool Health
// ---------------------------------------------------------------------------

export const toolDescriptions: Record<string, string> = {
  // Shell
  bash: "Shell command execution (cd, git, make, etc.)",
  Bash: "Shell command execution (Claude)",
  sql: "Execute a SQL query",
  read_bash: "Read output from a running shell session",
  write_bash: "Send input to a running shell session",
  stop_bash: "Terminate a running shell session",
  run_in_terminal: "Run a command in a persistent terminal",
  get_terminal_output: "Read output from a background terminal",
  // File read
  view: "Read file contents at a path",
  read_file: "Read file contents at a path",
  Read: "Read file contents (Claude)",
  TodoRead: "Read the agent's todo list (Claude)",
  NotebookRead: "Read a Jupyter notebook (Claude)",
  // File write
  edit: "Replace text in an existing file",
  Edit: "Replace text in an existing file (Claude)",
  MultiEdit: "Apply multiple edits to a file in one call (Claude)",
  Write: "Write content to a file (Claude)",
  create: "Create a new file with content",
  replace_string_in_file: "Find and replace text in a file",
  multi_replace_string_in_file: "Batch find-and-replace across files",
  apply_patch: "Apply a unified diff patch to files",
  TodoWrite: "Update the agent's todo list (Claude)",
  NotebookEdit: "Edit a Jupyter notebook (Claude)",
  // File search
  grep: "Search file contents by pattern",
  Grep: "Search file contents by pattern (Claude)",
  grep_search: "Search file contents by pattern",
  glob: "Find files matching a glob pattern",
  Glob: "Find files matching a glob pattern (Claude)",
  LS: "List directory contents (Claude)",
  rg: "Ripgrep — fast regex search across files",
  semantic_search: "Natural language search across the codebase",
  file_search: "Find files by glob pattern",
  list_dir: "List directory contents",
  ToolSearch: "Search available tools by name (Claude)",
  tool_search_tool_regex: "Search available tools by regex",
  vscode_listCodeUsages: "Find all references to a symbol",
  ListMcpResources: "List available MCP resources (Claude)",
  ListMcpResourceTemplates: "List MCP resource templates (Claude)",
  // Browser
  web_fetch: "Fetch a URL and return its contents",
  WebFetch: "Fetch a URL and return its contents (Claude)",
  WebSearch: "Search the web (Claude)",
  fetch_webpage: "Fetch and render a web page",
  ReadMcpResource: "Read an MCP resource by URI (Claude)",
  // Agent delegation
  task: "Delegate a subtask to another agent",
  Task: "Delegate a subtask to another agent (Claude)",
  read_agent: "Read output from a running sub-agent",
  skill: "Invoke a registered skill by name",
  search_subagent: "Launch a fast codebase exploration agent",
  runSubagent: "Launch a subagent for a complex task",
  // System / bookkeeping
  report_intent: "Declare the agent's intended next action",
  store_memory: "Save a note to persistent memory",
  memory: "Read/write persistent memory across sessions",
  manage_todo_list: "Track tasks in a structured todo list",
  Think: "Internal reasoning step — no external action (Claude)",
  Computer: "Desktop automation tool (Claude)",
  get_changed_files: "List files changed in git",
};

export function ToolHealth({ tools }: { tools: AnalyticsTools["tools"] }) {
  if (!tools.length) return <p className="text-muted-foreground text-sm">No tool data yet.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-muted-foreground border-b border-border">
            <th className="text-left py-1.5 px-2 font-medium">Tool</th>
            <th className="text-right py-1.5 px-2 font-medium">
              <Tooltip content="Total number of times this tool was called"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Calls</span></Tooltip>
            </th>
            <th className="text-right py-1.5 px-2 font-medium">
              <Tooltip content="Number of calls with a reported failure; unknown outcomes are excluded"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Errors</span></Tooltip>
            </th>
            <th className="text-right py-1.5 px-2 font-medium">
              <Tooltip content="Percentage of calls with known outcomes that completed without errors"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Success</span></Tooltip>
            </th>
            <th className="text-right py-1.5 px-2 font-medium">
              <Tooltip content="Average time per call"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Avg Time</span></Tooltip>
            </th>
            <th className="text-right py-1.5 px-2 font-medium">
              <Tooltip content="Median (50th percentile) latency"><span className="cursor-help border-b border-dotted border-muted-foreground/50">p50</span></Tooltip>
            </th>
            <th className="text-right py-1.5 px-2 font-medium">
              <Tooltip content="95th percentile latency"><span className="cursor-help border-b border-dotted border-muted-foreground/50">p95</span></Tooltip>
            </th>
            <th className="text-right py-1.5 px-2 font-medium">
              <Tooltip content="Total cumulative time spent in this tool"><span className="cursor-help border-b border-dotted border-muted-foreground/50">Total Time</span></Tooltip>
            </th>
          </tr>
        </thead>
        <tbody>
          {tools.map((t, i) => {
            const failures = t.failureCount;
            const knownCount = t.count - t.unknownCount;
            const successRate = knownCount > 0 ? ((knownCount - failures) / knownCount * 100) : null;
            const desc = toolDescriptions[t.name];
            return (
              <tr key={i} className="border-b border-border/50 hover:bg-accent/30">
                <td className="py-1.5 px-2 font-mono">
                  {desc ? <Tooltip content={desc}><span className="cursor-help border-b border-dotted border-muted-foreground/50">{t.name}</span></Tooltip> : t.name}
                </td>
                <td className="text-right py-1.5 px-2">
                  {t.count}
                  {t.unknownCount > 0 && <span className="text-muted-foreground"> ({t.unknownCount} unknown)</span>}
                </td>
                <td className="text-right py-1.5 px-2">
                  {knownCount === 0
                    ? <span className="text-muted-foreground">Unknown</span>
                    : failures ? <span className="text-red-400">{failures}</span> : <span className="text-muted-foreground">0</span>}
                </td>
                <td className="text-right py-1.5 px-2">
                  <span className={successRate === null ? "text-muted-foreground" : successRate >= 95 ? "text-green-400" : successRate >= 80 ? "text-yellow-400" : "text-red-400"}>
                    {successRate === null ? "Unknown" : `${successRate.toFixed(0)}%`}
                  </span>
                </td>
                <td className="text-right py-1.5 px-2">{formatDuration(Number(t.avgDurationMs) || 0)}</td>
                <td className="text-right py-1.5 px-2">{formatDuration(Number(t.p50DurationMs) || 0)}</td>
                <td className="text-right py-1.5 px-2">{formatDuration(Number(t.p95DurationMs) || 0)}</td>
                <td className="text-right py-1.5 px-2">{formatDuration(Number(t.totalDurationMs) || 0)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
