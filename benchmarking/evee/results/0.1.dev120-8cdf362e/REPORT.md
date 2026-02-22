# CodePlane Benchmark Report: Evee

**Version:** `0.1.dev120` (commit `8cdf362e`)
**Date:** 2026-02-22
**Model:** Claude Opus 4.6 (fast mode)
**Repo under test:** [microsoft/evee](https://github.com/microsoft/evee)

---

## 1. Methodology

Five GitHub issues from the Evee repository were selected as benchmark tasks.
Each issue was implemented twice under identical conditions:

- **Baseline (native):** Agent uses VS Code's built-in tools only (`read_file`,
  `replace_string_in_file`, `run_in_terminal`, `list_dir`, `grep_search`, etc.)
- **CodePlane:** Agent uses CodePlane MCP tools (`read_source`, `write_source`,
  `read_scaffold`, `search`, `lint_check`, `run_test_targets`, `semantic_diff`,
  `git_stage_and_commit`, etc.) with terminal fallback only when no CPL tool exists.

Both conditions used the same model (Claude Opus 4.6 fast mode), the same prompt
(including "commit in chunks" and DoD checklist), and the same VS Code Copilot
agent mode environment.

### Outcome scoring

After each run, a separate agent session reviewed the branch diff against `main`
using a structured rubric:

| Dimension       | Scale | Description                                     |
|-----------------|-------|-------------------------------------------------|
| Correctness     | 0–3   | Does the code work and handle edge cases?        |
| Completeness    | 0–3   | Are all DoD items addressed?                     |
| Code quality    | 0–3   | Clean, idiomatic, no hacks?                      |
| Test quality    | 0–3   | Coverage, edge cases, integration tests?         |
| Documentation   | 0–3   | PR description, docstrings, config docs?         |
| Lint clean      | 0–1   | Binary: all linters pass?                        |
| Tests pass      | 0–1   | Binary: full test suite passes?                  |
| **Max score**   | **17**|                                                  |

All outcomes were scored by Claude Opus 4.6 (fast mode).

---

## 2. Issues tested

| Issue | Title                          | Complexity |
|-------|-------------------------------|------------|
| #4    | Cache model inference          | Medium     |
| #108  | Mocked E2E integration tests   | Medium     |
| #233  | Early-stop evaluator           | Medium     |
| #260  | Disable progress bars          | Low        |
| #262  | REST-based models              | High       |

---

## 3. Head-to-head comparison (means, n=5)

| Metric                | Baseline      | CodePlane     | Delta       | Direction |
|-----------------------|---------------|---------------|-------------|-----------|
| **Outcome score**     | 16.0 / 17     | 16.4 / 17     | +0.4 (+2.5%)| Better    |
| Turns                 | 29.2          | 43.2          | +14.0 (+48%)| Worse     |
| Tool calls            | 46.0          | 47.0          | +1.0 (+2%)  | Neutral   |
| Tool errors           | 0.2           | 4.0           | +3.8        | Worse     |
| Total tokens          | 1.78M         | 2.66M         | +0.88M (+49%)| Worse    |
| Completion tokens     | 12.1K         | 22.3K         | +10.2K (+85%)| Worse    |
| Cache hit ratio       | 96.0%         | 98.3%         | +2.3pp      | Better    |
| LLM duration          | 134s          | 213s          | +79s (+59%) | Worse     |
| Avg TTFT              | 2,563ms       | 2,519ms       | −44ms (−2%) | Neutral   |
| Context (last msg)    | 109           | 133           | +24 (+22%)  | Worse     |

---

## 4. Per-issue breakdown

### Issue #260 — Disable progress bars

| Metric          | Baseline | CodePlane | Δ%      |
|-----------------|----------|-----------|---------|
| Turns           | 27       | 49        | +81.5%  |
| Tool calls      | 46       | 49        | +6.5%   |
| Tool errors     | 0        | 5         | —       |
| Tokens          | 1.09M    | 2.57M     | +136.4% |
| LLM duration    | 81s      | 211s      | +159.8% |
| TTFT            | 2,170ms  | 3,094ms   | +42.6%  |
| **Score**       | **17**   | **16**    | −5.9%   |

