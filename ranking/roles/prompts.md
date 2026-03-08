# Agent Prompt Templates

Copy-paste these into VS Code agent chat to start each session.
Open the chat with cwd set to the clone directory
(`ranking/clones/{SET}/{CLONE}/`). All paths below are relative to that.

---

## Training — Auditor

```
Read ../../../roles/auditor.md — those are your instructions.
Your tasks file is ../../../repos/{SET}/{REPO_NAME}.md
Begin.
```

Replace `{SET}` with `ranker-gate` or `cutoff`.
Replace `{REPO_NAME}` with e.g. `python-fastapi`.

---

## Training — Executor

```
Read ../../../roles/executor.md — those are your instructions.
Your tasks file is ../../../repos/{SET}/{REPO_NAME}.md
Begin.
```

---

## Training — Reviewer

```
Read ../../../roles/reviewer.md — those are your instructions.
The tasks file is ../../../repos/{SET}/{REPO_NAME}.md
The ground truth outputs are at ../../../data/{REPO_NAME}/ground_truth/
Begin.
```

---

## Eval — Executor

```
Read ../../../roles/eval-executor.md — those are your instructions.
It references executor.md which you must also read.
Your tasks file is ../../../repos/eval/{REPO_NAME}.md
Begin.
```

---

## Eval — Reviewer

```
Read ../../../roles/eval-reviewer.md — those are your instructions.
It references reviewer.md which you must also read.
The tasks file is ../../../repos/eval/{REPO_NAME}.md
The ground truth outputs are at ../../../data/{REPO_NAME}/ground_truth/
Begin.
```
