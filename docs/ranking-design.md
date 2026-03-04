# Recon Ranking System — Complete Design

## 1. Foundations

### 1.1 Semantic Object

The unit of prediction is a **DefFact**: a row in the `def_facts` table with a
stable `def_uid`, `kind`, `name`, `qualified_name`, `start_line`, `end_line`,
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

Five retrieval sources produce candidates:

| Signal | Source | Granularity |
|--------|--------|-------------|
| Embedding | Dense vector similarity (bge-small-en-v1.5, 384-dim) | Per code-def or per non-code-file |
| Lexical | Tantivy full-text search | Line hits mapped to containing DefFact |
| Term match | SQL LIKE on DefFact names with IDF weighting | Per DefFact |
| Graph | 1-hop structural walk (callees, callers, siblings) | Per DefFact |
| Symbol/Explicit | Direct symbol/path resolution from query text | Per DefFact |

### 1.3 Gate Taxonomy

Four labels, defined as properties of the **(query, repo) pair**:

| Label | Definition |
|-------|-----------|
| **OK** | The query maps to a specific, bounded neighborhood of semantic objects. A developer could start working without clarifying questions. |
| **UNSAT** | The query makes factually wrong assumptions about the repo's architecture, components, or behavior. No meaningful object set satisfies it. |
| **BROAD** | The task genuinely requires coordinated changes across many modules/subsystems. The touched set is structurally dispersed — no single ranked list with a reasonable cutoff achieves acceptable precision and recall simultaneously. |
| **AMBIG** | The query is semantically underspecified: multiple disjoint neighborhoods could plausibly be the target. A developer would ask "which one?" before starting. |

---

## 2. Embedding Pipeline

### 2.1 Strategy: Defs for Code, Files for Non-Code

Two disjoint embedding indices, no overlap:

**Per-DefFact index** — code kinds only (`function`, `method`, `class`, `struct`,
`interface`, `trait`, `enum`, `property`, `constant`, `variable`, `module`):

Each code DefFact gets its own embedding vector. The embedded text is an
anglicized per-def scaffold:

```
DEF_SCAFFOLD
module <file path phrase>
<kind> <anglicized name>(<compact signature>)
describes <first sentence of docstring>
parent <parent class/module name if applicable>
calls <callees within this def's body>
decorated <decorator names>
mentions <string literals within this def>
```

Per-def scaffolds are tiny: median ~48 chars (~14 tokens), p95 ~151 chars.
99.9% fall under 500 chars. With adaptive batching (batch size ×4 for short
texts), ONNX attention cost is dominated by sequence length, not count.

Measured performance (codeplane repo, bge-small-en-v1.5, CPU):

| Metric | Value |
|--------|-------|
| Code defs in this repo | ~8,258 |
| Index time | ~10s |
| Query time (brute-force matmul) | ~11ms |
| Storage | ~6 MB |

**Per-file index** — non-code kinds only (`pair`, `key`, `table`, `target`, `heading`):

Non-code files retain the existing file-level embedding. Individual config/doc
entries carry low signal ("timeout = 30" tells you nothing), but collectively
they characterize the file ("this is the pytest config section of pyproject.toml").
The current file scaffold already aggregates them well: `configures addopts,
artifacts, GITHUB_TOKEN`, `sections build-system, project`, `topics API
Authentication, Installation`.

This includes **markdown files**. Headings are structurally similar to config
entries — individually just labels, collectively they describe the document.
The file scaffold's `topics` line captures all headings.

### 2.2 Why Not Both Indices for Code Files

If each code def's scaffold includes the module path context
("module auth middleware rate limiter"), the file-level embedding is redundant
for code files. The per-def vectors subsume the file-level signal — every def
already encodes "I live in src/auth/middleware/rate_limiter.py". No need for a
separate file vector when the ranker can aggregate per-def scores by file if
needed.

For non-code files, per-entry embedding wastes vectors on near-identical
low-signal text. File-level embedding captures the collective signal that
actually discriminates between config files.

Result: two disjoint indices, zero overlap, clean separation.

### 2.3 Index Storage

Two matrices stored in `.codeplane/`:

| Index | Contents | Key | Storage |
|-------|----------|-----|---------|
| `def_embeddings.npz` | Code DefFact vectors | `def_uid` | ~6 MB (8K defs) |
| `file_embeddings.npz` | Non-code file vectors | `file_path` | ~100 KB |

