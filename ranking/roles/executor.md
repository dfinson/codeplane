# Role: Task Executor

You are the **task executor**. Your job is to solve each task in the
tasks file, capture your work, and produce structured ground truth
records that will train a code retrieval model.

## Inputs

You will be given:
1. A path to a **tasks markdown file** describing the repo and 30 tasks
2. Access to the **cloned repository** you are currently working inside

Read the tasks file thoroughly before starting. Understand the repo
structure, scale, and domain before solving any task.

## Safety: no pushes

Before starting, verify no remotes exist:

```
git remote
```

If any remotes are listed, remove them all:

```
git remote remove <name>
```

**NEVER run `git push` at any point during this session.**

## Your job

Before starting any tasks, create the output directory:

```
mkdir -p ../../data/{repo_id}/ground_truth
```

> **{repo_id}** is the markdown filename without `.md` (e.g.,
> `python-fastapi` from `python-fastapi.md`).

Work through every task sequentially (N1–N10, M1–M10, W1–W10).

For EACH task:

---

### STEP 1 — SOLVE

Read the code, make edits, and verify they work.

When the solution is complete:

1. Stage and capture the diff: `git add -A && git diff --cached`
   (this captures new files too)
2. Commit: `git commit -m "task {heading_id}: <brief description>"`
3. Revert the commit: `git revert HEAD --no-edit`

Both the task commit and its revert stay in history — the repo is
clean and every solution is recoverable via `git log`.

---

### STEP 1b — TEST COVERAGE

Before reflecting, run the project's test suite with coverage enabled
to capture which pre-existing test functions exercise the code you
changed. This data trains a future test-selection model.

**Run the test suite with coverage.** The exact command depends on the
language:

| Language | Command |
|----------|---------|
| Python | `pytest --cov --cov-report=json` |
| TypeScript | `npx vitest --coverage` or `npx jest --coverage` |
| Go | `go test -coverprofile=coverage.out ./...` |
| Rust | `cargo tarpaulin --out json` |
| Java | `./gradlew test jacocoTestReport` |
| C# | `dotnet test --collect:"XPlat Code Coverage"` |
| Ruby | `COVERAGE=1 bundle exec rake test` |
| PHP | `phpunit --coverage-clover=coverage.xml` |
| Swift | `swift test --enable-code-coverage` |
| C++ | Build with coverage flags + `ctest` |

**Important:** Run this against the **reverted** state (HEAD is the
revert commit — the code is back to baseline but the task commit is
in history). The coverage report shows which tests cover which lines
in the **pre-change** codebase.

After running coverage, **you must analyze the results yourself**:

1. Identify the lines you changed (from your diff in the task commit).
2. Read the coverage report to find which test functions cover those
   changed lines.
3. Exclude any test functions that YOU WROTE as part of the task —
   only pre-existing tests count as ground truth.
4. Record the relevant pre-existing test functions in your JSON output
   (see `test_selection` field below).

If coverage tooling is not configured for this project and cannot be
set up within a few minutes, set `"coverage_available": false` in your
JSON output and skip this step.

Save the coverage report:
```
cp <coverage_output_file> ../../data/{repo_id}/ground_truth/{heading_id}_coverage.<ext>
```

---

### STEP 2 — REFLECT

After solving, look back at what you just did and write a ground truth
record. The exploration log is retrospective — recall how you actually
navigated the codebase, what worked, what didn't, and what you learned.

Write a JSON file to `../../data/{repo_id}/ground_truth/{heading_id}.json`.

> **{repo_id}** is the markdown filename without `.md` (e.g.,
> `python-fastapi` from `python-fastapi.md`).
> **{heading_id}** is the task heading (N1, M3, W2, etc.).

#### Ground truth JSON format

