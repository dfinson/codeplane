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

### 1.3 Query Tiers

Eight OK query tiers: five isolation tiers (each exercises a single retrieval
signal) and three combination tiers (exercise multi-signal agreement).
Seeds and pins are passed as separate arguments alongside the query text,
matching the production `recon` and `recon_raw_signals` API signatures.

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

Isolation tiers ensure the ranker sees examples where individual signals
dominate. Combination tiers ensure it learns when multi-signal agreement
is predictive. Q_IDENT_NAV and Q_FULL mirror how agents actually call
recon in production (after `map_repo` provides symbols and paths).

**Non-OK tiers** (up to 2 each, optional — skip if forced):

| Tier | Definition |
|------|------------|
| **UNSAT** | Query makes factually wrong assumptions about the repo |
| **BROAD** | Work spanning 15+ files in 3+ unrelated directories |
| **AMBIG** | 2+ subsystems could be the target; query doesn’t specify which |

Total per task: 8 required OK + 0–6 optional non-OK = 8–14 queries.

### 1.4 Gate Taxonomy

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
| `label_relevant` | Ground truth | Binary: relevant (1) or irrelevant (0) |

**Training:** Group by `(run_id, query_id)`. Optimize NDCG with binary gain.
Only OK-labeled queries participate.

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

Three repo sets serve different purposes:

**Ranker + Gate set** (30 repos): 10 languages × 3 repos per language
(small/medium/large scale). Trains the object ranker and gate classifier.
30 tasks per repo (10 narrow / 10 medium / 10 wide), each with 5 OK
queries + up to 9 bad queries (3× UNSAT, 3× BROAD, 3× AMBIG).

**Cutoff set** (20 repos): 10 languages × 2 repos per language.
Trains the cutoff predictor (N* estimation). 30 tasks per repo, each
with 5 OK queries only (no bad queries — non-OK queries are gated
before cutoff). Repos chosen for **varied touched-set sizes** so the
cutoff model sees a wide range of N* values.

**Evaluation set** (15 repos): 10 languages × 1 medium repo per language
+ 5 additional repos for the most popular languages (Python, TypeScript,
Go, Java, C++). Uses the full query set (5 OK + up to 9 bad) — same
as ranker+gate. Held out from training for unbiased evaluation.

**Total:** 65 repos, 1,950 tasks, 9,750+ queries.

**Selection criteria** (all sets):

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

3. **Open source and permissively licensed** — training data must be usable
   under the repo's license.

**Validation (one-time, post-selection):** After indexing all 65 repos, confirm
that the distribution of semantic object counts spans a wide range. If it
clusters, swap repos to increase diversity.

### 5.2 Task Generation

**Who:** A "task author" reasoning agent, operating per repo.

**Task count:** 30 tasks per repo (10 narrow / 10 medium / 10 wide).

**Process:**

1. Read the cloned source code. Explore module layout, key abstractions,
   file organization, and code patterns.
2. Generate tasks across a range of scopes:
   - **Narrow** — bug fix or small feature touching one or two files.
   - **Medium** — feature or refactor spanning a module or subsystem.
   - **Wide** — cross-cutting change touching multiple subsystems.

   The agent produces a mix, covering major subsystems and varied task types
   (bug fix, feature, refactor, test improvement, API change, config change,
   etc.).

4. Each task is a natural-language description of work, written as if it were
   an issue or task assignment. No code, no diffs, no hints about which files
   to touch.

**Quality gate:** Each task must be:
(a) well-defined enough to start without clarifying questions,
(b) grounded in the actual source tree — referencing real modules,
    real architectural boundaries, and real behavioral patterns that
    exist in the cloned code,
(c) scoped correctly for its tier (narrow: 1-2 files, medium: one
    module/subsystem, wide: cross-cutting across multiple subsystems),
(d) designed to produce good queries — the agent solving the task
    should be able to write queries that exercise all eight query
    tiers (5 isolation + 3 combination, see §1.3).

### 5.3 Data Collection Pipeline

