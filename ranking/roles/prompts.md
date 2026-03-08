# Agent Prompt Templates

Copy-paste these into VS Code agent chat to start each session.
Open the chat with the agent's cwd already set to the clone directory.

---

## Training — Auditor

```
Read /home/$USER/wsl-repos/codeplane/ranking/roles/auditor.md — those are your
instructions. Your tasks file is:

/home/$USER/wsl-repos/codeplane/ranking/repos/{SET}/{REPO_NAME}.md

Begin.
```

Replace `{SET}` with `ranker-gate` or `cutoff`.
Replace `{REPO_NAME}` with e.g. `python-fastapi`.

---

## Training — Executor

```
Read /home/$USER/wsl-repos/codeplane/ranking/roles/executor.md — those are your
instructions. Your tasks file is:

/home/$USER/wsl-repos/codeplane/ranking/repos/{SET}/{REPO_NAME}.md

Begin.
```

---

## Training — Reviewer

```
Read /home/$USER/wsl-repos/codeplane/ranking/roles/reviewer.md — those are your
instructions. The tasks file is:

/home/$USER/wsl-repos/codeplane/ranking/repos/{SET}/{REPO_NAME}.md

The ground truth outputs are at:

/home/$USER/wsl-repos/codeplane/ranking/data/{REPO_NAME}/ground_truth/

Begin.
```

---

## Eval — Executor

```
Read /home/$USER/wsl-repos/codeplane/ranking/roles/eval-executor.md — those are
your instructions. It references executor.md which you must also read.

Your tasks file is:

/home/$USER/wsl-repos/codeplane/ranking/repos/eval/{REPO_NAME}.md

Begin.
```

---

## Eval — Reviewer

```
Read /home/$USER/wsl-repos/codeplane/ranking/roles/eval-reviewer.md — those are
your instructions. It references reviewer.md which you must also read.

The tasks file is:

/home/$USER/wsl-repos/codeplane/ranking/repos/eval/{REPO_NAME}.md

The ground truth outputs are at:

/home/$USER/wsl-repos/codeplane/ranking/data/{REPO_NAME}/ground_truth/

Begin.
```