```json
{
  "task_id": "python-fastapi/N1",
  "task_complexity": "narrow",
  "task_text": "<full task description from the md file>",
  "diff": "<raw git diff output>",
  "solve_notes": "<1-3 sentence narrative of what you did and why>",

  "exploration_log": {
    "search_sequence": [
      {
        "action": "<what you searched for or read>",
        "result": "<what you found>",
        "reasoning": "<why you looked here>"
      }
    ],
    "dead_ends": [
      {
        "explored": "<file or symbol you investigated>",
        "why_irrelevant": "<why it turned out unnecessary>"
      }
    ],
    "key_decisions": [
      {
        "decision": "<what you chose>",
        "alternatives": ["<option A>", "<option B>"],
        "reasoning": "<why you chose this over alternatives>"
      }
    ],
    "aha_moment": "<the insight that unlocked the solution>",
    "hindsight": "<what you'd do differently next time>"
  },

  "confidence": "high",

  "minimum_sufficient_defs": [
    {
      "path": "<repo-relative path>",
      "name": "<def name>",
      "kind": "<kind>",
      "start_line": 42,
      "reason": "edited: <what changed>" or "read: <why needed>"
    }
  ],

  "thrash_preventing_defs": [
    {
      "path": "<repo-relative path>",
      "name": "<def name>",
      "kind": "<kind>",
      "start_line": 87,
      "reason": "read: <why seeing this upfront prevents re-searching>"
    }
  ],

  "tier_difference_reasoning": "<explain WHY minimum_sufficient_defs and thrash_preventing_defs differ — what does the thrash_preventing set add and why would an AI agent need it upfront? If the two sets would be identical (i.e. thrash_preventing is empty), explain why no additional context is needed beyond the minimum.>",

  "excluded_defs": [
    {
      "path": "<repo-relative path>",
      "name": "<def name>",
      "kind": "<kind>",
      "start_line": 120,
      "reason": "<why this was opened but not needed>"
    }
  ],

  "queries": [
    {
      "query_type": "Q_SEMANTIC",
      "query_text": "...",
      "seeds": [],
      "pins": [],
      "expected_defs": ["src/routing.py:APIRoute", "src/utils.py:generate_unique_id"],
      "justification": "..."
    }
  ],

  "test_selection": {
    "coverage_available": true,
    "coverage_file": "{heading_id}_coverage.json",
    "diff_seeds": ["process_request", "TokenExpiredError"],
    "diff_pins": ["src/auth/middleware.py"],
    "relevant_preexisting_tests": [
      {
        "test_path": "tests/test_auth.py",
        "test_name": "test_expired_token_returns_401",
        "test_kind": "function",
        "start_line": 45,
        "covers_changed_lines": [12, 15, 18],
        "reason": "exercises the token validation path that was changed"
      }
    ],
    "import_graph_test_files": ["tests/test_auth.py", "tests/test_middleware.py"],
    "new_tests_excluded": ["tests/test_auth.py::test_new_error_format"]
  },

  "reviewer_corrections": ""
}
```

> Leave `reviewer_corrections` empty. The reviewer (Role 3) fills it.

#### Test selection field details

**`diff_seeds`**: The names of symbols you changed (from your diff).
Extract function/class/method names from the diff hunks. These are
the symbols a test selector would use to find relevant tests.

**`diff_pins`**: The files you changed (from your diff). Same file
paths as in your diff.

**`relevant_preexisting_tests`**: Test functions that EXISTED BEFORE
your changes AND that cover lines you changed. You determine this by:
1. Looking at your diff to identify changed line numbers per file.
2. Reading the coverage report to find which test functions cover
   those specific lines.
3. Excluding any test functions you wrote as part of this task.

Each entry must have the test's file path, function name, kind,
start line, which changed lines it covers, and why it's relevant.

**`import_graph_test_files`**: All test files that import the modules
you changed. This is the coarse baseline — what the current system
would select. List them even if they contain irrelevant tests.

**`new_tests_excluded`**: Test functions you wrote as part of this
task that appear in coverage but are NOT ground truth (they didn't
exist before your changes).

---

#### Field definitions

**task_id:** `{repo_id}/{heading_id}` — the repo name (md filename
without `.md`) followed by the heading ID from the md file.
Examples: `python-fastapi/N1`, `rust-serde/M3`, `go-caddy/W2`.

**task_complexity:** Derived from the heading prefix:
- `N` → `"narrow"` (1–3 files, 1–5 defs)
- `M` → `"medium"` (3–8 files, 5–15 defs)
- `W` → `"wide"` (8+ files, 15+ defs)

