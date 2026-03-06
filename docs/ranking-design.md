# Recon Ranking System — Design

## 1. Foundations

### 1.1 Semantic Object

The unit of prediction is a **DefFact**: a row in the `def_facts` table with a
stable `def_uid`, `kind`, `name`, `lexical_path`, `start_line`, `end_line`,
and file reference.

DefFact kinds across languages:

| Kind | Category | What it is |
|------|----------|-----------|
| `function` | Code | Top-level function |
| `method` | Code | Method inside a class/struct/trait |
| `class` | Code | Class definition |
| `struct` | Code | Struct (Rust, Go, C#) |
| `interface` | Code | Interface (TS, Go, Java, C#) |
| `trait` | Code | Trait (Rust) |
| `enum` | Code | Enum |
| `variable` | Code | Top-level or module-level variable |
| `constant` | Code | Named constant |
| `module` | Code | Module declaration |
| `property` | Code | Property (getter/setter) |
| `pair` | Non-code | Config key-value (TOML/YAML/JSON) |
| `key` | Non-code | Config key |
| `table` | Non-code | Config section (TOML) |
| `target` | Non-code | Build target (Makefile) |
| `heading` | Non-code | Document heading (Markdown) |

### 1.2 Retrieval Signals

Five retrieval sources produce candidates. Each returns its full natural
result set — no artificial caps, no budget limits.

| Signal | Source | Granularity |
|--------|--------|-------------|
| Embedding | Dense vector similarity (bge-small-en-v1.5, 384-dim) | Per code-def or per non-code-file |
| Lexical | Tantivy full-text search | Line hits mapped to containing DefFact |
| Term match | SQL LIKE on DefFact names | Per DefFact |
| Graph | 1-hop structural walk (callees, callers, siblings) | Per DefFact |
| Symbol/Explicit | Direct symbol/path resolution from query text, seeds, and pins | Per DefFact |

Harvesters emit raw measurements only — no pre-scored evidence, no
hardcoded weights. Categorical features (`graph_edge_type`,
`symbol_source`, `import_direction`) replace numeric scores. The ranker
learns signal value from data.

### 1.3 Query Tiers

Eight OK query tiers: five isolation (each exercises a single retrieval
signal) and three combination (exercise multi-signal agreement).
Seeds and pins are separate arguments alongside the query text.

**Isolation tiers** (one signal each):

| Tier | Name | Primary signal | Seeds | Pins |
|------|------|---------------|-------|------|
| **Q_SEMANTIC** | Semantic | Embedding | none | none |
| **Q_LEXICAL** | Lexical | Full-text / Tantivy | none | none |
| **Q_IDENTIFIER** | Identifier | Term match (SQL LIKE) | none | none |
| **Q_STRUCTURAL** | Structural | Graph (1-hop walk) | 1–2 | none |
| **Q_NAVIGATIONAL** | Navigational | Explicit path/symbol resolution | none | 2–4 |

**Combination tiers** (multi-signal):

| Tier | Name | Signals combined | Seeds | Pins |
|------|------|-----------------|-------|------|
| **Q_SEM_IDENT** | Semantic + Identifier | Embedding + term match | 2–3 | none |
| **Q_IDENT_NAV** | Identifier + Navigational | Term match + explicit | 2–4 | 2–4 |
| **Q_FULL** | Full signal | All signals | 2–4 | 2–4 |

**Non-OK tiers** (up to 4 each, optional — skip if forced):

| Tier | Definition |
|------|------------|
| **UNSAT** | Query makes factually wrong assumptions about the repo |
| **BROAD** | Work spanning 15+ files in 3+ unrelated directories |
| **AMBIG** | 2+ subsystems could be the target; query doesn't specify which |

Total per task: 8 required OK + 0–12 optional non-OK = 8–20 queries.

### 1.4 Gate Taxonomy

Four labels, defined as properties of the **(query, repo) pair**:

| Label | Definition |
|-------|-----------|
| **OK** | The query maps to a specific, bounded neighborhood of semantic objects. |
| **UNSAT** | The query makes factually wrong assumptions about the repo. |
| **BROAD** | The touched set is structurally dispersed — no ranked list with a reasonable cutoff achieves acceptable precision and recall. |
| **AMBIG** | Multiple disjoint neighborhoods could plausibly be the target. |

### 1.5 Relevance Model

Graded (3-level). The ranker learns to prioritize minimum_sufficient
defs over thrash_preventing defs, so that under budget pressure the
must-see context survives the cutoff.

| Gain | Tier | Meaning |
|------|------|---------|
| 2 | `minimum_sufficient` | Human-necessary — removing this def causes task failure |
| 1 | `thrash_preventing` | Agent-necessary — removing this causes extra searches but task is still solvable |
| 0 | irrelevant | Not relevant to the task |

LambdaMART natively optimizes NDCG with graded gains.

---

## 2. Models

### 2.1 Object Ranker (Model 1)

**Goal:** Score $P(\text{relevant} \mid q, o)$ for each candidate DefFact.

**Base model:** LightGBM LambdaMART. Graded relevance gains (2/1/0).
Grouped by `(run_id, query_id)`. Only OK-labeled queries.

**Per-candidate features:**

```
# Identity
def_uid, path, kind, name, lexical_path

# Span
start_line, end_line, object_size_lines

# Path features
file_ext, parent_dir, path_depth

# Structural metadata from index
has_docstring, has_decorators, has_return_type
hub_score, is_test, signature_text, namespace
nesting_depth, has_parent_scope

# Retriever signals (None if retriever didn't find this def)
emb_score, emb_rank
term_match_count, term_total_matches
lex_hit_count
graph_edge_type, graph_seed_rank
symbol_source
import_direction
retriever_hits
```

### 2.2 Cutoff (Model 2)

**Goal:** Predict $N(q)$: how many top-ranked objects to return.

**Base model:** LightGBM regressor. Target: $N^*(q) = \arg\max_N F_1$.

**Features:** query features + repo features + score distribution
(percentiles, gaps, entropy, variance, cumulative mass) + retriever
agreement distribution.

**Training:** K-fold across all 50 repos. Out-of-fold ranker predictions
→ compute $N^*$ per query → 7,500 rows, no leakage.

### 2.3 Gate (Model 3)

**Goal:** Classify (query, repo) as OK / UNSAT / BROAD / AMBIG.

**Base model:** LightGBM multiclass. Cross-entropy objective.

**Features:** query text features + repo features + retrieval
distribution features (top score, score decay, path entropy, cluster
count, retriever agreement, seed presence).

| Gate output | Action |
|------------|--------|
| OK | Ranker + cutoff → return ranked DefFacts |
| UNSAT | Surface mismatch |
| AMBIG | Ask for disambiguation |
| BROAD | Ask for decomposition |

---

## 3. Runtime Flow

```
Query + Seeds + Pins
  │
  ├─ Embedding query (def matrix + file matrix)
  ├─ Lexical search (Tantivy)
  ├─ Term match (SQL LIKE)
  ├─ Symbol/path resolution (seeds + pins + query text)
  │
  ▼
Candidate pool (union by def_uid, per-retriever raw signals)
  │
  ├─ Graph walk (1-hop from candidates with ≥2 signals or explicit)
  │
  ▼
Full candidate pool + features
  │
  ├─ Gate classifies (query, repo)
  │     ├─ UNSAT → surface mismatch
  │     ├─ AMBIG → ask for disambiguation
  │     ├─ BROAD → ask for decomposition
  │     └─ OK ──┐
  │              ▼
  │     Ranker scores each candidate
  │              ▼
  │     Cutoff predicts N(q)
  │              ▼
  │     Return top N ranked DefFacts
  │
  ▼
Response to agent (ranked semantic spans)
```

**Output:** ranked list of DefFacts. No file tiers. No file-level
grouping. No test co-retrieval. Presentation is the consumer's concern.

---

## 4. Dataset Generation

### 4.1 Repo Selection

Three repo sets:

- **Ranker + Gate** (30 repos): train ranker and gate. 10 languages × 3 repos.
- **Cutoff** (42 repos): train cutoff with disjoint data. Medium-scale repos
  with varied codebase sizes for diverse N* distributions.
  No K-fold needed — cutoff repos are scored by the ranker trained on the
  ranker+gate set, so no leakage. Gate also trains on these repos
  (gate uses raw signals, not ranker output, so no leakage).
- **Eval** (15 repos): held-out evaluation. 10 languages + 5 extra.

All repos generate the same full query set: 8 OK queries + up to 12
non-OK queries per task. Gate uses all queries from all 72 training
repos. Cutoff uses only OK queries from cutoff repos.

**Total:** 87 repos, 2,610 tasks, ~23,000+ queries.

### 4.2 Task Generation

30 tasks per repo (10 narrow / 10 medium / 10 wide). Each task is a
natural-language description of work — no code, no diffs, no hints.

### 4.3 Data Collection Pipeline

Three phases, three agent roles:
- **Phase 1 — Pre-flight audit** (Role 1): validate tasks against the
  actual repo; correct the md file if needed.
- **Phase 2 — Solve + Reflect** (Role 2): solve each task, produce
  ground truth JSON with exploration log.
- **Phase 3 — Review** (Role 3): verify executor outputs, correct
  JSONs in-place, fill `reviewer_corrections`.
- **Phase 4 — Signal collection** (automated, re-runnable).

Role prompts live in `ranking/roles/`:

| File | Role | Job |
|------|------|-----|
| `roles/auditor.md` | Pre-flight Auditor | Verify tasks are grounded, coherent, scoped, solvable. Edit md to fix bad tasks. |
| `roles/executor.md` | Task Executor | Solve tasks, capture exploration process, produce ground truth JSON. |
| `roles/reviewer.md` | Outputs Reviewer | Verify executor outputs, simulate solving independently, correct JSONs in-place. |

#### Invocation

Each role runs as a separate agent session inside the cloned repo:

```
# Role 1: Pre-flight audit
"Read ../../repos/{set}/{repo}.md as the Pre-flight Auditor.
 Your role instructions are in ../../roles/auditor.md.
 Read both files thoroughly, then do your job."

# Role 2: Task execution
"Read ../../repos/{set}/{repo}.md as the Task Executor.
 Your role instructions are in ../../roles/executor.md.
 Read both files thoroughly, then do your job."

# Role 3: Outputs review
"Read ../../repos/{set}/{repo}.md as the Outputs Reviewer.
 Your role instructions are in ../../roles/reviewer.md.
 Read both files thoroughly, then do your job."
```

#### Ground truth JSON schema

Each task produces one file: `data/{repo_id}/ground_truth/{heading_id}.json`.

```json
{
  "task_id": "python-fastapi/N1",
  "task_complexity": "narrow",
  "task_text": "<verbatim from md>",
  "diff": "<raw git diff>",
  "solve_notes": "<1-3 sentences>",
  "exploration_log": {
    "search_sequence": [
      {"action": "...", "result": "...", "reasoning": "..."}
    ],
    "dead_ends": [
      {"explored": "...", "why_irrelevant": "..."}
    ],
    "key_decisions": [
      {"decision": "...", "alternatives": ["..."], "reasoning": "..."}
    ],
    "aha_moment": "...",
    "hindsight": "..."
  },
  "confidence": "high|medium|low",
  "minimum_sufficient_defs": [
    {"path": "...", "name": "...", "kind": "...", "start_line": 42, "reason": "edited:|read: ..."}
  ],
  "thrash_preventing_defs": [
    {"path": "...", "name": "...", "kind": "...", "start_line": 87, "reason": "read: ..."}
  ],
  "tier_difference_reasoning": "<why the two tiers differ or are identical>",
  "excluded_defs": [
    {"path": "...", "name": "...", "kind": "...", "reason": "..."}
  ],
  "queries": [
    {"query_type": "Q_SEMANTIC", "query_text": "...", "seeds": [], "pins": [], "justification": "..."}
  ],
  "reviewer_corrections": "<filled by Role 3: corrections summary or 'No corrections required'>"
}
```

Field details, query type rules, seed/pin rules, and validation
checklist are in `roles/executor.md` (the canonical reference).

**Post-processing** (automated):

1. For each def in `minimum_sufficient_defs` and `thrash_preventing_defs`:
   look up `(path, name, kind)` in codeplane index → resolve `def_uid`.
2. If no match: flag for review (should be <2%).
3. Write `touched_objects.jsonl` with `tier` field (`minimum` or `thrash_preventing`).
   The ranker trains on the union (both tiers = relevant).
4. Write `audit/` with `diff`, `justification`, `excluded_defs`,
   `confidence`, `solve_notes` for third-agent auditing.
5. Assemble `runs.jsonl`, `queries.jsonl`.

#### Phase 3: Retrieval Signal Collection (Re-runnable)

For each query in ground truth:

```
recon_raw_signals(query=query_text, seeds=seeds, pins=pins)
```

Returns candidate pool with per-retriever raw signals. Seeds/pins
affect the pool: seeded symbols enter via the explicit harvester
(`symbol_source="agent_seed"`), pinned paths inject all defs from
those files (`symbol_source="pin"`).

Join with ground truth → `label_relevant` per candidate (binary).

---

## 5. Training Data Schemas

### 5.1 `runs`

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | str | Unique task run identifier |
| `repo_id` | str | Repository identifier |
| `task_id` | str | Task identifier |
| `task_text` | str | Full task description |

### 5.2 `touched_objects`

One row per relevant def per task. Absence = irrelevant.
Two tiers: `minimum` (human-necessary) and `thrash_preventing`
(agent-necessary). The ranker trains on the union of both.

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | str | Task run identifier |
| `def_uid` | str | DefFact stable identity |
| `path` | str | File path |
| `kind` | str | DefFact kind |
| `name` | str | DefFact name |
| `start_line` | int | Span start |
| `end_line` | int | Span end |
| `tier` | str | `minimum` or `thrash_preventing` |
| `name` | str | DefFact name |
| `start_line` | int | Span start |
| `end_line` | int | Span end |

### 5.3 `queries`

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | str | Task run identifier |
| `query_id` | str | Unique query identifier |
| `query_text` | str | Full query text |
| `query_type` | str | Q_SEMANTIC / Q_LEXICAL / Q_IDENTIFIER / Q_STRUCTURAL / Q_NAVIGATIONAL / Q_SEM_IDENT / Q_IDENT_NAV / Q_FULL / UNSAT / BROAD / AMBIG |
| `seeds` | list[str] | Symbol names passed as seeds |
| `pins` | list[str] | File paths passed as pins |
| `label_gate` | str | OK / UNSAT / BROAD / AMBIG |

Non-OK queries live in a separate per-repo file:
`data/{repo_id}/non_ok_queries.json`. They are merged into `queries`
during post-processing with `label_gate` set to the query type.

### 5.4 `candidates_rank`

One group per `(run_id, query_id)`. Per-candidate features from
`recon_raw_signals` output + binary relevance label.

### 5.5 `queries_cutoff`

One row per OK query from CUTOFF repos only. Score distribution
features from ranker output (ranker trained on ranker+gate repos)
+ $N^*$ target.

### 5.6 `queries_gate`

One row per query (all types). Retrieval distribution features +
gate label.

---

## 6. Training Procedures

### 6.1 Ranker

1. Filter to OK-labeled queries.
2. Train LightGBM LambdaMART grouped by `(run_id, query_id)`.
3. Graded relevance: 2 (minimum_sufficient), 1 (thrash_preventing), 0.

### 6.2 Cutoff (disjoint repo split)

1. Train ranker on 30 ranker+gate repos.
2. Score all 31 cutoff repos with the trained ranker.
3. Compute $N^*$ per cutoff query.
4. ~7,400 rows, zero leakage (ranker never saw cutoff data).
5. Train cutoff regressor.

### 6.3 Gate

1. All query types (OK + UNSAT + BROAD + AMBIG) from ALL 72 training
   repos (30 ranker+gate + 31 cutoff).
2. Retrieval distribution features from candidate pools.
3. LightGBM multiclass, cross-entropy.

### 6.4 Shipment Sequence

1. Data collection (30 ranker+gate repos, 31 cutoff repos, 15 eval repos)
2. Gate training (all 72 training repos) → gate ships
3. Ranker training (30 ranker+gate repos) → validate NDCG on eval set
4. Cutoff training (31 cutoff repos scored by trained ranker)
5. Full pipeline ships (gate + ranker + cutoff)

---

## 7. Evaluation

Uses the EVEE benchmarking framework.

**Metrics** (reported per query type):

| Metric | Granularity |
|--------|-------------|
| NDCG of ranked list | Per query |
| F1 / precision / recall of returned set | Per query |
| Gate accuracy + confusion matrix | Per query / aggregate |
| Empty-result rate | Aggregate |
| Returned-set size | Aggregate |

**Comparison:** head-to-head with heuristic recon baseline on F1 delta,
precision delta, recall delta, and latency.

**Project structure:**

```
src/codeplane/ranking/          # Runtime inference (ships with codeplane)
├── ranker.py, cutoff.py, gate.py, features.py, models.py
└── data/                       # Serialized .lgbm model artifacts

ranking/                        # Training pipeline (separate project)
├── roles/
│   ├── auditor.md              # Role 1: pre-flight audit prompt
│   ├── executor.md             # Role 2: task execution prompt
│   └── reviewer.md             # Role 3: outputs review prompt
├── repos/{set}/{repo}.md       # Task definitions per repo
├── src/cpl_ranking/
│   ├── collector.py, collect_signals.py
│   ├── train_ranker.py, train_cutoff.py, train_gate.py
│   └── schema.py
└── data/{repo_id}/             # Ground truth + signals (tracked in git)

benchmarking/                   # EVEE evaluation
├── datasets/ranking_gt.py
├── models/recon_ranking.py
├── metrics/ranking.py, gate.py
└── experiments/recon_ranking.yaml
```

---

## 8. Design Invariants

Every numeric boundary is either learned from data (ranker scores,
cutoff N, gate boundaries), a system configuration parameter
(rendering budget), or computed empirically per query ($N^*$).

No artificial caps in retrievers. No hardcoded evidence scores. No
arbitrary constants in model definitions, feature computations, or
training procedures. Seeds and pins inject candidates but do not boost
scores — the ranker decides their value. Graph seeds = candidates
found by ≥2 retrievers or explicitly mentioned, no scoring formula.

Non-code files use file-level embeddings only. Their synthetic defs
enter the candidate pool via file-embedding expansion. The ranker
treats them identically to code defs.