Two independent phases with separate outputs:

- **Ground truth** (Phase 1+2): run once per repo, output is permanent.
- **Retrieval signals** (Phase 3): re-run whenever harvesters change.

#### Phase 1+2: Solve + Reflect (Ground Truth)

One agent session per repo. The agent receives a single prompt and works
through every task sequentially, producing structured ground truth output.

**Agent prompt** (one per repo, sent once):

```
Read the file at ../../repos/{name}.md and understand it fully.

Solve each task using native tools. After solving each task (but before
stashing), produce the ground truth record.

For EACH task in the file:

  STEP 1 — SOLVE
  Read the code, make edits, verify they work. Capture a git diff.
  Then git stash to restore clean state before the next task.

  STEP 2 — REFLECT
  Write a JSON file to ../../data/{repo_id}/ground_truth/{task_id}.json.

  GROUND TRUTH FORMAT:

  {
    "task_id": "N1",
    "task_text": "<full task description from the md file>",
    "relevant_defs": [
      {"path": "<repo-relative path>", "name": "<def name>", "kind": "<kind>"}
    ],
    "queries": [
      {"query_type": "Q_SEMANTIC", "query_text": "...", "seeds": [], "pins": []},
      ...
    ]
  }

  FIELD DEFINITIONS:

  task_id: The heading ID from the md file (N1, M1, W2, etc.)

  task_text: The full task description text, verbatim.

  relevant_defs: Every function, method, class, or definition that was
  RELEVANT to solving this task. This includes:
    - Defs you EDITED (changed their code)
    - Defs you READ because understanding them was necessary to make
      the correct change (contracts, interfaces, callers, base classes,
      related config)

  Do NOT include:
    - Defs you opened and immediately closed without using
    - Defs you skimmed out of curiosity but didn't need
    - Entire files — list specific defs, not "everything in file X"

  Each entry has:
    - path: repo-relative file path (e.g. "src/auth/middleware.py")
    - name: the definition's simple name (e.g. "check_rate")
    - kind: one of: function, method, class, struct, interface, trait,
      enum, variable, constant, module, property, pair, key, table,
      target, heading

  If you edited a method inside a class, list the METHOD. Only list
  the parent class if you also needed its class-level code (class
  variables, __init__, decorators, etc.).

  WHY THIS MATTERS: This list becomes the ground truth label for
  training a ranking model. Every def you list will be labeled
  "relevant." Every def you omit will be labeled "irrelevant." If
  you include junk, the model learns to surface junk. If you miss
  something genuinely needed, the model learns to miss it. Be precise.

  SEED AND PIN RULES:
  - seeds: symbol names from the code you touched. Pick the 1-4 MOST
    CENTRAL ones — what a developer would know from the task
    description or from running map_repo before starting work.
    Do NOT include every helper that got touched.
  - pins: repo-relative file paths. Pick the 2-4 MOST OBVIOUS files
    — what a developer could identify from the task description or
    repo structure before starting work.
  - Seeds and pins represent what a developer knows GOING IN, not
    perfect hindsight of the full answer.

  THE 8 OK QUERY TYPES (ALL 8 REQUIRED):

  Q_SEMANTIC (isolation — embedding only):
    Describe the problem using ONLY domain/business concepts.
    FORBIDDEN: symbol names, file paths, code terms, language keywords.
    REQUIRED: a description that a non-programmer could understand.
    seeds: []  pins: []
    Example: "The component that limits how many API requests a client
    can make within a time window"

  Q_LEXICAL (isolation — full-text only):
    Use strings that appear LITERALLY in the source code.
    REQUIRED: at least one phrase in quotes that grep would find —
    an error message, log string, comment, docstring, or string literal.
    FORBIDDEN: symbol names that don't appear as literal strings.
    seeds: []  pins: []
    Example: 'The code that raises "rate limit exceeded for client"'

  Q_IDENTIFIER (isolation — term match only):
    List exact symbol names from the code you touched.
    REQUIRED: at least 3 symbol names, comma-separated.
    FORBIDDEN: file paths, English descriptions, relationship words.
    seeds: []  pins: []
    Example: "RateLimiter, check_rate, _sliding_window_count,
    MAX_REQUESTS_PER_WINDOW"

  Q_STRUCTURAL (isolation — graph only):
    Describe the code through structural relationships.
    REQUIRED: at least one concrete symbol AND a relationship word
    (callers, callees, subclasses, implementors, siblings, imports,
    parent class).
    seeds: 1-2 (the entry points for graph traversal)
    pins: []
    Example: "The callers of RateLimiter.check_rate and the class
    that RateLimiter inherits from"

  Q_NAVIGATIONAL (isolation — explicit/path only):
    Use explicit file paths and directory locations.
    REQUIRED: at least 2 file paths from the files you touched.
    FORBIDDEN: domain descriptions, relationship words.
    seeds: []
    pins: 2-4 file paths from your solution
    Example: "In src/auth/middleware.py, src/auth/config.py, and
    tests/auth/test_middleware.py"

  Q_SEM_IDENT (combination — embedding + term match):
    Domain description that also names key symbols naturally.
    REQUIRED: mix domain concepts with 2-3 exact symbol names in a
    natural sentence.
    seeds: 2-3 of the symbols mentioned
    pins: []
    Example: "The rate limiting middleware, specifically
    RateLimiter.check_rate and the sliding window logic"

  Q_IDENT_NAV (combination — term match + explicit):
    Symbol names with file paths. This is how agents typically call
    recon after running map_repo.
    REQUIRED: 2+ symbol names AND 2+ file paths.
    seeds: 2-4 symbol names
    pins: 2-4 file paths
    Example: "RateLimiter and check_rate in src/auth/middleware.py,
    with tests in tests/auth/test_middleware.py"

  Q_FULL (combination — all signals):
    Natural developer query. No constraints. Use whatever information
    a developer who understood the task would write.
    seeds: 2-4 central symbol names
    pins: 2-4 key file paths
    Example: "I need to fix the rate limiting in
    src/auth/middleware.py — the RateLimiter.check_rate method uses
    a sliding window but doesn't handle the counter wrap edge case.
    The callers in the API handlers need updating too."

  NON-OK QUERIES (optional — only those that arise naturally):

  UNSAT (up to 2):
    A plausible query with a FACTUALLY WRONG assumption about this
    repo — references a class, module, or dependency that doesn't
    exist. Must sound believable.
    seeds: []  pins: []
    SKIP if you can't think of a natural one.

  BROAD (up to 2):
    Work requiring changes across 15+ files in 3+ unrelated
    directories. Must be plausible for this repo.
    seeds: []  pins: []
    SKIP if the task doesn't suggest a broader effort.

  AMBIG (up to 2):
    A query where this repo has 2+ subsystems that could each be
    the target, and the query doesn't specify which.
    seeds: []  pins: []
    SKIP if the repo lacks genuine ambiguity for this task.

  STEP 3 — VALIDATE (second pass, do this AFTER writing the JSON)
  Re-read your JSON file and verify:
    1. relevant_defs: Did you include every def you genuinely needed?
       Did you accidentally include defs you only glanced at? Open
       your diff — every changed function/method/class should appear.
       Then check: what did you READ to understand the change? Those
       should appear too. Remove anything you didn't actually use.
    2. queries: Does each query follow its REQUIRED/FORBIDDEN rules?
       Read each query aloud — would a different developer, given
       only that query (plus its seeds/pins), find the same code?
    3. seeds/pins: Are they what you'd know BEFORE solving, not after?
       If a seed or pin only became apparent during implementation,
       remove it.
    4. Completeness: Do you have exactly 8 OK queries? Are query_type
       values exact strings (Q_SEMANTIC, Q_LEXICAL, etc.)?

  Fix any issues found in validation before moving to the next task.

Work through every task sequentially. After all tasks, say
"ALL TASKS COMPLETE".
```