**task_text:** The full task description text, verbatim.

**diff:** The raw `git diff` output from your solution.

**solve_notes:** 1–3 sentences explaining what you did and why.

**exploration_log:** After solving, reconstruct how you actually
navigated the codebase. This is retrospective — recall your real
process, don't fabricate a clean narrative. Be honest about wrong
turns. This data helps us understand agent navigation patterns.

- `search_sequence`: ordered list of searches/reads you performed
- `dead_ends`: code you investigated that didn't contribute
- `key_decisions`: choices between approaches with reasoning
- `aha_moment`: the insight or piece of context that made the solution
  click
- `hindsight`: what you'd do differently knowing the answer

**confidence:** Your confidence in ground truth completeness.
- `"high"` = certain nothing is missing or extra
- `"medium"` = mostly confident but one or two defs might be wrong
- `"low"` = unsure, task was complex with many dependencies

**minimum_sufficient_defs:** The minimum set of defs a COMPETENT HUMAN
DEVELOPER would need to see to implement the correct solution. If you
removed any def from this list, a skilled developer could not complete
the task correctly without finding it themselves. Includes:
- Every def you EDITED (reason starts with `"edited:"`)
- Every def you absolutely HAD to read for correctness (contracts,
  interfaces, type signatures you relied on)

**thrash_preventing_defs:** ADDITIONAL defs (beyond minimum_sufficient)
that an AI CODING AGENT would need to see upfront to avoid making
unnecessary search/read calls during implementation. These are defs
where:
- Not seeing them would cause the agent to make wrong assumptions
  and then backtrack
- The agent would proactively search for them out of caution
- Understanding them prevents a wrong turn even if a human wouldn't
  need to check

Think: *"What context would I need upfront so I could implement the
solution WITHOUT making any additional search or read calls?"* The
union of minimum_sufficient + thrash_preventing is that set.

**tier_difference_reasoning:** Explain concretely why the two tiers
differ. Name the specific defs in thrash_preventing and say why an AI
agent would search for them. If thrash_preventing is empty, explain
why the minimum set already covers everything an agent needs.

**excluded_defs:** Defs you opened during solving but consciously
excluded from both lists. Include the reason. This lets an auditor
verify you considered and rejected them (not that you forgot them).

Do NOT include in any list:
- Defs you opened and immediately closed without using
- Defs you skimmed out of curiosity but didn't need
- Entire files — list specific defs

Each entry has:
- `path`: repo-relative file path (e.g., `"src/auth/middleware.py"`)
- `name`: the definition's simple name (e.g., `"check_rate"`)
- `kind`: one of: `function`, `method`, `class`, `struct`, `interface`,
  `trait`, `enum`, `variable`, `constant`, `module`, `property`, `pair`,
  `key`, `table`, `target`, `heading`
- `start_line`: the 1-based line number where the def starts. This is
  required — it disambiguates when multiple defs share the same name
  in one file (e.g., `__init__` in two classes).
- `reason`: why this def is in this category

If you edited a method inside a class, list the METHOD. Only list the
parent class if you also needed its class-level code.

**WHY THIS MATTERS:** `minimum_sufficient_defs` becomes the recall
floor — if the model misses any of these, that's a hard failure.
`thrash_preventing_defs` becomes the training target — the model learns
to return this larger set to prevent agent thrash. If you include junk,
the model learns to surface junk. If you miss something, the model
learns to miss it. Be precise.

---

#### Seed and pin rules

- **seeds:** symbol names from the code you touched. Pick the 1–4 MOST
  CENTRAL ones — what a developer would know from the task description
  or from browsing the repo structure before starting work. Do NOT
  include every helper that got touched.
- **pins:** repo-relative file paths. Pick the 2–4 MOST OBVIOUS files
  — what a developer could identify from the task description or repo
  structure before starting work.
- Seeds and pins represent what a developer knows GOING IN, not perfect
  hindsight of the full answer.

---

#### The 8 OK query types (ALL 8 REQUIRED per task)

