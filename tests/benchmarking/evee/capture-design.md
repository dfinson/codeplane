# Copilot Traffic Capture — Design

> **Purpose:** Replace workspace-storage scraping with a mitmproxy-based HTTP
> interceptor that captures complete, untruncated Copilot Chat API traffic in
> real time. Produces one JSON file per session with exact token counts, full
> tool call arguments + results, model info, and latency — none of which the
> workspace-storage approach could reliably provide.
>
> **Status:** Design draft

---

## Problem Statement

The current `extract_vscode_agent_trace.py` scrapes VS Code's internal
`workspaceStorage/` directories after a session completes. This has
fundamental limitations:

| Limitation | Impact |
|-----------|--------|
| Tool call **arguments** are not persisted | Cannot analyze what the agent asked tools to do |
| Token counts are **estimated** (chars ÷ 4) | Inaccurate cost/efficiency metrics |
| Chat turns (user/assistant text) only in client-side `state.vscdb` | Unavailable on remote/WSL servers |
| Session isolation depends on UUID directory names | Fragile; naming conventions can change |
| No latency data per API call | Cannot measure time-to-first-token or round-trip cost |
| Model name inferred from logs, not API responses | May be wrong or missing |
| VS Code internal format changes without notice | Parser breaks silently |
| `state.vscdb` is in a different workspace storage hash | Cross-workspace correlation is brittle (as demonstrated) |

**The mitmproxy interceptor eliminates all of these** by capturing the actual
HTTP traffic between VS Code and the Copilot API.

---

## Architecture

```
┌──────────────────┐       ┌──────────────┐       ┌──────────────────────┐
│  VS Code Copilot │──────▶│  mitmproxy   │──────▶│  api.githubcopilot   │
│  (http.proxy)    │  HTTP │  (port 8080) │  TLS  │        .com          │
└──────────────────┘       │              │       └──────────────────────┘
                           │  addon:      │
                           │  copilot_    │       ┌──────────────────────┐
                           │  logger.py   │──────▶│  ~/.copilot-logs/    │
                           │              │       │  <session-id>.json   │
                           └──────────────┘       └──────────────────────┘
                                                           │
                                                           ▼
                                                  ┌──────────────────────┐
                                                  │  trace_from_capture  │
                                                  │  .py (post-process)  │
                                                  │                      │
                                                  │  → benchmark trace   │
                                                  │    JSON (same schema │
                                                  │    as current)       │
                                                  └──────────────────────┘
```

### Prerequisites — VS Code proxy settings

Before any capture session, VS Code **must** be configured to route traffic
through the local mitmproxy instance.  Add these to your User or Workspace
`settings.json`:

```jsonc
{
  "http.proxy": "http://127.0.0.1:8080",
  "http.proxyStrictSSL": false
}
```

| Setting | Why |
|---------|-----|
| `http.proxy` | Directs all VS Code HTTP(S) traffic (including Copilot) through mitmproxy on port 8080. |
| `http.proxyStrictSSL` | mitmproxy terminates TLS with its own CA; setting this to `false` prevents certificate-validation errors. |

> **Remove or disable these settings when you are not capturing.**  Leaving
> them active without mitmproxy running will break Copilot connectivity.

### Two-phase pipeline

1. **Capture** (`copilot_logger.py` — mitmproxy addon):
   Runs as a transparent proxy during agent sessions. Writes raw conversation
   JSON files to `~/.copilot-logs/`. One file per VS Code session ID.
   Zero configuration beyond the initial proxy setup.

2. **Transform** (`trace_from_capture.py` — post-processor):
   Reads a capture JSON and produces the benchmark trace JSON in the same
   schema as `extract_vscode_agent_trace.py` (schema version `0.7.0`).
   Computes all tiered metrics (T1–T4) from the captured data.

---

## Phase 1: Capture (`copilot_logger.py`)

### What we intercept

