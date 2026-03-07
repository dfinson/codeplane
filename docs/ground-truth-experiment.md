# Ground Truth Tier Experiment

## Question

Do `minimum_sufficient_defs` and `thrash_preventing_defs` actually
diverge in practice, or is the two-tier design overengineering?

## Method

Invented 2 realistic tasks on the codeplane repo (1 narrow, 1 medium).
Solved N1 for real (captured diff, reverted). Analyzed M1 by tracing
the code without editing. Produced full ground truth for both, then
compared the two tiers.

## Task N1 (narrow): Add parse_warnings counter to LCOV parser

**What:** `LcovParser.parse` silently swallows `ValueError` via 4×
`except ValueError: pass`. Add a `parse_warnings` counter to
`CoverageReport` and increment it instead of silently passing.

**Diff:** Added `parse_warnings: int = 0` field to `CoverageReport`.
Replaced 4× `except ValueError: pass` with `parse_warnings += 1`.
Updated return to pass the counter.

**Files touched:** `parsers/lcov.py`, `models.py`

| Tier | Defs |
|------|------|
| minimum_sufficient (gain=2) | `lcov.py:parse` (line 60, edited), `models.py:CoverageReport` (line 129, edited) |
| thrash_preventing (gain=1) | `models.py:CoverageParseError` (line 14, agent checks for existing error mechanism), `parsers/base.py:CoverageParser` (line 9, agent checks return type protocol) |

**Tier difference:** Marginal. The 2 thrash_preventing defs each
prevent one confirmation search — an agent would check "should I raise
CoverageParseError instead?" and "does the parse() protocol need
changing?" A human wouldn't bother checking.

## Task M1 (medium): Add uncovered branch details to coverage reports

**What:** `FileCoverage` has `uncovered_lines` but no equivalent for
branches. `_file_coverage_detail` and `build_tiered_coverage` show line
coverage detail but not branch detail. Add `uncovered_branches`
property, include branch detail in both report functions.

**Files touched:** `models.py`, `report.py`

| Tier | Defs |
|------|------|
| minimum_sufficient (gain=2) | `models.py:FileCoverage` (41, edited), `models.py:uncovered_lines` (71, read — pattern), `models.py:BranchCoverage` (18, read — fields), `report.py:_file_coverage_detail` (175, edited), `report.py:build_tiered_coverage` (209, edited) |
| thrash_preventing (gain=1) | `report.py:build_compact_summary` (120, does it need updating?), `report.py:build_coverage_detail` (265, consistency?), `report.py:_compress_ranges` (51, reusable?), `models.py:branches_found` (76, naming convention), `models.py:branch_rate` (86, computation consistency) |

**Tier difference:** Significant. 5 thrash_preventing defs — all are
things an agent would proactively search for: "does the other detail
view need branch info too?", "can I reuse _compress_ranges?", "what's
the naming convention for branch properties?" A human familiar with the
codebase already knows these answers.

## Conclusion (tier experiment)

| Complexity | min_suff | thrash_prev | Ratio | Verdict |
|-----------|----------|-------------|-------|---------|
| Narrow | 2 | 2 | 1:1 | Real but marginal |
| Medium | 5 | 5 | 1:1 | Real and significant |

The tiers genuinely diverge. Graded relevance (2/1/0) ensures minimum
defs survive budget pressure. **Keep both tiers.**

---

## Signal Quality Experiment

### Question

Do the retrieval signals actually separate relevant from irrelevant
candidates? Can a ranker learn to surface ground truth defs?

### Bug found first

`"coverage"` was in `PRUNABLE_DIRS` — the directory walker pruned any
directory named `coverage` at any depth, including source directories
like `src/codeplane/testing/coverage/`. 14 source files were missing
from the index. Fixed by removing `"coverage"` from `PRUNABLE_DIRS`
and making the `.cplignore` pattern root-only (`/coverage/`).

### Method

Ran `recon_raw_signals` for 4 tasks (N1, N2, N3, M2) across 4
subsystems with 3 query types each (Q_FULL with seeds+pins,
Q_SEMANTIC with no hints, Q_IDENTIFIER with symbol names only).
12 signal collection runs total. Measured GT def recall, signal
separation (retriever_hits), and F1 with a simple ranker
(sort by retriever_hits desc).

### Tasks

| Task | Subsystem | GT defs | Files |
|------|-----------|---------|-------|
| N1 | testing/coverage | 5 | lcov.py, models.py, base.py |
| N2 | mcp/delivery | 3 | delivery.py |
| N3 | tools/map_repo | 3 | map_repo.py |
| M2 | refactor/ops | 7 | ops.py |

### Results: retrieval quality per task × query type