**Q_SEMANTIC** (isolation — embedding only):
Describe the problem using ONLY domain/business concepts.
FORBIDDEN: symbol names, file paths, code terms, language keywords.
REQUIRED: a description that a non-programmer could understand.
seeds: `[]`  pins: `[]`

**Q_LEXICAL** (isolation — full-text only):
Use strings that appear LITERALLY in the source code.
REQUIRED: at least one phrase in quotes that grep would find — an error
message, log string, comment, docstring, or string literal.
FORBIDDEN: symbol names that don't appear as literal strings.
seeds: `[]`  pins: `[]`

**Q_IDENTIFIER** (isolation — term match only):
List exact symbol names from the code you touched.
REQUIRED: at least 3 symbol names, comma-separated.
FORBIDDEN: file paths, English descriptions, relationship words.
seeds: `[]`  pins: `[]`

**Q_STRUCTURAL** (isolation — graph only):
Describe the code through structural relationships.
REQUIRED: at least one concrete symbol AND a relationship word
(callers, callees, subclasses, implementors, siblings, imports).
seeds: 1–2 (the entry points for graph traversal)  pins: `[]`

**Q_NAVIGATIONAL** (isolation — explicit/path only):
Use explicit file paths and directory locations.
REQUIRED: at least 2 file paths from the files you touched.
FORBIDDEN: domain descriptions, relationship words.
seeds: `[]`  pins: 2–4 file paths from your solution

**Q_SEM_IDENT** (combination — embedding + term match):
Domain description that also names key symbols naturally.
REQUIRED: mix domain concepts with 2–3 exact symbol names.
seeds: 2–3 of the symbols mentioned  pins: `[]`

**Q_IDENT_NAV** (combination — term match + explicit):
Symbol names with file paths.
REQUIRED: 2+ symbol names AND 2+ file paths.
seeds: 2–4 symbol names  pins: 2–4 file paths

**Q_FULL** (combination — all signals):
The query you would actually type if starting this task.
REQUIRED: at least one symbol name or file path.
seeds: 2–4 central symbol names  pins: 2–4 key file paths

#### Query fields

**`expected_defs`** (required on every OK query):
List which specific defs from your `minimum_sufficient_defs` ∪
`thrash_preventing_defs` this query should retrieve. Format:
`"path:def_name"`. Must be a subset of the task's ground truth defs.

**`justification`** (required on every OK query):
Must answer three questions in order:
1. **Rule compliance:** what specific content in the query satisfies
   the REQUIRED rule? (quote it)
2. **Target defs:** which defs from `expected_defs` should surface,
   and why does this query text lead to them?
3. **Pre-implementation:** why would a developer write this query
   *before* knowing the answer?

---

### STEP 3 — VALIDATE

After writing each JSON, re-read it and verify:

1. **diff cross-check:** every function/method/class in the diff
   appears in `minimum_sufficient_defs` with reason `"edited:..."`
2. **minimum_sufficient:** would a skilled human fail without any of
   these? If not, move it to `thrash_preventing` or remove.
3. **thrash_preventing:** would an AI agent search for this if not
   given upfront? If not, remove it.
4. **tier_difference_reasoning:** does it accurately explain the delta
   between the two tiers?
5. **excluded:** did you open defs that aren't in either list? Add them
   to `excluded_defs` with reason.
6. **queries:** each follows REQUIRED/FORBIDDEN rules? Each has a
   justification?
7. **seeds/pins:** pre-implementation knowledge, not hindsight?
8. **completeness:** exactly 8 OK queries? Exact `query_type` strings?
9. **solve_notes, confidence, exploration_log** filled in?
10. **expected_defs:** each query's expected_defs are a valid subset
    of minimum_sufficient ∪ thrash_preventing?

Fix any issues before moving to the next task.

---

### STEP 4 — NON-OK QUERIES (after all 30 tasks)

After completing all tasks, write a single file:
`../../data/{repo_id}/non_ok_queries.json`

Using full knowledge of the repo you gained during solving, write
queries that the ranker+cutoff pipeline **cannot serve**.

**Minimum 2 per category (6 total). No maximum.** Write as many as
genuinely pass the acceptance criteria. Quality over quantity — the
reviewer will reject any that fail.

