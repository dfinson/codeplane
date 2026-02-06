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
| [expressjs/express](https://github.com/expressjs/express) | JavaScript | ~15k LOC | Low | Simple structure, minimal dependencies |
| [golang/go](https://github.com/golang/go) | Go | ~1M LOC | Very High | Massive codebase, tests CodePlane at scale |

---

## Scenarios

### Scenario 1: Symbol Rename Refactoring
**Task**: Rename a widely-used function/class across the codebase.

| Metric | Measurement |
|--------|-------------|
| Tool calls | Count of MCP/terminal invocations |
| Files modified | Correctness of changes |
| Missed references | False negatives |
| False positives | Incorrect changes |
| Time to completion | Wall clock seconds |

**CodePlane approach**: `refactor_rename` → `refactor_inspect` → `refactor_apply`  
**Terminal approach**: `grep` → manual `sed`/editor → verify with `grep`

---

### Scenario 2: Cross-File Search and Navigate
**Task**: Find all usages of a specific API pattern, understand call hierarchy.

| Metric | Measurement |
|--------|-------------|
| Search iterations | Number of search refinements |
| Context consumed | Tokens used for file reads |
| Accuracy | Percentage of relevant results found |

**CodePlane approach**: `search` (mode=references) → `read_files`  
**Terminal approach**: `grep -r` → `cat` individual files

---

### Scenario 3: Add Feature with Tests
**Task**: Implement a new function and add corresponding unit tests.

| Metric | Measurement |
|--------|-------------|
| Write iterations | Number of file write attempts |
| Test pass rate | First-run test success |
| Lint/type errors | Issues caught before commit |

**CodePlane approach**: `write_files` → `lint_check` → `run_test_targets`  
**Terminal approach**: `echo >>` / editor → `pytest` / `npm test`

---

### Scenario 4: Bug Investigation from Stack Trace
**Task**: Given a stack trace, locate the bug and propose a fix.

| Metric | Measurement |
|--------|-------------|
| Files examined | Number of files read |
| Pinpoint accuracy | Found correct line? |
| Context efficiency | Relevant lines vs total lines read |

**CodePlane approach**: `git_inspect` (blame) → `search` → `read_files`  
**Terminal approach**: `git blame` → `grep` → `cat`

---

### Scenario 5: Repository Orientation
**Task**: New to the repo—understand structure, find entry points, identify test patterns.

| Metric | Measurement |
|--------|-------------|
| Time to orientation | Seconds until accurate mental model |
| Tool calls | Number of exploration operations |
| Accuracy | Correct identification of key components |

**CodePlane approach**: `map_repo` (include all sections)  
**Terminal approach**: `find` → `ls` → `cat README` → `head` various files

---

### Scenario 6: Git Workflow (Branch, Commit, Push)
**Task**: Create feature branch, make changes, commit with hooks, push.

| Metric | Measurement |
|--------|-------------|
| Operations | Number of git commands |
| Hook compliance | Pre-commit hooks respected? |
| Error recovery | Handling of conflicts/failures |

**CodePlane approach**: `git_checkout` (create) → `write_files` → `git_commit` → `git_push`  
**Terminal approach**: `git checkout -b` → edit → `git add` → `git commit` → `git push`

---

### Scenario 7: Large-Scale Code Analysis
**Task**: Find all functions that don't have docstrings/comments.

| Metric | Measurement |
|--------|-------------|
| Recall | Percentage of undocumented functions found |
| Precision | False positive rate |
| Scalability | Performance on large codebases |

**CodePlane approach**: `search` (mode=symbol, filter_kinds) → cross-reference with content  
**Terminal approach**: AST parsing scripts or `grep` heuristics

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
