# Agent Prompt Templates

Copy-paste these into VS Code agent chat to start each session.
Open the chat with cwd set to the clone directory
(`ranking/clones/{SET}/{CLONE}/`). All paths below are relative to that.

Replace `{SET}` with `ranker-gate`, `cutoff`, or `eval`.
Replace `{REPO_NAME}` with e.g. `python-fastapi`.

---

## Auditor

```
Read ../../../roles/auditor.md — those are your instructions.
Your tasks file is ../../../repos/{SET}/{REPO_NAME}.md
Begin.
```

---

## Executor

Run **3 separate sessions** per repo, one per task tier.
This keeps context manageable (~150-200K tokens per session).

### Session A — Narrow tasks

```
Read ../../../roles/executor.md — those are your instructions.
Your tasks file is ../../../repos/{SET}/{REPO_NAME}.md
Execute tasks N1 through N10 and N11 only. Skip all M and W tasks.
Begin.
```

### Session B — Medium tasks

```
Read ../../../roles/executor.md — those are your instructions.
Your tasks file is ../../../repos/{SET}/{REPO_NAME}.md
Execute tasks M1 through M10 and M11 only. Skip all N and W tasks.
Begin.
```

### Session C — Wide tasks + non-OK queries

```
Read ../../../roles/executor.md — those are your instructions.
Your tasks file is ../../../repos/{SET}/{REPO_NAME}.md
Execute tasks W1 through W10 and W11 only. Skip all N and M tasks.
After all W tasks, execute STEP 4 (non-OK queries).
Begin.
```

---

## Reviewer

Run **after all 3 executor sessions** for a repo are complete.

```
Read ../../../roles/reviewer.md — those are your instructions.
The tasks file is ../../../repos/{SET}/{REPO_NAME}.md
The ground truth outputs are at ../../../data/{REPO_NAME}/ground_truth/
Begin.
```