### 2.4 Query-Time Behavior

For a query:

1. Embed query text once.
2. Search both matrices:
   - Def index returns `(def_uid, similarity)` pairs for code objects.
   - File index returns `(file_path, similarity)` pairs for non-code files.
3. For file-level hits: propagate the file's similarity score to all non-code
   DefFacts in that file.
4. Union into one candidate pool keyed by `def_uid` with `emb_score` per object.

### 2.5 Adaptive Batching

Texts sorted by length before batching. Batch size adapts to text length:

- Short (<500 chars / ~140 tokens): base batch × 4
- Medium (500–1200 chars / ~340 tokens): base batch × 2
- Long (>1200 chars / 340+ tokens): base batch

Base batch size adapts to available system memory. ONNX attention cost is
quadratic in sequence length — short texts are trivially cheap per-element.
Since 99.9% of code def scaffolds are short, the vast majority batch at 4×.

### 2.6 Incremental Updates

Same lifecycle as the current `FileEmbeddingIndex`: `stage_file` →
`commit_staged` → `save`. On file change, recompute scaffolds for all DefFacts
in that file, re-embed only the changed ones (signature hash comparison).
Non-code files re-embed at file level as before.

---

## 3. Models

### 3.1 Model 1: Object Ranker

**Goal:** Given query $q$ and candidate semantic object $o$, score
$P(\text{touched} \mid q, o)$.

**Base model:** LightGBM LambdaMART.

**Features per candidate** (`candidates_rank` row):

| Feature | Source | Description |
|---------|--------|-------------|
| `emb_score` | Embedding | Cosine similarity (per-def for code, per-file for non-code) |
| `emb_rank` | Embedding | Rank among all candidates by embedding score |
| `lex_score` | Lexical | Count of Tantivy hits landing within this def's span |
| `lex_rank` | Lexical | Rank among all candidates by lexical hit count |
| `term_score` | Term match | IDF-weighted term match score for this def's name |
| `term_rank` | Term match | Rank among all candidates by term score |
| `graph_score` | Graph | Edge quality score (callee/caller/sibling weighted by seed rank) |
| `graph_rank` | Graph | Rank among all candidates by graph score |
| `symbol_score` | Explicit | Symbol/path resolution score |
| `symbol_rank` | Explicit | Rank by symbol score |
| `retriever_hits` | All | Count of retrievers (0–5) that surfaced this object |
| `object_kind` | Metadata | Function, class, method, pair, etc. |
| `object_size_lines` | Metadata | `end_line - start_line + 1` |
| `file_ext` | Metadata | Source file extension |
| `path_tokens` | Metadata | Tokenized file path features |
| `query_len` | Query | Query character length |
| `has_identifier` | Query | Query contains identifier-like tokens |
| `has_path` | Query | Query contains file path references |
| `label_rank` | Ground truth | Graded: edited > read-necessary > untouched (ordinal) |

**Training:** Group by `(run_id, query_id)`. Optimize NDCG. Only OK-labeled
queries participate.

**Inference:** Score all candidates in the pool, sort descending, pass to cutoff.

### 3.2 Model 2: Cutoff

**Goal:** Predict $N(q)$: how many top-ranked objects to return, maximizing F1
against the ground-truth touched set, subject to the rendering budget
(a system configuration parameter).

**Base model:** LightGBM regressor.

**Target label:** $N^*(q)$ per query: the value of $N$ that maximizes F1
between the top-$N$ predicted set and the ground-truth touched set. Computed
empirically per query from out-of-fold ranker outputs.

**Features per query** (`queries_cutoff` row):

| Feature | Description |
|---------|-------------|
| `query_len`, `has_identifier`, `has_path` | Query text features |
| `object_count`, `language_mix` | Repo features |
| Score distribution features | Full profile of the ranked list: ordered scores, pairwise gaps, cumulative mass, entropy, variance |
| Multi-retriever agreement | Distribution of `retriever_hits` across the ranked list |
| `N_star` | Empirically optimal cutoff (target) |

**No-leakage training:**

K-fold across tasks/runs:

1. Train ranker on K−1 folds.
2. Score held-out fold → out-of-fold ranked list.
3. Compute $N^*$ per held-out query.
4. Train cutoff on aggregated out-of-fold data.

**Inference:** Ranker produces ranked list → compute distribution features →
cutoff predicts $N(q)$ → return top $N$ → enforce rendering budget.

### 3.3 Model 3: Gate

