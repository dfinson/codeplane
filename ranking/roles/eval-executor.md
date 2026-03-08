# Role: Eval Task Executor

> **This role extends the training task executor with trace capture
> for evaluation.**
>
> Read and follow ALL instructions in
> `/home/$USER/wsl-repos/codeplane/ranking/roles/executor.md` first.
> Everything below is IN ADDITION to those instructions — not a
> replacement.

---

## Additional eval-only instructions

### Trace markers

For every task, you MUST bracket the SOLVE phase with terminal echo
markers. The markers use the full task ID including the repo name.

**Before** starting STEP 1 — SOLVE for each task:

```bash
echo "START_EVAL_TASK-{repo_id}/{heading_id}"
```

**After** completing STEP 1 — SOLVE (after the commit + revert, before
STEP 1b — TEST COVERAGE):

```bash
echo "END_EVAL_TASK-{repo_id}/{heading_id}"
```

Example for task N1 in python-pydantic:

```bash
echo "START_EVAL_TASK-python-pydantic/N1"
# ... solve the task: read files, make edits, run tests ...
# ... git add -A && git diff --cached ...
# ... git commit -m "task N1: ..." ...
# ... git revert HEAD --no-edit ...
echo "END_EVAL_TASK-python-pydantic/N1"
```

**CRITICAL RULES for markers:**

1. Each marker MUST be its own separate `echo` command — never
   combine with `&&` or `;`
2. The task ID MUST be `{repo_id}/{heading_id}` — e.g.,
   `python-pydantic/N1`, not just `N1`
3. Do NOT put any tool calls between `END_EVAL_TASK` and the start
   of STEP 1b (coverage) — the marker must cleanly end the solve
   phase
4. Do NOT echo markers during STEP 2 (REFLECT) — only STEP 1 (SOLVE)
   is traced

### Task sequence with markers

The full per-task sequence for eval is:

```
echo "START_EVAL_TASK-{repo_id}/{heading_id}"
  STEP 1 — SOLVE (read, edit, test, commit, revert)
echo "END_EVAL_TASK-{repo_id}/{heading_id}"
  STEP 1b — TEST COVERAGE (run with --coverage, save report)
  STEP 2 — REFLECT (write ground truth JSON)
```

### Everything else is identical

All other instructions — the JSON format, query types, def tiers,
non-OK queries file, coverage collection — are exactly as specified
in `executor.md`. Follow them verbatim.

## When you are done

After all tasks AND the non-OK queries file, say:

```
ALL EVAL TASKS COMPLETE.
```