Worst-performing CPL run. The agent hit 5 tool errors and needed 81% more turns
to achieve a slightly lower quality score.

### Issue #233 — Early-stop evaluator

| Metric          | Baseline | CodePlane | Δ%      |
|-----------------|----------|-----------|---------|
| Turns           | 32       | 40        | +25.0%  |
| Tool calls      | 41       | 46        | +12.2%  |
| Tool errors     | 1        | 3         | +200%   |
| Tokens          | 3.24M    | 3.05M     | −5.8%   |
| LLM duration    | 181s     | 238s      | +31.4%  |
| TTFT            | 3,612ms  | 2,541ms   | −29.6%  |
| **Score**       | **17**   | **17**    | 0%      |

Best CPL run relative to baseline. Tokens were actually lower (−5.8%), and TTFT
improved significantly. Quality was perfect for both.

### Issue #108 — Mocked E2E integration tests

| Metric          | Baseline | CodePlane | Δ%      |
|-----------------|----------|-----------|---------|
| Turns           | 28       | 44        | +57.1%  |
| Tool calls      | 51       | 49        | −3.9%   |
| Tool errors     | 0        | 7         | —       |
| Tokens          | 1.73M    | 3.42M     | +98.2%  |
| LLM duration    | 168s     | 218s      | +29.9%  |
| TTFT            | 2,320ms  | 2,317ms   | −0.1%   |
| **Score**       | **15**   | **17**    | +13.3%  |

CPL achieved a perfect score where baseline did not (baseline missed CI gating
and lacked proper integration tests). However, CPL used nearly 2× the tokens
and hit 7 tool errors — the highest error count across all runs.

### Issue #4 — Cache model inference

| Metric          | Baseline | CodePlane | Δ%      |
|-----------------|----------|-----------|---------|
| Turns           | 29       | 41        | +41.4%  |
| Tool calls      | 48       | 44        | −8.3%   |
| Tool errors     | 0        | 2         | —       |
| Tokens          | 1.39M    | 2.13M     | +53.5%  |
| LLM duration    | 107s     | 185s      | +72.2%  |
| TTFT            | 2,415ms  | 2,330ms   | −3.5%   |
| **Score**       | **14**   | **15**    | +7.1%   |

Both runs missed the mandatory `configuration.md` update. CPL scored slightly
higher (better integration tests). Lowest error count for CPL (2 errors).

### Issue #262 — REST-based models

| Metric          | Baseline | CodePlane | Δ%      |
|-----------------|----------|-----------|---------|
| Turns           | 30       | 42        | +40.0%  |
| Tool calls      | 44       | 47        | +6.8%   |
| Tool errors     | 0        | 3         | —       |
| Tokens          | 1.48M    | 2.14M     | +44.4%  |
| LLM duration    | 133s     | 213s      | +60.5%  |
| TTFT            | 2,299ms  | 2,311ms   | +0.5%   |
| **Score**       | **17**   | **17**    | 0%      |

Clean run for both. CPL used 46 of 47 tool calls through CodePlane (highest
CPL adoption rate), with only 1 terminal call.

---

## 5. Outcome quality detail