The `{name}` and `{repo_id}` placeholders are filled from the repo's
MD file metadata. The agent produces one JSON file per task under
`../../data/{repo_id}/ground_truth/`.

**Post-processing** (automated, after agent completes all tasks):

1. For each `(path, name, kind)` in `relevant_defs`: look up in the
   codeplane index → resolve `def_uid`, `start_line`, `end_line`.
2. If a triple doesn't match any indexed def, flag for review (typo,
   unindexed file, or hallucination).
3. Assemble `runs.jsonl`, `touched_objects.jsonl`, `queries.jsonl`
   from the per-task JSON files.
4. Summary report: N tasks, N defs total, N unmatched (should be <2%).

**Output:** `runs.jsonl`, `touched_objects.jsonl`, `queries.jsonl` under
`data/{repo_id}/ground_truth/`. This data is permanent — it never needs
re-collection.

#### Phase 3: Retrieval Signal Collection (Re-runnable)

A separate step, run against the indexed repo:

**3a. Raw signal collection:**

For **each query** in the ground truth, call `recon_raw_signals` with
the query's seeds and pins as separate arguments:

```
recon_raw_signals(query=query_text, seeds=seeds, pins=pins)
```

This returns the candidate pool with per-retriever signals for all
queries — including non-OK ones, because the gate model needs to learn
what each class looks like from the retrieval distribution.

