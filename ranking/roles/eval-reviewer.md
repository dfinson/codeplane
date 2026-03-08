# Role: Eval Outputs Reviewer

> **This role extends the training outputs reviewer with trace export
> and parsing for evaluation.**
>
> Read and follow ALL instructions in
> `/home/$USER/wsl-repos/codeplane/ranking/roles/reviewer.md` first.
> Everything below is IN ADDITION to those instructions — not a
> replacement.

---

## Additional eval-only instructions

### Review is identical

Perform the exact same review process as specified in `reviewer.md`:
verify diffs, def tiers, queries, exploration logs, non-OK queries.
No changes to review criteria.

### After review: export chat traces

After reviewing all tasks AND the non-OK queries file, you have one
additional responsibility: export the chat session traces for
analysis.

**Step 1 — Export the chat session:**

Use the VS Code command to export the chat:

```
workbench.action.chat.export
```

> This will prompt the user to save the file. The user will save it.
> Wait for confirmation.

**Step 2 — Run the trace parser:**

After the export is saved, run the trace parser script to extract
per-task tool-use traces from the exported chat:

```bash
python3 /home/$USER/wsl-repos/codeplane/ranking/infra/parse_traces.py \
  <path_to_exported_chat.json> \
  --output /home/$USER/wsl-repos/codeplane/ranking/data/{repo_id}/traces.jsonl
```

Replace `{repo_id}` with the repo name (e.g., `python-pydantic`).
Replace `<path_to_exported_chat.json>` with the path the user saved
the export to.

Verify the output:
- The script should report how many tasks were extracted
- The count should match the number of tasks that have
  `START_EVAL_TASK`/`END_EVAL_TASK` markers (should be 33)
- If any tasks are missing, note which ones and why (combined
  markers? missing start/end?)

**Step 3 — Report:**

Include the trace extraction results in your final report.

## When you are done

After reviewing all tasks, non-OK queries, AND exporting/parsing
traces, say:

```
EVAL REVIEW COMPLETE.
Tasks corrected: <list of task IDs, or "none">
Non-OK queries corrected: <yes/no>
Traces extracted: <count>/33 tasks
Traces output: ranking/data/{repo_id}/traces.jsonl
```