**Goal:** Classify (query, repo) as OK / UNSAT / BROAD / AMBIG before
committing to ranker + cutoff.

**Base model:** LightGBM multiclass classifier.

**Features per query** (`queries_gate` row):

| Feature | Description |
|---------|-------------|
| `query_len` | Query length |
| `identifier_density` | Ratio of identifier-like tokens |
| `path_presence` | Contains file paths |
| `has_numbers`, `has_quoted_strings` | Surface features |
| `object_count`, `file_count` | Repo stats |
| `top_score` | Highest retrieval score |
| Score decay profile | Full vector of ordered scores |
| `path_entropy` | Entropy of file paths among top candidates |
| `cluster_count` | Number of disjoint directory clusters among top candidates |
| Multi-retriever agreement | Distribution across candidates |
| `total_candidates` | Pool size |
| `label_gate` | OK / UNSAT / BROAD / AMBIG (target) |

All features are continuous. The model learns its own decision boundaries.

**Training:** Multiclass cross-entropy. All labels from reasoning-agent
authoring, validated against retrieval distribution.

**Inference:**

| Gate output | Action |
|------------|--------|
| OK | Ranker + cutoff → return results |
| UNSAT | Surface mismatch, ask for correction |
| AMBIG | Ask for disambiguation |
| BROAD | Ask for decomposition |

---

## 4. Runtime Flow

```
Query
  │
  ├─ Embedding query (def matrix + file matrix)
  ├─ Lexical search (Tantivy)
  ├─ Term match (SQL LIKE + IDF)
  ├─ Symbol/path resolution
  │
  ▼
Candidate pool (union by def_uid, per-retriever scores)
  │
  ├─ Graph walk (1-hop from top candidates)
  │
  ▼
Full candidate pool + features
  │
  ├─ Gate classifies (query, repo) pair
  │     ├─ UNSAT → surface mismatch
  │     ├─ AMBIG → ask for disambiguation
  │     ├─ BROAD → ask for decomposition
  │     └─ OK ──┐
  │              ▼
  │     Ranker scores each candidate
  │              │
  │              ▼
  │     Cutoff predicts N(q)
  │              │
  │              ▼
  │     Return top N (enforce rendering budget)
  │
  ▼
Response to agent
```

Gate runs on retrieval distribution features, so retrieval happens regardless.
The cost of candidate generation + feature extraction is cheap; early exit on
non-OK avoids returning a bad result set.

---

## 5. Dataset Generation

### 5.1 Repo Selection