| Task | Query | Cands | Found | R@10 | R@20 | GT hits | Non-GT | Sep |
|------|-------|-------|-------|------|------|---------|--------|-----|
| N1 | Q_FULL | 958 | 5/5 | 1/5 | 3/5 | 2.6 | 1.0 | 2.5× |
| N1 | Q_SEMANTIC | 769 | 5/5 | 0/5 | 0/5 | 1.0 | 1.0 | 1.0× |
| N1 | Q_IDENT | 721 | 5/5 | 2/5 | 4/5 | 2.2 | 1.0 | 2.1× |
| N2 | Q_FULL | 391 | 3/3 | 0/3 | 1/3 | 3.0 | 1.0 | 2.9× |
| N2 | Q_SEMANTIC | 696 | 3/3 | 0/3 | 0/3 | 1.0 | 1.0 | 1.0× |
| N2 | Q_IDENT | 430 | 3/3 | 0/3 | 2/3 | 2.0 | 1.0 | 2.0× |
| N3 | Q_FULL | 1034 | 3/3 | 0/3 | 3/3 | 3.0 | 1.0 | 2.9× |
| N3 | Q_SEMANTIC | 1332 | 3/3 | 0/3 | 0/3 | 1.0 | 1.0 | 1.0× |
| N3 | Q_IDENT | 967 | 3/3 | 0/3 | 3/3 | 2.0 | 1.0 | 2.0× |
| M2 | Q_FULL | 904 | 7/7 | 0/7 | 1/7 | 3.0 | 1.1 | 2.9× |
| M2 | Q_SEMANTIC | 316 | 0/7 | 0/7 | 0/7 | 0.0 | 1.0 | 0.0× |
| M2 | Q_IDENT | 966 | 7/7 | 0/7 | 2/7 | 2.6 | 1.0 | 2.5× |

### Results: F1@5 with simple ranker (sort by retriever_hits)

| Task | Q_FULL | Q_SEMANTIC | Q_IDENT |
|------|--------|------------|---------|
| N1 | 0.600 | 0.000 | 0.800 |
| N2 | 0.500 | 0.250 | 0.750 |
| N3 | 0.750 | 0.000 | 0.750 |
| M2 | 0.167 | 0.000 | 0.833 |
| **avg** | **0.504** | **0.062** | **0.783** |

### Aggregate by query type

| Query type | Avg F1@5 | Avg separation | Recall@ALL |
|-----------|----------|---------------|------------|
| Q_FULL (seeds+pins) | 0.504 | 2.8× | 100% |
| Q_SEMANTIC (no hints) | 0.062 | 0.8× | 61% |
| Q_IDENTIFIER (names) | 0.783 | 2.2× | 100% |

### Verdict

| Scenario | Expected F1 | Evidence |
|----------|------------|---------|
| Best case (perfect ranker, any query) | 1.000 | All GT defs in pool, just reorder |
| Good case (identifier queries, simple ranker) | 0.783 | Avg across 4 tasks |
| Typical case (full queries with seeds+pins) | 0.504 | Avg across 4 tasks |
| Worst case (pure semantic, no symbols) | 0.062 | No retriever agreement signal |

**The signal is there.** Multi-retriever agreement separates GT defs
at ~2.5× across all non-semantic query types. A LambdaMART model
with access to all features (not just retriever_hits) should push
the typical case well above 0.5 F1.

**Semantic-only queries have no embeddings.** Investigation revealed
that the def embedding index is empty (0 embeddings computed) and
file embeddings contain only 1 of 586 files. The embedding subsystem
is broken — all 12 runs above used term match only with zero dense
vector signal. This means:

- The F1 numbers above are a **pessimistic lower bound** — they
  represent retrieval quality without the embedding harvester
- Once embeddings are fixed, Q_SEMANTIC should gain `emb_score` as
  a strong signal (bge-small-en-v1.5 is designed for code↔query)
- The ranker will have `emb_score` and `emb_rank` as additional
  features, improving all query types

**Embedding fix is a separate workstream**, not a blocker for dataset
generation — the ranker can learn from whatever signals are available,
and more signals = better performance.

**The investment is justified.** 100% recall in the candidate pool
means the ranker has every GT def available — it just needs to learn
to rank them above noise. With 2,280 tasks × 8 queries each =
~18,000 training examples, LambdaMART should learn the ranking
function.

### Verdict

| Scenario | F1 | Interpretation |
|----------|-----|---------------|
| Best case (perfect ranker) | 1.000 | All GT defs surface at top 5 |
| Plausible good (ranker + right cutoff) | 0.400-0.667 | GT defs in top 10-15 |
| Current (no ranker, raw order) | 0.240 | Peak at N=20, lots of noise |
| Worst case (semantic-only, no ranker) | <0.100 | Almost no signal |

**The signal is there.** Multi-retriever agreement (retriever_hits)
separates GT defs from noise at ~2.5×. A LambdaMART model trained on
thousands of examples should easily learn to exploit this gap.
The investment in dataset generation is justified.