Seeds and pins affect the candidate pool: seeded symbols enter via
the explicit harvester (`symbol_source="agent_seed"`), pinned file
paths inject all defs from those files (`symbol_source="pin"`). This
means the candidate pool differs per query type — which is exactly
what the ranker needs for learning.

**3b. Label joining and validation:**

Join raw signal output with ground-truth labels to compute
`label_relevant` per candidate (binary: def_uid in touched_objects
or not). For gate labels, validate that the retrieval distribution
is consistent with the authored label:

- **OK**: top candidates overlap with the relevant set.
- **UNSAT**: top candidates are low-confidence or irrelevant.
- **BROAD**: top candidates cover only a fraction of what the broad
  task would touch.
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

One row per relevant def per task. Only relevant defs appear — absence
means irrelevant. No edit/read distinction: recon answers “what context
is relevant,” not “what will be edited.”

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | str | Task run identifier |
| `def_uid` | str | DefFact stable identity (joined from index via path+name+kind) |
| `path` | str | File path |
| `kind` | str | DefFact kind |
| `name` | str | DefFact name |
| `start_line` | int | Span start (from index) |
| `end_line` | int | Span end (from index) |

### 7.3 `queries`

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | str | Task run identifier |
| `query_id` | str | Unique query identifier |
| `query_text` | str | Full query text |
| `query_type` | str | `Q_SEMANTIC` / `Q_LEXICAL` / `Q_IDENTIFIER` / `Q_STRUCTURAL` / `Q_NAVIGATIONAL` / `Q_SEM_IDENT` / `Q_IDENT_NAV` / `Q_FULL` / `UNSAT` / `BROAD` / `AMBIG` |
| `seeds` | list[str] | Symbol names passed as seeds (empty for isolation queries without seeds) |
| `pins` | list[str] | File paths passed as pins (empty for queries without pins) |
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
| `label_relevant` | bool | True if this def_uid is in touched_objects for this run |

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
3. Optimize NDCG with binary relevance labels.

### 8.2 Cutoff (no-leakage K-fold across all 50 repos)

1. K-fold split across repos (e.g. 5-fold, 10 repos per fold).
2. Per fold: train ranker on K−1 folds (40 repos), score held-out
   fold (10 repos) → out-of-fold ranked lists.
3. Per held-out query: compute $N^*(q) = \arg\max_N F_1(\text{top-}N,
   \text{ground truth})$.
4. Repeat across all folds → 7,500 `queries_cutoff` rows with no
   leakage (every query scored by a ranker that never trained on it).
