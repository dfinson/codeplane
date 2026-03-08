# Agent Prompt Templates

Copy-paste these into VS Code agent chat to start each session.

---

## Training — Executor

```
Read /home/$USER/wsl-repos/codeplane/ranking/roles/executor.md — those are your
instructions. Your tasks file is:

/home/$USER/wsl-repos/codeplane/ranking/repos/{SET}/{REPO_NAME}.md

You are working inside the cloned repo at:

/home/$USER/wsl-repos/codeplane/ranking/clones/{SET}/{CLONE_NAME}/

Begin.
```

Replace `{SET}` with `ranker-gate` or `cutoff`.
Replace `{REPO_NAME}` with e.g. `python-fastapi`.
Replace `{CLONE_NAME}` with the clone directory name (e.g. `fastapi`).

---

## Training — Reviewer

```
Read /home/$USER/wsl-repos/codeplane/ranking/roles/reviewer.md — those are your
instructions. The tasks file is:

/home/$USER/wsl-repos/codeplane/ranking/repos/{SET}/{REPO_NAME}.md

The ground truth outputs are at:

/home/$USER/wsl-repos/codeplane/ranking/data/{REPO_NAME}/ground_truth/

You are working inside the cloned repo at:

/home/$USER/wsl-repos/codeplane/ranking/clones/{SET}/{CLONE_NAME}/

Begin.
```

---

## Eval — Executor

```
Read /home/$USER/wsl-repos/codeplane/ranking/roles/eval-executor.md — those are
your instructions. It references executor.md which you must also read.

Your tasks file is:

/home/$USER/wsl-repos/codeplane/ranking/repos/eval/{REPO_NAME}.md

You are working inside the cloned repo at:

/home/$USER/wsl-repos/codeplane/ranking/clones/eval/{CLONE_NAME}/

Begin.
```

Replace `{REPO_NAME}` with e.g. `python-pydantic`.
Replace `{CLONE_NAME}` with the clone directory name (e.g. `pydantic`).

---

## Eval — Reviewer

```
Read /home/$USER/wsl-repos/codeplane/ranking/roles/eval-reviewer.md — those are
your instructions. It references reviewer.md which you must also read.

The tasks file is:

/home/$USER/wsl-repos/codeplane/ranking/repos/eval/{REPO_NAME}.md

The ground truth outputs are at:

/home/$USER/wsl-repos/codeplane/ranking/data/{REPO_NAME}/ground_truth/

You are working inside the cloned repo at:

/home/$USER/wsl-repos/codeplane/ranking/clones/eval/{CLONE_NAME}/

Begin.
```