| Issue | Dim          | Baseline | CPL |
|-------|-------------|----------|-----|
| #260  | Correctness  | 3        | 3   |
|       | Completeness | 3        | 3   |
|       | Code quality | 3        | 3   |
|       | Test quality | 3        | 3   |
|       | Documentation| 3        | 2   |
|       | Lint clean   | 1        | 1   |
|       | Tests pass   | 1        | 1   |
|       | **Total**    | **17**   | **16** |
| #233  | Correctness  | 3        | 3   |
|       | Completeness | 3        | 3   |
|       | Code quality | 3        | 3   |
|       | Test quality | 3        | 3   |
|       | Documentation| 3        | 3   |
|       | Lint clean   | 1        | 1   |
|       | Tests pass   | 1        | 1   |
|       | **Total**    | **17**   | **17** |
| #108  | Correctness  | 3        | 3   |
|       | Completeness | 2        | 3   |
|       | Code quality | 3        | 3   |
|       | Test quality | 3        | 3   |
|       | Documentation| 2        | 3   |
|       | Lint clean   | 1        | 1   |
|       | Tests pass   | 1        | 1   |
|       | **Total**    | **15**   | **17** |
| #4    | Correctness  | 3        | 3   |
|       | Completeness | 2        | 2   |
|       | Code quality | 3        | 3   |
|       | Test quality | 2        | 3   |
|       | Documentation| 2        | 2   |
|       | Lint clean   | 1        | 1   |
|       | Tests pass   | 1        | 1   |
|       | **Total**    | **14**   | **15** |
| #262  | Correctness  | 3        | 3   |
|       | Completeness | 3        | 3   |
|       | Code quality | 3        | 3   |
|       | Test quality | 3        | 3   |
|       | Documentation| 3        | 3   |
|       | Lint clean   | 1        | 1   |
|       | Tests pass   | 1        | 1   |
|       | **Total**    | **17**   | **17** |

---

## 6. Key observations

### Quality: slight edge to CodePlane (+2.5%)

CodePlane scored higher on 2 of 5 issues (#108, #4), lower on 1 (#260), and
tied on 2 (#233, #262). The mean difference (16.0 → 16.4) is within noise for
n=5.

### Efficiency: baseline wins across the board

Every efficiency metric favors the baseline:

- **+48% more turns** — CodePlane's multi-step tool protocol (describe →
  scaffold → search → read_source → write_source) requires more round trips
  than native tools (read_file → replace_string_in_file).
- **+49% more tokens** — CPL tool responses are larger (structured JSON with
  metadata, hints, file hashes) compared to raw file content.
- **+59% more LLM wall-clock** — direct consequence of more turns and tokens.
- **+85% more completion tokens** — the agent generates more output per task
  with CPL, possibly due to longer tool invocation syntax.

### Tool errors: 20× higher with CodePlane

Baseline averaged 0.2 errors/run; CodePlane averaged 4.0. Common error causes:
- Wrong parameter names for CPL tools (agent guessing instead of checking docs)
- Hash mismatches on `write_source` (stale `expected_file_sha256`)
- Validation errors from CPL's stricter input schemas

### Cache hit ratio: CPL marginally better

98.3% vs 96.0%. CPL's structured responses are more cache-friendly due to
consistent JSON formatting, but the absolute token volume is still higher.

### TTFT: negligible difference

2,519ms vs 2,563ms — within measurement noise. The LLM inference latency is
dominated by model load and prompt processing, not tool selection.

---

## 7. Limitations

- **n=5** is too small for statistical significance. These are directional
  signals only.
- **Single model** (Claude Opus 4.6 fast mode). Results may differ with other
  models.
- **Single repo** (Evee). Results may not generalize to larger or more complex
  codebases where CodePlane's structural index could provide greater benefit.
- **Cold CodePlane index.** The daemon indexed the repo on first use; subsequent
  runs may perform differently with a warm index.
- **No multi-file refactoring tasks.** The selected issues are feature
  implementations, not cross-cutting refactors where `refactor_rename` and
  `refactor_move` could show value.
- **Outcome scoring is LLM-based.** Scores are subjective assessments by the
  same model, not ground-truth correctness checks.

---

## 8. Files in this directory

| File | Description |
|------|-------------|
| `evee_{issue}_claude-opus-4-6-fast_codeplane_raw.json` | Full chatreplay export (raw) |
| `evee_{issue}_claude-opus-4-6-fast_codeplane_trace.json` | Extracted event trace |
| `evee_{issue}_claude-opus-4-6-fast_codeplane_result_metrics.json` | Computed metrics + outcome |
| `REPORT.md` | This report |