**Scope:** 10 languages (codeplane's first-class citizens) × 3 repos per
language = 30 repos.

**Selection criteria** (qualitative, applied once by human or agent with
human review):

1. **Scale diversity within each language:**
   - One focused library/tool — a single developer could hold the entire
     codebase in their head.
   - One multi-module project with clear internal boundaries — requires
     navigating between subsystems.
   - One large project where no single developer knows all the code —
     deep module hierarchies, multiple teams' worth of functionality.

2. **Structural quality:**
   - Codeplane indexes the repo successfully (parses, builds graph,
     generates embeddings).
   - Code is reasonably well-structured (not a single monolithic file,
     not entirely generated code).

3. **History richness:**
   - Repo has meaningful commit/PR history — enough that a task-authoring
     agent can study real development patterns to generate realistic tasks.

4. **Open source and permissively licensed** — training data must be usable
   under the repo's license.

**Validation (one-time, post-selection):** After indexing all 30 repos, confirm
that the distribution of semantic object counts spans a wide range. If it
clusters, swap repos to increase diversity.

### 5.2 Task Generation

**Who:** A "task author" reasoning agent, operating per repo.

**Process:**

1. Index the repo via codeplane. Explore structure: module layout, key
   abstractions, patterns.
2. Read recent commit messages and PR descriptions for flavor of real work.
3. Generate tasks across a range of scopes:
   - **Narrow** — bug fix or small feature touching one or two files.
   - **Medium** — feature or refactor spanning a module or subsystem.
   - **Wide** — cross-cutting change touching multiple subsystems.

   The agent produces a mix, covering major subsystems and varied task types
   (bug fix, feature, refactor, test improvement, API change, config change,
   etc.).

4. Each task is a natural-language description of work, written as if it were
   an issue or task assignment. No code, no diffs, no hints about which files
   to touch.

**Quality gate:** A review pass verifies each task is (a) well-defined enough
to start, (b) grounded in the actual codebase, (c) scoped as intended.

### 5.3 Data Collection Pipeline

Two independent phases with separate outputs:

- **Ground truth** (Phase 1+2): run once per repo, output is permanent.
- **Retrieval signals** (Phase 3): re-run whenever harvesters change.

#### Phase 1+2: Solve + Reflect (Ground Truth)

One agent session per repo. The agent receives a single prompt pointing
it at the repo's MD file (`ranking/repos/{name}.md`) and the output
directory. It reads the file, works through every task sequentially,
and writes structured output per task.

**Agent prompt** (one per repo, sent once):

```
You are a coding agent working on the repository cloned at
ranking/clones/{clone_name}/.

Read the file ranking/repos/{name}.md — it contains a description of the
repository structure and a list of tasks (sections N1, N2, ..., M1, ...,
W1, ...).

For EACH task in the file, do the following:

  STEP 1 — SOLVE: Read the code you need, make the necessary edits, and
  verify they work. Use the tools available to you (file reads, edits,
  terminal). When the task is complete, capture a git diff, then
  `git stash` to restore the repo to its clean state before starting
  the next task.

  STEP 2 — REFLECT: After solving (but before stashing), write a JSON
  file to ranking/data/{repo_id}/ground_truth/{task_id}.json with this
  exact structure:

  {
    "task_id": "N1",
    "task_text": "<the full task description from the md file>",
    "edited_files": ["path/to/file1.py", "path/to/file2.py"],
    "read_necessary": ["path/to/file3.py", "path/to/file4.py"],
    "queries": [
      {"query_type": "L0", "query_text": "..."},
      {"query_type": "L1", "query_text": "..."},
      {"query_type": "L2", "query_text": "..."},
      {"query_type": "UNSAT", "query_text": "..."},
      {"query_type": "BROAD", "query_text": "..."}
    ]
  }

  Fields:
  - task_id: the task ID from the heading (N1, M1, W2, etc.)
  - task_text: the full task description text
  - edited_files: files you modified (from your diff)
  - read_necessary: files you read that were genuinely necessary to
    understand or solve the task — NOT files you opened and immediately
    closed or explored out of curiosity. Do NOT include edited files
    here (they are tracked separately).
  - queries: 3 to 6 query objects:

    THREE OK queries at increasing specificity:
    - L0: High-level task description. No identifiers, no paths. A
      developer familiar with the codebase would know where to look.
    - L1: L0 plus concrete identifiers — symbol names, error strings,
      function names — drawn from the code you actually touched.
    - L2: L1 plus anchoring constraints — file paths, module names,
      behavioral constraints.

    UP TO THREE non-OK queries (skip any that feel forced):
    - UNSAT: A plausible query about the same area that makes a
      factually wrong assumption about the architecture.
    - BROAD: A large effort this task is part of — touching many files
      across subsystems. Too big for one task.
    - AMBIG: Same domain, but this repo has multiple subsystems that
      could be the target and the query doesn't resolve between them.

    You MUST produce L0, L1, L2. Non-OK queries are optional — produce
    only those that arise naturally. Fewer is better than forced.

Work through every task in the file. After completing all tasks, say
"ALL TASKS COMPLETE".
```

The `{name}`, `{clone_name}`, and `{repo_id}` placeholders are filled
from the repo's MD file metadata. The agent produces one JSON file per
task under `ranking/data/{repo_id}/ground_truth/`.

After all JSON files are written, a post-processing step:

1. Extracts `edited_files` per task, maps changed lines to DefFacts via
   the codeplane index → "edited" TouchedObjects.
2. Maps `read_necessary` paths to DefFacts → "read_necessary"
   TouchedObjects.
3. Assembles `runs.jsonl`, `touched_objects.jsonl`, `queries.jsonl`
   from the per-task JSON files.

**Output:** `runs.jsonl`, `touched_objects.jsonl`, `queries.jsonl` under
`data/{repo_id}/ground_truth/`. This data is permanent — it never needs
re-collection.

#### Phase 3: Retrieval Signal Collection (Re-runnable)

A separate step, run against the indexed repo:

**3a. Raw signal collection:**

For **each query** in the ground truth (3 OK + up to 3 bad), call
`recon_raw_signals(query)`. This returns the candidate pool with per-retriever
scores for all queries — including bad ones, because the gate model needs to
learn what each class looks like from the retrieval distribution.

**3b. Label joining and validation:**

Join raw signal output with ground-truth labels to compute `label_rank`
per candidate. For gate labels, validate that the retrieval distribution
is consistent with the authored label:

- **OK**: top candidates concentrate around the touched set.
- **UNSAT**: top candidates are low-confidence or irrelevant.
- **BROAD**: top candidates cover only a fraction of what the broad task
  would touch.
- **AMBIG**: top candidates cluster in multiple disjoint regions.

If inconsistent, flag for review (ground truth is not re-authored —
the signals are what changed).

**Output:** `candidates_rank.jsonl` under `data/{repo_id}/signals/`.
This data is re-collected whenever embeddings, harvesters, or scoring
change.

---

## 6. `recon_raw_signals()` MCP Endpoint

**Input:** A single query string (plain text).

**Context:** Runs against the existing codeplane index. No seeds, no pins.

**Internal behavior:**

1. Parse query text (extract terms, paths, symbols — same as current
   `parse_task`).
2. Run all retrievers against the index:
   - Embedding query (both def-level and file-level matrices) →
     `(def_uid, emb_score)` pairs.
   - Lexical search (Tantivy) → map line hits to containing DefFacts →
     `(def_uid, lex_hit_count)`.
   - Term match (SQL LIKE + IDF) → `(def_uid, term_idf_score)`.
   - Symbol/path resolution → `(def_uid, symbol_score)`.
3. Union into candidate pool keyed by `def_uid`.
4. Run graph walk from top candidates → add graph-discovered defs with
   edge quality scores.
5. Per candidate, compute ranks within the pool per retriever.
6. Return structured response:

```json
{
    "query_features": {
        "query_len": 42,
        "has_identifier": true,
        "has_path": false,
        "identifier_density": 0.3,
        "has_numbers": false,
        "has_quoted_strings": false
    },
    "repo_features": {
        "object_count": 23661,
        "file_count": 444
    },
    "candidates": [
        {
            "def_uid": "src/auth/middleware.py::RateLimiter.check_rate",
            "path": "src/auth/middleware.py",
            "kind": "method",
            "name": "check_rate",
            "start_line": 45,
            "end_line": 78,
            "object_size_lines": 34,
            "file_ext": ".py",
            "emb_score": 0.82,
            "emb_rank": 3,
            "lex_score": 4.0,
            "lex_rank": 1,
            "term_score": 1.7,
            "term_rank": 2,
            "graph_score": 0.85,
            "graph_rank": 5,
            "symbol_score": null,
            "symbol_rank": null,
            "retriever_hits": 4
        }
    ]
}
```

**Does not:** Run any model (ranker, cutoff, gate). Does not filter, sort, or
truncate the candidate pool. Returns the raw union.

---

## 7. Training Datasets

### 7.1 `runs`

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | str | Unique task run identifier |
| `repo_id` | str | Repository identifier |
| `repo_sha` | str | Commit SHA at solve time |
| `task_id` | str | Task identifier |
| `task_text` | str | Full task description |
| `agent_version` | str | Coding agent version |
| `status` | str | Solve outcome |

### 7.2 `touched_objects`

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | str | Task run identifier |
| `def_uid` | str | DefFact stable identity |
| `path` | str | File path |
| `kind` | str | DefFact kind |
| `name` | str | DefFact name |
| `start_line` | int | Span start |
| `end_line` | int | Span end |
| `touch_type` | str | `edited` / `read_necessary` |

### 7.3 `queries`

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | str | Task run identifier |
| `query_id` | str | Unique query identifier |
| `query_text` | str | Full query text |
| `query_type` | str | `L0` / `L1` / `L2` (for OK) or `UNSAT` / `BROAD` / `AMBIG` |
| `label_gate` | str | `OK` / `UNSAT` / `BROAD` / `AMBIG` |

### 7.4 `candidates_rank`

One group per `(run_id, query_id)`. Rows are candidates. Used for ranker
training (OK queries only) and gate feature computation (all queries).

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | str | Task run identifier |
| `query_id` | str | Query identifier |
| `def_uid` | str | Candidate DefFact |
| `emb_score` | float | Embedding similarity |
| `emb_rank` | int | Rank by embedding |
| `lex_score` | float | Lexical hit count |
| `lex_rank` | int | Rank by lexical |
| `term_score` | float | IDF-weighted term match |
| `term_rank` | int | Rank by term match |
| `graph_score` | float | Graph edge quality |
| `graph_rank` | int | Rank by graph |
| `symbol_score` | float | Symbol/path match |
| `symbol_rank` | int | Rank by symbol |
| `retriever_hits` | int | Count of retrievers (0–5) |
| `object_kind` | str | DefFact kind |
| `object_size_lines` | int | Span size |
| `file_ext` | str | File extension |
| `query_len` | int | Query length |
| `has_identifier` | bool | Query has identifiers |
| `has_path` | bool | Query has paths |
| `label_rank` | int | Graded: edited > read-necessary > untouched |

### 7.5 `queries_cutoff`

One row per `(run_id, query_id)`. Only OK queries. Computed from out-of-fold
ranker outputs.

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | str | Task run identifier |
| `query_id` | str | Query identifier |
| Query features | various | `query_len`, `has_identifier`, `has_path` |
| Repo features | various | `object_count`, `language_mix` |
| Score distribution features | various | Full ranked-list profile: ordered scores, gaps, cumulative mass, entropy, variance |
| Multi-retriever agreement | float | Distribution of `retriever_hits` across ranked list |
| `N_star` | int | Empirically optimal cutoff |

### 7.6 `queries_gate`

One row per query. All query types.

| Column | Type | Description |
|--------|------|-------------|
| `query_id` | str | Query identifier |
| Query text features | various | `query_len`, `identifier_density`, `path_presence`, `has_numbers`, `has_quoted_strings` |
| Repo features | various | `object_count`, `file_count` |
| Retrieval distribution features | various | `top_score`, score decay profile, path entropy, cluster count, multi-retriever agreement, total candidate count |
| `label_gate` | str | `OK` / `UNSAT` / `BROAD` / `AMBIG` |

---

## 8. Training Procedures

### 8.1 Ranker

1. Filter to OK-labeled queries only.
2. Train LightGBM LambdaMART grouped by `(run_id, query_id)`.
3. Optimize NDCG with graded relevance labels.

### 8.2 Cutoff (no-leakage K-fold)

1. K-fold split across tasks/runs.
2. Per fold: train ranker on K−1 folds, score held-out fold → out-of-fold
   ranked list.
3. Per held-out query: compute $N^*(q) = \arg\max_N F_1(\text{top-}N,
   \text{ground truth})$.
4. Collect `queries_cutoff` rows from all folds.
5. Train cutoff regressor on aggregated out-of-fold data.

### 8.3 Gate

1. Combine all query types (OK, UNSAT, BROAD, AMBIG) with their validated
   labels.
2. Compute retrieval distribution features from `candidates_rank` (all
   queries, not just OK).
3. Train LightGBM multiclass classifier, cross-entropy objective.

---

## 9. Evaluation (EVEE)

Evaluation uses the existing [EVEE](https://github.com/microsoft/evee)
(`evee-ms-core`) benchmarking framework already integrated in
`benchmarking/`. The ranking system evaluation extends the current
recon evaluation infrastructure.

### 9.1 New Experiment: `recon_ranking.yaml`

A new EVEE experiment config that evaluates the full ranking pipeline
(ranker + cutoff + gate) end-to-end, replacing the current file-level
retrieval evaluation with def-level evaluation.

### 9.2 New EVEE Components

#### Dataset: `cpl-ranking-gt`

Loads the training dataset tables (§7) as EVEE dataset records. Each record
is a `(run_id, query_id)` pair with the ground-truth touched objects and
gate label.

#### Model: `cpl-ranking`

Wraps the full ranking pipeline:

1. Calls `recon_raw_signals(query)` to get the candidate pool.
2. Runs the gate model → classifies the query.
3. If OK: runs ranker → scores candidates → runs cutoff → returns top N.
4. If non-OK: returns the gate classification and no candidates.

Returns the predicted set (ranked DefFact list), gate prediction, and
predicted N.

#### Metrics

| Metric | What it measures | Granularity |
|--------|-----------------|-------------|
| `cpl-ranker-ndcg` | NDCG of the ranked list against ground-truth relevance grades | Per query |
| `cpl-ranker-hit-at-k` | Whether edited objects appear in the top K | Per query |
| `cpl-cutoff-f1` | F1 of the returned set (top N) against ground truth | Per query |
| `cpl-cutoff-precision` | Precision of the returned set | Per query |
| `cpl-cutoff-recall` | Recall of the returned set | Per query |
| `cpl-gate-accuracy` | Classification accuracy of OK/UNSAT/BROAD/AMBIG | Per query |
| `cpl-gate-confusion` | Confusion matrix across gate classes | Aggregate |

### 9.3 Comparison Experiments

#### Baseline: Current Recon (heuristic RRF + elbow)

The existing `recon_baseline.yaml` experiment measures file-level retrieval
quality with the current heuristic pipeline. This serves as the baseline for
comparison.

#### Head-to-head: Ranking vs Heuristic

A paired experiment using the same task/query sets:

- **Control**: current recon pipeline (RRF + elbow + tier assignment)
- **Treatment**: ranking pipeline (ranker + cutoff + gate)

Both produce a set of DefFacts (the heuristic pipeline's file-level output
is expanded to all defs in the returned files for comparable evaluation).
Measured on the same ground-truth touched objects.

Key metrics for comparison:

| Metric | Question answered |
|--------|-------------------|
| F1 delta | Does the ranking pipeline return a better set? |
| Precision delta | Does it reduce noise (fewer irrelevant objects)? |
| Recall delta | Does it find more of what was actually needed? |
| Gate accuracy | Does the gate correctly route non-OK queries? |
| Latency | Is the ranking pipeline fast enough for interactive use? |

### 9.4 Evaluation Cadence

1. **During development**: Run evaluation on the codeplane repo's own
   ground truth (existing 72-record dataset adapted to def-level).
2. **After dataset generation**: Run evaluation on the full 30-repo
   training set using held-out folds.
3. **Ongoing**: Add new repos/tasks to the evaluation set as codeplane
   adds language support. Re-train and re-evaluate.

### 9.5 Project Structure

The ranking system spans three locations, each with a distinct concern:

**Runtime inference** — ships with the `codeplane` package:

```
src/codeplane/ranking/
├── __init__.py              # Public API: rank_candidates(), classify_gate()
├── ranker.py                # Model 1: LambdaMART object ranker (load + score)
├── cutoff.py                # Model 2: Regressor for N(q) prediction
├── gate.py                  # Model 3: Multiclass gate classifier
├── features.py              # Feature extraction from raw signals
├── models.py                # Types: GateLabel, RankingResult, etc.
└── data/                    # Serialized model artifacts (package data)
    ├── ranker.lgbm
    ├── cutoff.lgbm
    └── gate.lgbm
```

`lightgbm` becomes a runtime dependency (inference only). Model artifacts
are package data — they ship with `codeplane` releases, not stored in the
target repo's `.codeplane/` directory.

**Training pipeline** — separate top-level project, produces model artifacts:

```
ranking/
├── pyproject.toml           # Own deps: lightgbm, scikit-learn, pandas, httpx
├── README.md
└── src/
    └── cpl_ranking/
        ├── __init__.py
        ├── schema.py            # §7 dataset table schemas
        ├── collector.py         # Ground truth collection (stable, run once)
        ├── collect_signals.py   # Retrieval signal collection (re-runnable)
        ├── train_ranker.py      # §8.1 LambdaMART training
        ├── train_cutoff.py      # §8.2 no-leakage K-fold cutoff training
        ├── train_gate.py        # §8.3 multiclass gate training
        └── train_all.py         # Orchestrates all 3 training stages
```

Training data (gitignored) lives under `ranking/data/{repo_id}/`:
- `ground_truth/` — stable: runs, touched_objects, queries (collected once)
- `signals/` — re-collectable: candidates_rank (re-run when harvesters change)

Produced model artifacts get copied into `src/codeplane/ranking/data/`
for release.

**EVEE evaluation** — extends the existing `benchmarking/` framework:

```
benchmarking/
├── datasets/
│   ├── recon_gt.py                  # existing — file-level ground truth
│   └── ranking_gt.py                # NEW — def-level ground truth
├── models/
│   ├── recon.py                     # existing — heuristic recon
│   └── recon_ranking.py             # NEW — ranking pipeline wrapper
├── metrics/
│   ├── retrieval.py                 # existing — file-level P/R/F1
│   ├── ranking.py                   # NEW — NDCG, hit@K, cutoff F1
│   └── gate.py                      # NEW — gate accuracy, confusion
└── experiments/
    ├── recon_baseline.yaml          # existing
    ├── recon_ranking.yaml           # NEW — ranking evaluation
    └── ranking_vs_heuristic.yaml    # NEW — head-to-head comparison
```

---

## 10. Data Flow Summary

```
┌────────────────────────────────────────────────────────┐
│                    PER REPO (×30)                      │
│                                                        │
│  One agent session per repo                            │
│    │                                                   │
│    ├─ Reads ranking/repos/{name}.md                    │
│    ├─ Repo cloned at ranking/clones/{clone}/           │
│    │                                                   │
│    ├─ FOR EACH TASK in the md file:                    │
│    │    1. Solve: read code, make edits, verify        │
│    │    2. Reflect: write {task_id}.json with:         │
│    │       - edited_files (from diff)                  │
│    │       - read_necessary (agent judgment)           │
│    │       - queries: L0, L1, L2 + optional            │
│    │         UNSAT, BROAD, AMBIG                       │
│    │    3. git stash → clean for next task             │
│    │                                                   │
│    └─ Output: data/{repo_id}/ground_truth/*.json       │
│                                                        │
│  Post-processing (automated):                          │
│    Map edited/read files → DefFacts via codeplane      │
│    Assemble runs.jsonl, touched_objects.jsonl,          │
│    queries.jsonl                                       │
│                                                        │
│  ┌──────────────────────────────────────────────┐      │
│  │  Phase 3: Signals (RE-RUNNABLE)              │      │
│  │    3a. Call recon_raw_signals() per query     │      │
│  │        → candidate pools with per-retriever   │      │
│  │          scores for all queries               │      │
│  │    3b. Join with ground truth, validate       │      │
│  │                                              │      │
│  │  Output: signals/                             │      │
│  │    candidates_rank (all queries)              │      │
│  └──────────────────────────────────────────────┘      │
│                                                        │
└────────────────────────────────────────────────────────┘
                         │
                         ▼
              Offline Training Pipeline
              ┌───────────────────────┐
              │  K-fold ranker train   │
              │  Out-of-fold scoring   │
              │  Compute N* per query  │
              │  Train cutoff model    │
              │  Train gate model      │
              └───────────────────────┘
                         │
                         ▼
              3 LightGBM models (CPU)
              ┌───────────────────────┐
              │  ranker.lgbm          │
              │  cutoff.lgbm          │
              │  gate.lgbm            │
              └───────────────────────┘
                         │
                         ▼
              EVEE Evaluation
              ┌───────────────────────┐
              │  NDCG, Hit@K          │
              │  Cutoff F1/P/R        │
              │  Gate accuracy        │
              │  vs heuristic delta   │
              └───────────────────────┘
```

---

## 11. What Changes in the Codebase

| Component | Change | Effort |
|-----------|--------|--------|
| **Embedding pipeline** | Add per-DefFact embedding index for code kinds. Non-code files keep file-level index. Two disjoint matrices, no overlap. | Medium |
| **`recon_raw_signals()` endpoint** | New MCP endpoint. Runs all harvesters, skips RRF/elbow/tier. Returns raw per-def features + scores. Composed from existing harvester functions. | Medium |
| **Harvesters (B–E)** | None. Already produce per-def signals. | Zero |
| **Production recon path** | None for v1. Continues using RRF + elbow. Models replace it later. | Zero |
| **Embedding query** | Add def-level matrix query alongside file-level. Union results into single candidate pool with `emb_score` per `def_uid`. | Small |
| **Index storage** | Add `def_embeddings.npz` + `def_meta.json` alongside existing file embedding files. | Small |
| **Runtime inference (`src/codeplane/ranking/`)** | New package. Loads serialized LightGBM models, extracts features from raw signals, runs ranker + cutoff + gate inference. Ships as package data with `codeplane`. | Medium |
| **Training pipeline (`ranking/`)** | New top-level project. Dataset schemas (§7), data collection orchestrator (§5.3), K-fold LightGBM training scripts (§8). Separate `pyproject.toml` and dependency set. | Large |
| **EVEE evaluation (`benchmarking/`)** | New experiment configs, dataset loader, model wrapper, and metrics for def-level ranking evaluation. | Medium |

---

## 12. Design Invariant

Every numeric boundary in this system is either:

- **Learned from data** (ranker scores, cutoff N, gate decision boundaries)
- **A system configuration parameter** with a clear operational definition
  (rendering budget = how many bytes the inline response can hold)
- **Computed empirically per query** ($N^* = \arg\max_N F_1$ for this
  specific ranked list and touched set)

No arbitrary constants in model definitions, feature computations, label
definitions, selection criteria, or training procedures.