5. Train cutoff regressor on the full 7,500-row aggregated dataset.

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
2. **After dataset generation**: Run evaluation on the full 50-repo
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
│          PER REPO (×30 ranker+gate, ×20 cutoff, ×15 eval) │
│                                                        │
│  One agent session per repo                            │
│    │                                                   │
│    ├─ Reads ranking/repos/{name}.md                    │
│    ├─ Repo cloned at ranking/clones/{clone}/           │
│    │                                                   │
│    ├─ FOR EACH TASK (30 per repo, 10N/10M/10W):       │
│    │    1. Solve: read code, make edits, verify        │
│    │    2. Reflect: write {task_id}.json with:         │
│    │       - edited_files (from diff)                  │
│    │       - read_necessary (agent judgment)           │
│    │       - 5 OK queries (semantic, lexical,          │
│    │         identifier, structural, navigational)     │
│    │       - up to 9 bad queries (3× UNSAT/BROAD/     │
│    │         AMBIG) [ranker+gate repos only]           │
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

---

## 13. ML Design Principles

### 13.1 No Artificial Caps in Retrievers

Every retriever returns its **full natural result set**. No `top_k`, no
`limit=`, no budget caps. The candidate pool is the raw union of all
retriever outputs. The ranker learns what matters from data — it does not
need pre-filtered inputs.

| Retriever | Current cap (to remove) | New behavior |
|-----------|------------------------|--------------|
| Embedding | `top_k=200` | All defs with cosine similarity > 0 |
| Term match | `limit=200` per term | All matching defs |
| Lexical | 50 files, `primary_terms[:16]` | All Tantivy hits using all terms (primary + secondary) |
| Graph | `_GRAPH_BUDGET=60` | Full 1-hop walk from all seeds |
| Explicit | 5 defs per mentioned file | All defs in mentioned files |
| Import | `_IMPORT_MAX_TOTAL=80` | All forward + reverse import edges |

### 13.2 No Hardcoded Scores — Raw Signals Only

Harvesters emit raw measurements, not pre-scored evidence. The ranker
learns the value of each signal from training data.

**Removed:**
- Graph edge weights (`_EDGE_WEIGHT_CALLEE=1.0`, `_EDGE_WEIGHT_CALLER=0.85`,
  `_EDGE_WEIGHT_SIBLING=0.7`)
- Graph quality formula (`quality = weight / seed_idx`)
- Explicit evidence scores (1.0, 0.7, 0.5 for different symbol sources)
- Import harvester scores (0.45, 0.40, 0.35, 0.35)
- IDF pre-computation in term match harvester
- Graph seed selection scoring (`evidence_axes * 2.0 + 2.0 if explicit`)

**Replaced with categorical features:**
- `graph_edge_type`: `callee` / `caller` / `sibling` (or `None`)
- `graph_seed_rank`: ordinal position of the seed in the merged pool
- `symbol_source`: `agent_seed` / `auto_seed` / `task_extracted` /
  `path_mention` (or `None`)
- `import_direction`: `forward` / `reverse` / `barrel` / `test_pair`
  (or `None`)
- `term_match_count`: raw number of query terms matching this def's name
- `term_total_matches`: how many defs matched each term (ranker can
  compute its own IDF if useful)

### 13.3 Per-Candidate Feature Set

```
# Identity
def_uid, path, kind, name, lexical_path

# Span
start_line, end_line, object_size_lines

# Path features
file_ext, parent_dir, path_depth

# Structural metadata from index
has_docstring, has_decorators, has_return_type
hub_score, is_test
signature_text        # raw signature string
namespace             # package/namespace (Java/C#/Go/etc)
nesting_depth         # depth in lexical_path (count of '.')
has_parent_scope      # whether nested inside another def

# Per-retriever raw signals (None if retriever didn't find this def)
emb_score             # raw cosine similarity
emb_rank              # rank within embedding results

term_match_count      # number of query terms that matched this def's name
term_total_matches    # total defs matched per term (IDF denominator)

lex_hit_count         # raw Tantivy hit count mapped to this def

graph_edge_type       # callee/caller/sibling or None
graph_seed_rank       # position of the seed in merged pool

symbol_source         # agent_seed/auto_seed/task_extracted/path_mention or None

import_direction      # forward/reverse/barrel/test_pair or None

retriever_hits        # count of retrievers that found this def (0-6)
```