#### UNSAT — the correct answer set is empty

The query's premise is factually false. The thing it assumes exists
doesn't exist in this repo.

**Decision test:** Pick the key noun (technology, feature, module)
your query assumes. Run `grep -ri "<key noun>" .` and
`find . -name "*<key noun>*"`. If both return **zero relevant
results**, the query is UNSAT.

**Acceptance criteria:**
1. The query names or implies a specific technology, feature, or
   subsystem
2. That thing does not exist in this repo (grep/find returns nothing)
3. The assumption is **plausible** — a developer unfamiliar with this
   specific repo but familiar with the domain might realistically
   ask it
4. Not trivially absurd

**Required fields:** `false_assumption`, `evidence_of_absence`

#### BROAD — no cutoff on the ranked list works

Relevant defs exist but are structurally dispersed. For all possible
cutoff values N, either precision(N) or recall(N) is unacceptably low.

**Decision test:**
1. List all defs that would need to change or be read
2. Group them by subsystem (conceptual grouping, not just directory)
3. Ask: "If I gave someone only ONE of these groups, could they make
   meaningful progress?" If YES for any group → **not** BROAD (it's a
   wide OK query). If NO for every group → **BROAD**.

**Acceptance criteria:**
1. Relevant defs exist (not UNSAT)
2. They span **3+ unrelated subsystems** (conceptually distinct)
3. **No subset ≤ ⅓ of the relevant defs constitutes a useful starting
   point** for the work
4. The work is **uniform** — each instance is equally important, no
   natural priority ordering or "start here" def

**Required fields:** `why_no_cutoff`, `dispersion_description`

#### AMBIG — 2+ disjoint complete answers exist

The query maps to 2+ non-overlapping def neighborhoods, each
independently a complete answer. The query doesn't specify which.

**Decision test:**
1. Identify 2+ groups of defs that each independently answer the query
2. Verify the groups are **disjoint** (no def in both)
3. Verify each group is **complete** — if the user meant that
   interpretation, this group alone is the full answer
4. Verify the query text **doesn't favor one group** over another

**Acceptance criteria:**
1. At least **2 disjoint def groups** can be named with concrete defs
2. Each group is a **complete** answer (not partial)
3. Groups are in **different subsystems**
4. A reasonable developer **could pick either** group based on the
   query text alone

**Required fields:** `candidate_neighborhoods` (list of
`{name, defs, why_plausible}`), `why_ambiguous`

#### Non-OK JSON format

```json
{
  "repo_id": "python-fastapi",
  "reviewer_corrections": "",
  "non_ok_queries": [
    {
      "query_type": "UNSAT",
      "query_text": "Fix the GraphQL subscription resolver timeout",
      "seeds": [],
      "pins": [],
      "false_assumption": "Assumes FastAPI has a GraphQL subsystem with subscription support. It doesn't.",
      "evidence_of_absence": "No files matching *graphql*. No imports of graphene, strawberry, or ariadne."
    },
    {
      "query_type": "BROAD",
      "query_text": "Add type annotations to all untyped function parameters",
      "seeds": [],
      "pins": [],
      "why_no_cutoff": "Untyped parameters in ~60 functions across every module. No subsystem is more relevant than another. Any cutoff capturing 80%+ recall also returns hundreds of fully-typed functions.",
      "dispersion_description": "12+ directories, every module has some. No clustering by score."
    },
    {
      "query_type": "AMBIG",
      "query_text": "Fix the authentication error handling",
      "seeds": [],
      "pins": [],
      "candidate_neighborhoods": [
        {"name": "OAuth2 flow", "defs": ["security/oauth2.py:OAuth2PasswordBearer"], "why_plausible": "Most common auth scheme"},
        {"name": "HTTP Bearer/Basic", "defs": ["security/http.py:HTTPBearer"], "why_plausible": "Separate error paths"}
      ],
      "why_ambiguous": "'Authentication error handling' doesn't specify which auth scheme. Three distinct subsystems handle auth differently."
    }
  ]
}
```

---

## When you are done

After all 30 tasks AND the non-OK queries file, say:

```
ALL TASKS COMPLETE.
```
