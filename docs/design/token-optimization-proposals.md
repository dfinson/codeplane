# Token Optimization: Design Proposals & Research

**Branch**: `bench/evee-benchmarking-setup`  
**Date**: 2025-07-14  
**Context**: evee #260 benchmark — CP uses 2.4× more total prompt tokens than native tooling  

---

## 1. Root Cause #2: Context Growth Mechanisms

### Research Findings

VS Code Copilot has **four major context-management mechanisms** that interact
with MCP tool responses:

#### A. Conversation History Summarization

**Setting**: `github.copilot.chat.summarizeAgentConversationHistory.enabled` (default: `true`)  
**Behavior**: When the context window fills up, VS Code *automatically summarizes*
the conversation history to free up space. The summarized history replaces
older turn-by-turn messages with a compressed representation.

**Implication for CodePlane**: Every verbose tool response gets fed into the
summarization pipeline. Structured JSON (CodePlane's format) summarizes
*differently* than prose. Long JSON payloads may get summarized into short
captions — losing actionable data the model might still need. Conversely,
if we make responses more compact and self-contained, the summaries
preserve more of the useful signal.

**Actionable**:
- Making responses compact upfront means the *pre-summarization* turns
  carry more information density per token.
- Summaries of compact responses lose less signal.
- The summarization setting is on by default — we cannot disable it, so we
  should optimize *for* it.

#### B. Priority-Based Prompt Pruning (`@vscode/prompt-tsx`)

VS Code uses the `@vscode/prompt-tsx` library to build its LLM prompts.
Each piece of context has a **priority** number (higher = more important).
When the combined prompt exceeds the context window, lowest-priority
content is pruned first.

The key features:
- `priority` on every message element — controls pruning order
- `flexGrow` — elements fill remaining budget dynamically
- `TokenLimit` — hard caps on sections
- `TextChunk(breakOn=" ")` — partial inclusion of large text
- `useKeepWith()` — linked elements (tool request + response) pruned together

**Implication for CodePlane**: Tool results are placed into the prompt
tree with a priority score. If CodePlane responses are large, they will
be *pruned earlier* under context pressure. Smaller responses survive
longer in the context window, meaning the model can reference older
tool results for more turns.

**This is the key insight**: compact responses don't just save tokens
on the turn they appear — they *persist longer in context*, improving
multi-turn coherence.

#### C. Tool Result Token Budgets (VS Code Extension API only)

The VS Code extension API passes `tokenizationOptions: { tokenBudget, countTokens }`
to tool invocations. This tells native tools "you have N tokens of budget —
adapt your output to fit."

**Critical limitation**: This is a VS Code extension API feature, **not an
MCP protocol feature**. MCP's `tools/call` only has `name` and `arguments`.
There is no way for VS Code to communicate a token budget to MCP tools.

**Implication**: CodePlane cannot currently receive token budget signals
from VS Code. We must self-regulate response sizes. Possible approaches:

1. **Heuristic self-regulation**: Add a `response_budget` parameter to
   tools (e.g., `compact`, `standard`, `full`). Teach agents via
   AGENTS.md to use `compact` in later turns.
2. **Session-aware throttling**: Track cumulative response bytes per
   session in middleware. Automatically increase compression after
   a threshold (e.g., after 20 tool calls, switch to compact mode).
3. **MCP protocol proposal**: Propose adding `tokenBudget` to the
   MCP `tools/call` params. This would let hosts communicate context
   pressure to all MCP servers. (Longer-term, requires spec change.)

#### D. Virtual Tools

**Setting**: `github.copilot.chat.virtualTools.threshold` (default: 128)  
**Behavior**: When tool count exceeds the threshold, tools are grouped
into "virtual tools" that the model activates on-demand. This reduces
the system prompt size by not listing all tool schemas upfront.

**Current workspace setting**: `128` (default, no override)  
**CodePlane tool count**: ~20 tools — well below the threshold.  

**Implication**: Not relevant for CodePlane right now. Would only matter
if we or other MCP servers push total tool count above 128.

#### E. Max Requests Limit

**Setting**: `chat.agent.maxRequests` (default: 25)  
**Behavior**: Hard cap on tool calls per session.

**In evee benchmark**: CP used 46 tool calls, native used 48 — both
exceed the default limit. Users must have increased this setting.

### Recommendations for Root Cause #2

| Priority | Action | Effort | Token Savings |
|----------|--------|--------|---------------|
| P0 | Compact response formats (see §2) | Medium | 30-50% per response |
| P1 | Session-aware auto-compaction | Medium | 20-40% in later turns |
| P2 | Verbosity parameter on all tools | Low | User-controlled |
| P3 | MCP protocol proposal for token budget | High | Long-term |

---

## 2. Compact Response Format Designs

### 2a. Search Response Compaction

#### Current Format (per result)
```json
{
  "hit_id": "lexical:src/codeplane/mcp/tools/index.py:49:0",
  "path": "src/codeplane/mcp/tools/index.py",
  "span": {
    "start_line": 49,
    "start_col": 0,
    "end_line": 49,
    "end_col": 0
  },
  "kind": "def",
  "symbol_id": "_serialize_tree",
  "preview_line": "(nodes: list[Any], *, include_line_counts: bool = True)",
  "symbol": {
    "name": "_serialize_tree",
    "kind": "function",
    "qualified_name": null
  }
}
```
~330 bytes per result. 20 results = ~6,600 bytes + envelope.

#### Proposed Compact Format
```json
{
  "id": "lexical:src/codeplane/mcp/tools/index.py:49",
  "path": "src/codeplane/mcp/tools/index.py",
  "line": 49,
  "kind": "def",
  "name": "_serialize_tree",
  "preview": "(nodes: list[Any], *, include_line_counts: bool = True)"
}
```
~190 bytes per result. 20 results = ~3,800 bytes.

#### Changes

| Field | Current | Proposed | Rationale |
|-------|---------|----------|-----------|
| `hit_id` | `"lexical:path:line:col"` | `"id"`: `"lexical:path:line"` | Shorter key, drop col (always 0 for lexical) |
| `span` | 4-field nested object | `"line": N` | For lexical/symbol: start_line is sufficient. end_line = start_line, cols are always 0. For structural modes, keep `enclosing_span`. |
| `symbol_id` | Redundant with `symbol.name` | Drop | Merge into top-level `name` |
| `symbol` | Nested object | Flatten | `name` + `kind` at top level (`kind` already exists) |
| `preview_line` | Long key | `preview` | Shorter key |
| `qualified_name` | Often null | Omit when null | Already common practice |

**Estimated savings**: ~42% per result = ~2,800 bytes per 20-result search.

#### Edge Case: Structural Enrichment

When `enrichment` is `function` or `class`, keep `enclosing_span` as-is
(it's needed for `read_source` targeting). But still flatten the base fields.

### 2b. Semantic Diff Response Compaction

#### Current Format (standard verbosity, per change)
```json
{
  "path": "src/foo.py",
  "kind": "function",
  "name": "handle_request",
  "change": "body_changed",
  "structural_severity": "moderate",
  "behavior_change_risk": "medium",
  "classification_confidence": 0.85,
  "qualified_name": "module.handle_request",
  "entity_id": "function:module.handle_request",
  "start_line": 42,
  "start_col": 4,
  "end_line": 78,
  "end_col": 0,
  "lines_changed": 12,
  "delta_tags": ["logic_change"],
  "impact": {
    "reference_count": 5,
    "ref_tiers": {"direct": 3, "transitive": 2},
    "referencing_files": ["src/a.py", "src/b.py"],
    "importing_files": ["src/c.py"],
    "affected_test_files": ["tests/test_foo.py"],
    "confidence": "high",
    "visibility": "public",
    "is_static": false
  }
}
```
~680 bytes per change. A diff with 10 changes = ~6,800 bytes + scope + envelope.

#### Proposed Compact Format (new "compact" verbosity level)
```json
{
  "path": "src/foo.py",
  "kind": "function",
  "name": "handle_request",
  "change": "body_changed",
  "risk": "medium",
  "lines": 12,
  "span": [42, 78],
  "refs": 5,
  "tests": ["tests/test_foo.py"]
}
```
~210 bytes per change. 10 changes = ~2,100 bytes.

#### Changes

| Field | Current | Proposed (compact) | Rationale |
|-------|---------|-------------------|-----------|
| `structural_severity` | Full word | Drop in compact | `risk` (behavior_change_risk) is the actionable signal |
| `behavior_change_risk` | Long key | `risk` | Shorter key name |
| `classification_confidence` | Float | Drop in compact | Agent doesn't act on this |
| `qualified_name` | Often same as name | Drop in compact | Path + name is unique enough |
| `entity_id` | Formatted string | Drop in compact | Agent can reconstruct |
| `start_line`/`end_line` | 4 separate fields | `span: [start, end]` | Array is 60% shorter |
| `start_col`/`end_col` | Usually 0/4 | Drop in compact | Not actionable |
| `lines_changed` | Full key | `lines` | Shorter |
| `delta_tags` | Array of strings | Drop in compact | ML-internal signal |
| `impact` | Nested object | Flatten essentials: `refs` + `tests` | Only ref count and test files are actionable |
| `ref_tiers` | Nested breakdown | Drop in compact | Agent doesn't distinguish direct vs transitive |
| `referencing_files` | Full list | Drop in compact | Agent uses `affected_test_files` for testing |
| `importing_files` | Full list | Drop in compact | Not actionable in diff review |
| `confidence`/`visibility`/`is_static` | Impact details | Drop in compact | Not actionable |
| `risk_basis` | Explanation string | Drop in compact | Agent acts on risk level, not explanation |

**Implementation**: Add `verbosity: "compact"` level to `_change_to_dict` alongside
existing `full`/`standard`/`minimal`.

**Estimated savings**: ~69% per change = ~4,700 bytes per 10-change diff.

#### Scope/Envelope Compaction

The `scope` block in `_result_to_dict` serializes via `asdict()` with null filtering.
Compact mode should also compact scope:

```json
// Current scope
{"base_sha": "abc123", "target_sha": "def456", "worktree_dirty": true,
 "mode": "worktree_vs_head", "entity_id_scheme": "kind:qualified_name",
 "files_parsed": 410, "files_no_grammar": [], "files_parse_failed": [],
 "languages_analyzed": ["python"]}

// Compact scope
{"base": "abc123", "target": "def456", "dirty": true, "files": 410}
```

### 2c. Map Repo Response Compaction

#### Current Format Issues

`map_repo` already has a `verbosity` parameter (`full`/`standard`/`minimal`).
However, even `minimal` still serializes `languages` as a list of objects.
The `full`/`standard` modes serialize the directory tree with per-file entries.

#### Proposed Changes

1. **Compact tree serialization**: Replace per-file objects with path-only arrays
   for `standard` mode:

```json
// Current (standard, no line counts)
{"path": "src/codeplane/mcp/tools/index.py", "is_dir": false}

// Compact: just the path string
"src/codeplane/mcp/tools/index.py"
```

Directories still need children, so:
```json
// Current
{"path": "src/codeplane/mcp/tools", "is_dir": true, "file_count": 12,
 "children": [{"path": "...", "is_dir": false}, ...]}

// Compact: only directories are objects; files are strings
{"d": "src/codeplane/mcp/tools", "n": 12,
 "c": ["__init__.py", "base.py", "diff.py", ...]}
```

**Savings per file entry**: ~45 bytes → ~20 bytes (using relative names).
For 100-file tree: ~2,500 bytes saved.

2. **Compact dependencies**: Currently serializes full import list.
   Compact mode: just module names + counts.

3. **Compact test_layout**: Currently includes test file paths and
   test function names. Compact mode: counts + top-level paths only.

### 2d. Delivery Envelope Compaction

The `delivery` envelope wraps every tool response with:
```json
{"resource_kind": "search_hits", "delivery": "inline",
 "inline_budget_bytes_used": 608, "inline_budget_bytes_limit": 8000}
```

#### Proposed Changes

| Field | Current | Proposed | Savings |
|-------|---------|----------|---------|
| `resource_kind` | Full key | `kind` | 9 chars |
| `inline_budget_bytes_used` | Full key | `budget_used` | 15 chars |
| `inline_budget_bytes_limit` | Full key | `budget_limit` | 14 chars |
| `delivery` | Always "inline" for inline | Drop when inline | ~20 chars |

Modest savings per call (~60 bytes) but compounds over 46 calls.

### 2e. Implementation Strategy

Do NOT change defaults for existing `verbosity` levels — that would break
consumers. Instead:

1. Add a new `compact` level to tools that have `verbosity` (semantic_diff,
   map_repo).
2. For search: compact the format unconditionally (search never returns
   source text, so the compact format is strictly better).
3. Add a middleware-level `auto_compact` mode that activates after N calls
   in a session (same pattern as `_WORKFLOW_HINT_INTERVAL`).
4. Update AGENTS.md to recommend `compact` for non-diagnostic use.

---

## 3. Multi-Query Search: Red/Blue Team Analysis

### Proposal

Allow multiple search queries in a single `search` call:
```json
{
  "queries": [
    {"query": "handle_request", "mode": "definitions"},
    {"query": "handle_request", "mode": "references"},
    {"query": "import handle_request", "mode": "lexical"}
  ],
  "limit": 20
}
```

Alternatively, keep current single-query interface but add a `batch_search`
tool.

### Blue Team (Pro)

| Benefit | Impact |
|---------|--------|
| **Round-trip savings** | 1 call vs 3 = 2 fewer round-trips. At ~200ms MCP overhead each, saves ~400ms. |
| **Token savings** | 3 separate envelopes + 3 agentic_hints → 1 envelope + 1 hint. ~500 tokens saved. |
| **Common pattern** | Agents frequently search def + refs for the same symbol. Batching makes this atomic. |
| **Reduced gate pressure** | The `pure_search_chain` gate fires when 5 of 7 calls are searches. Batching reduces this. |
| **Simpler agent logic** | Agent writes one tool call instead of three sequential ones. |

### Red Team (Con)

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Response size explosion** | HIGH | 3×20 = 60 results in one response could be 12K+. Must cap total results across queries. |
| **Error semantics** | MEDIUM | If query 2 of 3 fails, do we return partial results? Must define partial failure behavior. |
| **Budget tracking** | MEDIUM | How to charge: 1 search call or 3? Budget becomes complex. |
| **Pagination complexity** | HIGH | Each sub-query might paginate independently. Cursor management for 3 parallel paginations is very complex. |
| **JSON schema complexity** | MEDIUM | The `queries` array makes the input schema significantly larger, adding ~200 tokens to tool descriptions. |
| **Gate bypass** | LOW | Agents could batch-search to avoid the search chain gate. The gate exists for a reason. |
| **Agentic hint targeting** | LOW | Per-query hints ("no results, try lexical?") can't be easily scoped when batched. |
| **Model confusion** | MEDIUM | Models are trained on simple tool interfaces. Complex array-of-queries schemas may increase parameter errors. |

### Verdict

**Recommend AGAINST multi-query search** for now.

The pagination and error-handling complexity (Red Team: HIGH) outweighs the
round-trip savings (Blue Team: ~400ms, ~500 tokens). The savings are real but
modest compared to the implementation cost and schema complexity.

**Better alternatives**:
1. **Fix the search chain gate threshold** — currently fires at 5/7. Could
   increase to 7/10 or make it awareness-only (hint without blocking).
2. **Add `search_def_and_refs` convenience tool** — a purpose-built tool
   that searches definitions AND references for a single symbol. Simpler
   schema, covers the most common batch pattern, no pagination ambiguity.
3. **Compact response formats (§2)** — reduce per-search token cost by 42%,
   making more sequential searches affordable.

---

## 4. Missed Items Check

Reviewing all 7 recommendations from the benchmark analysis:

| # | Recommendation | Status |
|---|---------------|--------|
| 1 | Remove `verification_context` | ✅ Implemented |
| 2 | Throttle `suggested_workflow` | ✅ Implemented (interval=3) |
| 3 | Add batch guidance to AGENTS.md | ✅ Implemented |
| 4 | Merge `inline_summary`/`summary` | ✅ Implemented |
| 5 | Make tool schemas visible in errors | ✅ Implemented |
| 6 | Compact response formats | 📋 Designed (this doc §2) |
| 7 | Reduce system prompt duplication | ✅ Implemented (copilot-instructions.md deduped) |

### Additional Items Not In Original 7

1. **`agentic_hint` in every response** (~50-200 chars each)
   - Rejoinders like `"REJOINDER: search(), read_source, and read_scaffold replace grep/rg/find/cat/head/tail/sed/wc."` appear in EVERY response.
   - Already addressed by throttle mechanism, but the rejoinders themselves
     could be shortened. Currently ~80 chars. Could be ~40 chars:
     `"Use CodePlane search/read_source/read_scaffold, not shell commands."`
   
2. **`scope_usage` in responses** (when scope_id provided)
   - Returns budget counters on every response. Agent rarely acts on these.
   - Consider omitting when budgets are healthy (>50% remaining).

3. **`query_time_ms: 0` in search** — always 0, dead field. Remove.

4. **`fallback_reason`** — only present when query syntax invalid.
   Good as-is (conditional).

5. **Non-structural changes in semantic_diff** — serialized via `asdict()`
   which includes every field. Could use the same tiered serialization
   as structural changes.

6. **System prompt: tool descriptions** — The `description` strings on
   MCP tools include examples and detailed parameter docs. These get
   injected into every turn's prompt. Shorter descriptions = fewer
   tokens per turn × 46 turns = significant savings. Worth auditing
   each tool's description length.

---

## 5. Priority Roadmap

### Already Done (This Session)
- [x] `verification_context` removed (~16K chars/session saved)
- [x] `suggested_workflow` throttled (~67% reduction)
- [x] `inline_summary` → `summary` merged
- [x] Validation errors point at inline schema (eliminate describe round-trip)
- [x] AGENTS.md batch guidance added
- [x] copilot-instructions.md deduped (~135 lines removed from system prompt)

### Next Sprint (Compact Formats)
1. Search response compaction (unconditional) — ~42% smaller
2. Add `compact` verbosity to semantic_diff — ~69% smaller  
3. Add `compact` verbosity to map_repo tree — ~45% smaller per file
4. Shorten delivery envelope field names — ~60 bytes/call
5. Remove `query_time_ms` dead field
6. Shorten rejoinder text

### Future
- Session-aware auto-compaction middleware
- `search_def_and_refs` convenience tool
- Tool description audit for system prompt compression
- MCP protocol proposal for token budget passthrough