### 13.4 Binary Relevance Output

The system is a **context retrieval** tool. The agent decides what to edit.
The system surfaces the full working set — everything relevant to the task.

**Ground truth label:** binary `relevant` (1) vs `irrelevant` (0). A def
is relevant if it appears in `relevant_defs` for this task. There is no
edit/read distinction — recon answers "what context is needed," not "what
will be edited."

**Training objective:** LambdaMART with binary gain.

**Output to agent:** ranked defs grouped by file. Single list, no
categories.

### 13.5 Non-Code File Handling

Non-code files (YAML, TOML, Markdown, Makefile, dotfiles) only have
**file-level embeddings**. Their synthetic defs (`pair`, `key`, `table`,
`heading`, `target`) enter the candidate pool via file-embedding expansion
— the file's cosine similarity is assigned to each of its defs.

After ranking, defs group back to files. Non-code defs group to their
parent non-code files. No parallel retrieval channel. The ranker sees
these defs alongside code defs — it learns relevance patterns from
`kind=pair/key/heading/etc` and absent `signature_text`.

### 13.6 Tier Assignment from Ranker Scores

After cutoff returns N defs, group by parent file:

- File contains a def in **top third** of returned set → `FULL_FILE`
- File contains a def in **middle third** → `SCAFFOLD`
- File contains a def in **bottom third** → `SUMMARY`

Non-code files tiered identically. No special path.

### 13.7 Post-Ranking Test Co-Retrieval

After ranker + cutoff produce the working set:

1. For each source file in the set, find test files that import it
   (existing import-graph edges).
2. Add those tests at the source file's tier or one below.

This is structural (real import edges), not heuristic. No scoring.

### 13.8 Seeds and Pins

- **Injection:** keep. Seeds/pins add candidates to the pool before
  ranking.
- **Score boosting:** removed. No `pin_floor`, no `anchor_floor`. The
  ranker scores everything equally.
- **As features:** `symbol_source=agent_seed` tells the ranker how the
  candidate entered the pool.
- **Gate signal:** gate features include `has_agent_seeds` and
  `agent_seed_count`.

### 13.9 Graph Seed Selection

Seeds for graph walk = all candidates found by ≥2 retrievers, plus all
explicit mentions. No scoring formula, no cap. If the resulting seed
set is large, batch the DB queries.

### 13.10 Training and Shipment Sequence

1. Data collection (all 50 repos, all tasks)
2. Gate training (all 50 repos' queries with gate labels)
3. Gate ships (wired into heuristic pipeline)
4. Ranker training (all 50 repos, OK queries, NDCG with graded labels)
5. Ranker NDCG validated on held-out data
6. Cutoff training (K-fold across all 50 repos, 7,500 rows)
7. Full pipeline ships (gate + ranker + cutoff replaces heuristic recon)

### 13.11 Evaluation Metrics

Report **per query type** (Q_SEMANTIC, Q_LEXICAL, Q_IDENTIFIER,
Q_STRUCTURAL, Q_NAVIGATIONAL):

- F1 of returned set vs ground truth (binary relevant)
- Precision, recall
- NDCG of ranked list
- Empty-result rate, returned-set size, noise ratio

### 13.12 Dead Code Removal

Remove from codebase before training data collection:

- `DefFact.qualified_name` column (always NULL, never populated)
- All hardcoded evidence scores in harvesters
- `primary_terms[:16]` restriction in lexical harvester
- IDF pre-computation in term match harvester
- Graph edge weight constants and quality formula
- Graph budget cap (`_GRAPH_BUDGET`)
- All artificial `limit=` / `top_k=` parameters on harvester queries
- Import harvester hardcoded scores
- Graph seed scoring formula (`evidence_axes * 2.0 + ...`)