| Filter | Value |
|--------|-------|
| Hosts | `api.githubcopilot.com`, `api.individual.githubcopilot.com`, `copilot-proxy.githubusercontent.com` |
| Paths | `/chat/completions`, `/v1/chat/completions`, `/v1/engines` |
| Method | POST (requests), any (responses) |

### Session identity

The VS Code Copilot extension sends a `vscode-sessionid` header with every
request. This is a stable, unique identifier for the VS Code window session.
All turns within a single chat conversation share the same session ID.

**Important:** This is a *window-level* session ID, not a *chat-level* ID.
If the user opens multiple chats in the same window, they share the session ID.
For benchmark purposes this is acceptable because we control the test protocol
(one chat per window). For general use, the capture file is still useful — each
turn's `messages` array contains the full conversation context, so individual
chats are distinguishable by message content continuity.

Fallback chain for session ID extraction:
1. `vscode-sessionid` header
2. `x-vscode-sessionid` header
3. `copilot-integration-id` header
4. `x-request-id` header
5. `unknown_<epoch>` (last resort)

### Per-turn data captured

Each API round-trip is a "turn" in the capture file:

```json
{
  "turn_number": 1,
  "timestamp_iso": "2026-02-19T15:30:00+0000",
  "duration_ms": 4523,
  "request_model": "claude-opus-4",
  "model": "claude-opus-4-20250514",

  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "...", "tool_calls": [...]},
    {"role": "tool", "tool_call_id": "toolu_...", "content": "..."}
  ],

  "tool_definitions": [
    {"type": "function", "function": {"name": "...", "parameters": {...}}}
  ],

  "assistant_text": "reassembled from SSE chunks",
  "tool_calls": [
    {
      "id": "toolu_abc123",
      "name": "mcp_codeplane-cod_read_source",
      "arguments": {"targets": [{"path": "src/foo.py", "start_line": 1}]}
    }
  ],

  "usage": {
    "prompt_tokens": 45230,
    "completion_tokens": 1847,
    "total_tokens": 47077
  },

  "finish_reason": "tool_calls",
  "sse_chunk_count": 312,

  "message_stats": {
    "role_counts": {"system": 1, "user": 3, "assistant": 2, "tool": 4},
    "total_content_bytes": 187432,
    "tool_result_count": 4
  }
}
```

### Conversation-level aggregates (updated after every turn)

```json
{
  "session_id": "abc123-def456",
  "started_at": "2026-02-19T15:30:00+0000",
  "ended_at": "2026-02-19T15:45:23+0000",
  "models_used": ["claude-opus-4-20250514"],
  "totals": {
    "turns": 47,
    "prompt_tokens": 1234567,
    "completion_tokens": 45678,
    "tokens": 1280245,
    "tool_calls": 89,
    "duration_ms": 923456
  },
  "tool_call_frequency": {
    "mcp_codeplane-cod_read_source": 23,
    "mcp_codeplane-cod_write_source": 8,
    "run_in_terminal": 15
  },
  "turns": ["..."]
}
```

### Security

- Auth headers (`authorization`, `cookie`, `x-github-token`, `openai-api-key`)
  are replaced with `<REDACTED>` before writing to disk.
- Log directory is created with `mode=0o700` (owner-only access).
- Capture files contain full conversation content including system prompts.
  Treat them as sensitive.

---

## Phase 2: Transform (`trace_from_capture.py`)

Converts a capture JSON into the benchmark trace schema. This is where all
metric computation happens.

### Data available from capture that was previously unavailable

| Data point | Previous (scraping) | Now (capture) |
|-----------|--------------------|--------------|
| Token counts | Estimated (chars ÷ 4) | **Exact** from API `usage` |
| Tool call arguments | Not available | **Full JSON** from request body |
| User prompts | Only if `state.vscdb` accessible | **Always** available in `messages` |
| Assistant responses | Only if `state.vscdb` accessible | **Always** available (SSE reassembled) |
| Model name | Inferred from logs | **Exact** from API response |
| Per-turn latency | Not available | **Exact** `duration_ms` |
| System prompt | Not available | **Full text** in first message |
| Tool definitions sent | Not available | **Full schema** from request |
| Finish reason | Not available | **Exact** (`stop`, `tool_calls`, `length`) |
| SSE chunk count | Not available | **Exact** per turn |
| Temperature / top_p / max_tokens | Not available | **Exact** from request |

