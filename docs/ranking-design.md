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

**Non-OK tiers** (up to 2 each, optional — skip if forced):

| Tier | Definition |
|------|------------|
| **UNSAT** | Query makes factually wrong assumptions about the repo |
| **BROAD** | Work spanning 15+ files in 3+ unrelated directories |
| **AMBIG** | 2+ subsystems could be the target; query doesn't specify which |

Total per task: 8 required OK + 0–6 optional non-OK = 8–14 queries.

### 1.4 Gate Taxonomy

Four labels, defined as properties of the **(query, repo) pair**:

| Label | Definition |
|-------|-----------|
| **OK** | The query maps to a specific, bounded neighborhood of semantic objects. |
| **UNSAT** | The query makes factually wrong assumptions about the repo. |
| **BROAD** | The touched set is structurally dispersed — no ranked list with a reasonable cutoff achieves acceptable precision and recall. |
| **AMBIG** | Multiple disjoint neighborhoods could plausibly be the target. |

### 1.5 Relevance Model

Binary. A def is either relevant to the task or it isn't. No edit/read
distinction — recon answers "what context is needed," not "what will be
edited." The agent decides what to edit.

---

## 2. Models

### 2.1 Object Ranker (Model 1)

**Goal:** Score $P(\text{relevant} \mid q, o)$ for each candidate DefFact.

**Base model:** LightGBM LambdaMART. Binary relevance gain. Grouped by
`(run_id, query_id)`. Only OK-labeled queries.

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

One unified repo set: **50 repos**, 10 languages × 5 repos per language
(small/medium/large scale diversity). All 50 repos train all 3 models.
Cutoff uses K-fold out-of-fold ranker predictions for no-leakage training.

**Total:** 50 repos, 1,500 tasks, 12,000–21,000 queries.

### 4.2 Task Generation

30 tasks per repo (10 narrow / 10 medium / 10 wide). Each task is a
natural-language description of work — no code, no diffs, no hints.

### 4.3 Data Collection Pipeline

Two phases:
- **Ground truth** (Phase 1+2): run once per repo, permanent.
- **Retrieval signals** (Phase 3): re-run when harvesters change.

#### Phase 1+2: Solve + Reflect

One agent session per repo.

**Agent prompt:**

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

  Q_LEXICAL (isolation — full-text only):
    Use strings that appear LITERALLY in the source code.
    REQUIRED: at least one phrase in quotes that grep would find —
    an error message, log string, comment, docstring, or string literal.
    FORBIDDEN: symbol names that don't appear as literal strings.
    seeds: []  pins: []

  Q_IDENTIFIER (isolation — term match only):
    List exact symbol names from the code you touched.
    REQUIRED: at least 3 symbol names, comma-separated.
    FORBIDDEN: file paths, English descriptions, relationship words.
    seeds: []  pins: []

  Q_STRUCTURAL (isolation — graph only):
    Describe the code through structural relationships.
    REQUIRED: at least one concrete symbol AND a relationship word
    (callers, callees, subclasses, implementors, siblings, imports).
    seeds: 1-2 (the entry points for graph traversal)
    pins: []

  Q_NAVIGATIONAL (isolation — explicit/path only):
    Use explicit file paths and directory locations.
    REQUIRED: at least 2 file paths from the files you touched.
    FORBIDDEN: domain descriptions, relationship words.
    seeds: []
    pins: 2-4 file paths from your solution

  Q_SEM_IDENT (combination — embedding + term match):
    Domain description that also names key symbols naturally.
    REQUIRED: mix domain concepts with 2-3 exact symbol names.
    seeds: 2-3 of the symbols mentioned
    pins: []

  Q_IDENT_NAV (combination — term match + explicit):
    Symbol names with file paths.
    REQUIRED: 2+ symbol names AND 2+ file paths.
    seeds: 2-4 symbol names
    pins: 2-4 file paths

  Q_FULL (combination — all signals):
    Natural developer query. No constraints.
    seeds: 2-4 central symbol names
    pins: 2-4 key file paths

  NON-OK QUERIES (optional — only those that arise naturally):

  UNSAT (up to 2): Factually wrong assumption. seeds: [] pins: []
  BROAD (up to 2): 15+ files, 3+ directories. seeds: [] pins: []
  AMBIG (up to 2): 2+ possible targets. seeds: [] pins: []
  SKIP any that feel forced.

  STEP 3 — VALIDATE (second pass, AFTER writing the JSON)
  Re-read your JSON file and verify:
    1. relevant_defs: Every def you genuinely needed? Nothing you
       only glanced at? Check your diff — every changed def should
       appear. Then: what did you READ to make the change? Those too.
    2. queries: Each follows its REQUIRED/FORBIDDEN rules?
    3. seeds/pins: What you'd know BEFORE solving, not after?
    4. Completeness: Exactly 8 OK queries? Exact query_type strings?

  Fix any issues before moving to the next task.

Work through every task sequentially. After all tasks, say
"ALL TASKS COMPLETE".
```

**Post-processing** (automated):

1. For each `(path, name, kind)` in `relevant_defs`: look up in
   codeplane index → resolve `def_uid`, `start_line`, `end_line`.
2. If no match: flag for review (should be <2%).
3. Assemble `runs.jsonl`, `touched_objects.jsonl`, `queries.jsonl`.

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

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | str | Task run identifier |
| `def_uid` | str | DefFact stable identity |
| `path` | str | File path |
| `kind` | str | DefFact kind |
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

### 5.4 `candidates_rank`

One group per `(run_id, query_id)`. Per-candidate features from
`recon_raw_signals` output + binary relevance label.

### 5.5 `queries_cutoff`

One row per OK query. Score distribution features from out-of-fold
ranker output + $N^*$ target.

### 5.6 `queries_gate`

One row per query (all types). Retrieval distribution features +
gate label.

---

## 6. Training Procedures

### 6.1 Ranker

1. Filter to OK-labeled queries.
2. Train LightGBM LambdaMART grouped by `(run_id, query_id)`.
3. Binary relevance gain.

### 6.2 Cutoff (no-leakage K-fold across all 50 repos)

1. 5-fold split across repos (10 repos per fold).
2. Per fold: train ranker on 40 repos, score held-out 10.
3. Compute $N^*$ per held-out query.
4. Aggregate → 7,500 rows.
5. Train cutoff regressor.

### 6.3 Gate

1. All query types (OK + UNSAT + BROAD + AMBIG).
2. Retrieval distribution features from candidate pools.
3. LightGBM multiclass, cross-entropy.

### 6.4 Shipment Sequence

1. Data collection (all 50 repos)
2. Gate training → gate ships (wired into heuristic pipeline)
3. Ranker training → validate NDCG on held-out data
4. Cutoff training (depends on validated ranker)
5. Full pipeline ships (gate + ranker + cutoff replaces heuristic recon)

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
├── src/cpl_ranking/
│   ├── collector.py, collect_signals.py
│   ├── train_ranker.py, train_cutoff.py, train_gate.py
│   └── schema.py
└── data/{repo_id}/             # Ground truth + signals (gitignored)

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
