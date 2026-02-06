# CodePlane Benchmarking Experiment

This document outlines an experiment to measure the impact of CodePlane MCP on AI coding agent performance.

## Objective

Quantify the difference in agent effectiveness when using CodePlane MCP tools versus raw terminal commands for common coding tasks.

## Hypothesis

Agents using CodePlane MCP will:
1. Complete tasks faster (fewer tool calls, less context overhead)
2. Make fewer errors (structured outputs, validation)
3. Produce more consistent results (deterministic operations)

---

## Target Repositories

| Repository | Language | Size | Complexity | Why Selected |
|------------|----------|------|------------|---------------|
| [fastapi/fastapi](https://github.com/tiangolo/fastapi) | Python | ~50k LOC | Medium | Well-structured, popular, good test coverage |
| [denoland/deno](https://github.com/denoland/deno) | Rust/TS | ~200k LOC | High | Multi-language, complex build, large codebase |
| [spring-projects/spring-boot](https://github.com/spring-projects/spring-boot) | Java | ~300k LOC | High | Industry-standard, complex module structure, extensive tests |

---

## Scenarios

### Scenario 1: Targeted Symbol Rename
**Task**: Rename a function used in 5-15 locations (not project-wide, scoped to a module).

| Metric | Measurement |
|--------|-------------|
| Tool calls | Count of MCP/terminal invocations |
| Correctness | All references updated, no false positives |
| Time to completion | Wall clock seconds |

**CodePlane approach**: `refactor_rename` → `refactor_apply`  
**Terminal approach**: `grep` → manual edits → verify

**Why CodePlane shines**: LSP-backed rename with certainty levels vs regex guessing.

---

### Scenario 2: Find Where Function Is Called
**Task**: Given a function name, find all call sites and understand the call context.

| Metric | Measurement |
|--------|-------------|
| Tool calls | Number of search/read operations |
| Accuracy | All call sites found |
| Context consumed | Tokens used |

**CodePlane approach**: `search` (mode=references) → `read_files` (targeted lines)  
**Terminal approach**: `grep -rn` → `cat` entire files

**Why CodePlane shines**: Symbol-aware search returns structured results with line numbers.

---

### Scenario 3: Quick Bug Fix Workflow
**Task**: Fix a simple bug (off-by-one, typo, missing null check) and commit.

| Metric | Measurement |
|--------|-------------|
| Write attempts | Number of file modification tries |
| Commit success | First-try commit with hooks passing |
| Total time | End-to-end seconds |

**CodePlane approach**: `read_files` → `write_files` → `lint_check` → `git_commit`  
**Terminal approach**: `cat` → `sed`/editor → run linter manually → `git commit`

**Why CodePlane shines**: Atomic writes, integrated lint, hook-compliant commits.

---

### Scenario 4: New to Repo Orientation
**Task**: Answer: "What's the project structure? Where are tests? What's the entry point?"

| Metric | Measurement |
|--------|-------------|
| Tool calls | Operations to get oriented |
| Time to answers | Seconds to produce accurate summary |
| Accuracy | Correct identification of key components |

**CodePlane approach**: `map_repo` (include: structure, entry_points, test_layout)  
**Terminal approach**: `find . -type f` → `ls` → `cat README` → `head` various files

**Why CodePlane shines**: Single call returns structured repo mental model.

---

### Scenario 5: Add Unit Test for Existing Function
**Task**: Write a test for an existing function, run it, ensure it passes.

| Metric | Measurement |
|--------|-------------|
| Test discovery | Found correct test location/pattern |
| Write iterations | File creation attempts |
| First-run pass | Test passes on first execution |

**CodePlane approach**: `search` (find function) → `discover_test_targets` → `write_files` → `run_test_targets`  
**Terminal approach**: `grep` → guess test location → write file → `pytest`/`npm test`

**Why CodePlane shines**: Test discovery shows patterns, targeted test execution.

---

### Scenario 6: Git Blame Investigation
**Task**: Given a suspicious line, find who wrote it, when, and the commit context.

| Metric | Measurement |
|--------|-------------|
| Tool calls | Operations to get full context |
| Accuracy | Correct attribution and commit info |

**CodePlane approach**: `git_inspect` (action=blame, line range) → `git_inspect` (action=show)  
**Terminal approach**: `git blame -L` → `git show` → parse output

**Why CodePlane shines**: Structured blame output with pagination, direct commit details.

---

### Scenario 7: Feature Branch Workflow
**Task**: Create branch, make a change, commit with message, push.

| Metric | Measurement |
|--------|-------------|
| Operations | Git commands needed |
| Hook compliance | Pre-commit hooks respected |
| Error handling | Recovery from any failures |

**CodePlane approach**: `git_checkout` (create=true) → `write_files` → `git_commit` → `git_push`  
**Terminal approach**: `git checkout -b` → edit → `git add` → `git commit` → `git push`

**Why CodePlane shines**: Hooks run automatically, structured error responses.

---

## Benchmarking Procedure

### Setup

1. **Environment**: Consistent VM/container with fixed resources
2. **Agent**: Same model (e.g., Claude Opus 4) for both conditions
3. **Isolation**: Fresh clone for each trial, no cached state
4. **Instrumentation**: Log all tool calls with timestamps

### Execution Protocol

```
For each repository:
  For each scenario:
    For condition in [CodePlane, Terminal-only]:
      For trial in 1..3:
        1. Reset repository to known state
        2. Initialize CodePlane (if CodePlane condition)
        3. Present task prompt to agent
        4. Record all tool calls, outputs, timing
        5. Evaluate outcome against ground truth
        6. Store metrics
```

### Controls

- **Prompt parity**: Identical task descriptions for both conditions
- **No hints**: Don't mention CodePlane in terminal-only condition
- **Timeout**: 5 minutes per scenario (fail if exceeded)
- **Retry policy**: No retries within a trial

---

## Metrics Collection

### Primary Metrics

| Metric | Unit | Collection Method |
|--------|------|-------------------|
| Task completion rate | % | Binary success/fail |
| Time to completion | seconds | Wall clock |
| Tool call count | count | Log parsing |
| Token consumption | tokens | API usage stats |
| Correctness score | 0-100 | Automated + manual review |

### Secondary Metrics

| Metric | Unit | Collection Method |
|--------|------|-------------------|
| Error count | count | Tool failures, retries |
| Context window usage | % | Token tracking |
| Agent confidence | 1-5 | Self-reported if available |

---

## Analysis Plan

1. **Per-scenario comparison**: Paired t-test or Wilcoxon signed-rank
2. **Aggregate effect**: Mixed-effects model with scenario as random effect
3. **Repo-size interaction**: Does CodePlane advantage increase with codebase size?
4. **Failure mode analysis**: Categorize failures by type

---

## Expected Timeline

| Phase | Duration | Deliverable |
|-------|----------|-------------|
| Setup & instrumentation | 1 week | Test harness, logging |
| Pilot runs (1 repo) | 1 week | Validation of procedure |
| Full experiment | 2 weeks | Raw data |
| Analysis & writeup | 1 week | Report |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Model variability | 3 trials per condition, statistical tests |
| Task ambiguity | Pre-register task definitions, pilot test |
| CodePlane bugs | Use stable release, document any workarounds |
| Terminal baseline unfair | Allow agent to use any standard tools |

---

## Success Criteria

The experiment will be considered successful if:

1. Data collection completes for ≥80% of planned trials
2. Results show statistically significant difference (p < 0.05) in ≥3 scenarios
3. Effect sizes are practically meaningful (>20% improvement on primary metrics)