### Metric computation

All existing tiered metrics (T1–T4) are computed from the richer data:

#### Tier 1 — Core proof metrics

| Metric | Source | Notes |
|--------|--------|-------|
| `total_tool_calls` | Count `tool_calls` across all turns | Exact (was exact before too) |
| `by_kind` (native/mcp/builtin) | Classify each tool call name | Same classification logic, applied to `tool_calls[].name` |
| `native_mcp_ratio` | native ÷ mcp | Same |
| `session_duration_s` | `ended_at - started_at` | **Exact** (was tool-directory-timestamp based) |
| `tool_calls_per_second` | total ÷ duration | More accurate with exact duration |
| `thrash_shape` | Burst/streak analysis on turn timestamps | Uses per-turn `timestamp` instead of directory mtime |
| `native_terminal_calls` | Count `run_in_terminal` calls | Same |

#### Tier 2 — Convergence efficiency

| Metric | Source | Notes |
|--------|--------|-------|
| `total_pseudo_turns` | Segment by `finish_reason != "tool_calls"` | **Better**: uses actual finish_reason, not timestamp gaps |
| `tool_calls_per_pseudo_turn` | total ÷ pseudo_turns | More accurate segmentation |
| `calls_before_first_mcp` | Index of first MCP tool call | Same logic |
| `longest_native_only_streak` | Consecutive non-MCP tool calls | Same logic |

**New pseudo-turn definition:** A pseudo-turn boundary occurs when a turn's
`finish_reason` is `stop` (not `tool_calls`), meaning the agent completed a
reasoning step and is waiting for user input. This is semantically correct —
the old approach used timestamp gaps (>30s) as a heuristic.

#### Tier 3 — Cost & payload proxies

| Metric | Source | Notes |
|--------|--------|-------|
| `total_result_bytes` | Sum `tool` role message content bytes | Same concept, exact data |
| `avg_result_by_tool` | Group by tool name | Same |
| **NEW** `total_prompt_tokens` | Sum `usage.prompt_tokens` | **Exact** — replaces char-based estimate |
| **NEW** `total_completion_tokens` | Sum `usage.completion_tokens` | **Exact** |
| **NEW** `total_api_latency_ms` | Sum `duration_ms` | Total time waiting for API responses |
| **NEW** `mean_latency_per_turn_ms` | Mean `duration_ms` | Average API response time |
| **NEW** `cost_estimate_usd` | Computed from token counts × model pricing | Approximate dollar cost |

#### Tier 4 — Stability & reliability

| Metric | Source | Notes |
|--------|--------|-------|
| `error_calls` | Count tool calls with non-success status | Same, but now also have HTTP status codes |
| `error_rate` | errors ÷ total | Same |
| **NEW** `rate_limited_count` | Count HTTP 429 responses | Detecting rate limiting |
| **NEW** `finish_reason_distribution` | Count of `stop`/`tool_calls`/`length` | Detect context overflow (`length`) |

### Output schema (trace JSON)

The transform produces the same top-level structure as the current extractor,
with `schema_version` bumped to `"0.7.0"`:

```json
{
  "schema_version": "0.7.0",
  "run_metadata": {
    "extraction_timestamp": "...",
    "parser_version": "1.0.0",
    "capture_source": "mitmproxy",
    "session_id": "...",
    "models_used": ["claude-opus-4-20250514"],
    "session_start_iso": "...",
    "session_end_iso": "...",
    "git_head_sha": "..."
  },
  "summaries": {
    "total_turns": 47,
    "total_tool_calls": 89,
    "tool_calls_by_name": {},
    "tool_calls_by_kind": {},
    "tool_calls_by_namespace": {},
    "total_prompt_tokens": 1234567,
    "total_completion_tokens": 45678,
    "total_tokens": 1280245,
    "tokens_source": "api_usage",
    "codeplane_share_of_all_tool_calls": 0.67,
    "tool_result_bytes_total": 523456
  },
  "turns": [
    {
      "turn_number": 1,
      "role_summary": "user>assistant(tool_calls)",
      "prompt_tokens": 45230,
      "completion_tokens": 1847,
      "duration_ms": 4523,
      "model": "claude-opus-4-20250514",
      "finish_reason": "tool_calls",
      "tool_calls": [
        {
          "id": "toolu_abc123",
          "name": "mcp_codeplane-cod_read_source",
          "tool_kind": "mcp",
          "tool_namespace": "codeplane",
          "call_subkind": "read_file",
          "arguments": {},
          "arguments_bytes": 234
        }
      ],
      "user_message_preview": "First 500 chars of last user message...",
      "assistant_text_preview": "First 500 chars of assistant response..."
    }
  ],
  "tool_invocations": [],
  "pseudo_turns": [],
  "events": [],
  "mcp_comparison_metrics": {
    "tier1_core": {},
    "tier2_convergence": {},
    "tier3_cost_proxies": {},
    "tier4_stability": {}
  }
}
```

---

## Setup & Usage

### One-time setup

```bash
# 1. Install mitmproxy
pip install mitmproxy

# 2. Place the addon script
cp copilot_logger.py ~/.local/bin/copilot_logger.py

# 3. Add proxy to VS Code settings.json
#    (already in repo .vscode/settings.json for benchmark runs)
{
    "http.proxy": "http://127.0.0.1:8080",
    "http.proxyStrictSSL": false
}
```

### Benchmark workflow

```bash
# Terminal 1: Start proxy
mitmdump -s ~/.local/bin/copilot_logger.py

# Terminal 2: Reload VS Code, run agent session
# ... agent completes ...

# Terminal 2: Transform to trace
python3 trace_from_capture.py \
  --capture ~/.copilot-logs/<session-id>.json \
  --repo-dir /path/to/evee \
  --out tests/benchmarking/evee/results/226_mlflow_with_codeplane.json

# Terminal 1: Ctrl+C to stop proxy
```

### CLI for `trace_from_capture.py`

```
trace_from_capture.py
  --capture PATH        Capture JSON file from copilot_logger.py
  --repo-dir PATH       Repository directory (for git HEAD)
  --out PATH            Output trace JSON path
  --codeplane-prefix    CodePlane MCP tool prefix (default: "codeplane_")
  --label LABEL         Human label for the run (e.g., "with_codeplane")
```

### CLI for `copilot_logger.py` (standalone viewer)

```bash
# View one conversation
python3 copilot_logger.py --dump ~/.copilot-logs/<session>.json

# List all conversations
python3 copilot_logger.py --dump-all

# Aggregate stats across all captures
python3 copilot_logger.py --stats
```

---

## What changes vs current approach

| Aspect | Current (scraping) | New (mitmproxy) |
|--------|-------------------|-----------------|
| Data source | VS Code workspace storage files | HTTP traffic intercept |
| When data is captured | After session (post-hoc scraping) | Real-time during session |
| Token counts | Estimated (chars ÷ 4) | Exact from API |
| Tool arguments | Not available | Full JSON |
| User/assistant text | Only if `state.vscdb` reachable | Always (from request body) |
| Model name | Inferred from logs | Exact from API |
| Latency | Not available | Per-turn ms |
| Session isolation | UUID directory names | HTTP session ID header |
| Setup cost | None (passive scraping) | Proxy config + mitmproxy running |
| Dependency on VS Code internals | High (format changes break it) | None (uses public API protocol) |
| Works across VS Code versions | Fragile | Stable (API protocol is versioned) |

### What we keep from the old approach

- **Tool classification** (`_SUBKIND_BY_TOOL_NAME`, `_classify_tool_kind`,
  `_derive_tool_namespace`, `_strip_mcp_prefix`) — reused in the transformer.
- **Tiered metrics** (T1–T4 structure) — same metric definitions, better data.
- **Trace JSON schema** — same top-level structure, version bumped.
- **`extract_vscode_agent_trace.py`** — kept as fallback for sessions where
  the proxy wasn't running.

### What we retire

- `state.vscdb` parsing (no longer needed — text is in the capture).
- Workspace storage directory scanning (no longer primary source).
- Character-based token estimation (replaced by exact counts).
- Copilot Chat log file parsing (model/timing now from API responses).

---

## Refinements to the starting-point script

The provided `copilot_logger.py` is a solid foundation. Changes needed:

### 1. Tool result capture

The current script captures tool calls the agent *makes* (in assistant
responses), but doesn't explicitly tag tool *results* sent back to the API.
These are already in `messages` with `role: "tool"`, but we should index them
for efficient metric computation:

```python
# In the turn record, add:
"tool_results_in_context": [
    {
        "tool_call_id": "toolu_abc123",
        "tool_name": "inferred_from_previous_turn",
        "content_bytes": 15234,
        "content_preview": "first 200 chars..."
    }
]
```

### 2. Incremental conversation context tracking

Each API call includes the *full* conversation history in `messages`. For
metric purposes, we should track what's *new* in each turn vs what's context
repetition:

```python
"incremental": {
    "new_user_messages": 1,
    "new_tool_results": 3,
    "new_content_bytes": 45678,
    "repeated_context_bytes": 187432
}
```

### 3. Chat-level segmentation (for multi-chat windows)

Since `vscode-sessionid` is window-level, add heuristic segmentation for
multi-chat scenarios by detecting conversation restarts (when the messages
array shrinks or system prompt changes):

```python
"chat_segments": [
    {
        "segment_index": 0,
        "first_turn": 1,
        "last_turn": 47,
        "system_prompt_hash": "sha256:abc123..."
    }
]
```

For benchmarks this is unnecessary (one chat per window), but makes the
tool general-purpose.

### 4. Defensive SSE parsing

The `_parse_sse_response` function should handle:
- Incomplete chunks (connection drops mid-stream)
- Non-standard SSE formatting from different Copilot backends
- Multiple `usage` objects (take the last one, which is typically the final)

---

## File layout

```
tests/benchmarking/evee/
├── benchmark-design.md              # Existing — benchmark protocol
├── capture-design.md                # This document
├── copilot_logger.py                # mitmproxy addon (capture)
├── trace_from_capture.py            # Capture -> trace transformer
├── extract_vscode_agent_trace.py    # Legacy scraper (fallback)
└── results/
    ├── 226_mlflow_with_codeplane.json
    ├── 226_mlflow_without_codeplane.json
    └── ...
```

---

## Open questions

1. **WSL proxy routing:** VS Code Remote (WSL) may not honor `http.proxy` for
   all Copilot requests. Need to verify that the proxy intercepts traffic from
   the remote extension host, not just the local UI. If not, may need to run
   mitmproxy on the WSL side and configure at the OS/env level.

2. **Certificate trust:** mitmproxy uses its own CA. VS Code's
   `http.proxyStrictSSL: false` should bypass this, but confirm it works for
   all Copilot API endpoints (some may use certificate pinning).

3. **Performance impact:** mitmproxy adds latency to every API call. For
   benchmarking this is acceptable (both runs go through proxy), but measure
   the overhead to ensure it doesn't change agent behavior.

4. **Streaming vs non-streaming:** The script handles both SSE and JSON
   responses. Verify that Copilot always uses streaming and whether the
   non-streaming path is still needed.
