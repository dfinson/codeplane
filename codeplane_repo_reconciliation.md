
# CodePlane Repo Truth & Reconciliation Design (No Watchers)

## ✅ Core Design Goals

- Correctly reflect the repository state on disk, even across external edits.
- Never mutate Git state unless explicitly triggered by an MCP command.
- Cheap, deterministic reconciliation before/after every CodePlane endpoint.
- No reliance on OS watchers—reconciliation is polling + fast diff.
- Works across Git-tracked files, Git-ignored-but-CPL-tracked files, and ignored files.

## 🔢 Canonical Repo State Version

The authoritative repo version is:

```
RepoVersion = (HEAD SHA, .git/index stat metadata, submodule SHAs)
```

Use this to invalidate caches or determine if reconciliation is needed.

- `HEAD SHA`: `git rev-parse HEAD` or libgit2 equivalent.
- `.git/index`: compare mtime + size (no need to read contents).
- Submodules: treat each as its own repo, include its HEAD SHA.

## 📂 File Type Classification

| Type | Defined By | Tracked In | Checked During Reconcile? | Indexed? |
|------|------------|------------|----------------------------|----------|
| 1. Git-tracked | Git        | Git index                  | Yes (stat + hash fallback) | Yes (shared and local) |
| 2. CPL-tracked (Git-ignored) | `.cplignore` opt-in | CPL overlay index | Yes (stat + hash)         | Yes (local only) |
| 3. Ignored | `.cplignore` hard-excluded | None | No                         | No |

## 🔍 Change Detection Strategy

### Git-Tracked Files

Use **Git-style status** logic:

1. Load Git index entries.
2. For each tracked file:
   - `stat()` → compare to cached metadata (mtime, size, inode).
   - If metadata differs → hash file content and compare to index SHA.
   - If confirmed changed → reindex the file and invalidate relevant CPL cache entries.

### CPL-Tracked Files (Not in Git)

- Maintain internal CPL index of these files.
- Compare stat against cached metadata.
- If metadata differs → hash file content to confirm.
- Reindex only changed files.

## 🔁 Reconciliation Triggers

Reconciliation occurs:

- On daemon start
- Before and after every MCP (API) endpoint that reads or mutates repo state
- After agent-initiated file or Git ops (rename, commit, rebase, etc.)

## 🔧 Rename and Move Detection

- Detect delete+create pairs with identical hash → infer rename.
- Optional: use Git-style similarity diff for small content changes.
- Default behavior: treat as unlink + create unless hash match.

## 🧪 CRLF, Symlinks, Submodules

- **CRLF**: Normalize line endings during hashing. Avoid false dirty.
- **Symlinks**: Treat as normal files. Do not follow. Git tracks symlink targets as content blobs.
- **Submodules**:
  - Track submodule HEADs independently.
  - Reindex on submodule HEAD or path change.
  - Never recurse unless submodule is initialized.

## 🧼 Corruption and Recovery

- CodePlane never mutates `.git/index`, working tree, or HEAD.
- On Git metadata corruption: fail with clear message; don’t auto-repair.
- On CPL index corruption: wipe and reindex from Git + disk.

## 🔄 Reconcile Algorithm (Pseudocode)

```python
def reconcile(repo):
    head_sha = get_head_sha()
    index_stat = stat('.git/index')

    if (head_sha, index_stat) != repo.last_seen_version:
        repo.invalidate_caches()

    changed_files = []

    # 1. Git-tracked
    for path, entry in git_index.entries():
        fs_meta = stat(path)
        if fs_meta != entry.stat:
            if sha(path) != entry.hash:
                changed_files.append(path)

    # 2. CPL-tracked untracked files
    for path, entry in cpl_overlay.entries():
        fs_meta = stat(path)
        if fs_meta != entry.stat:
            if sha(path) != entry.hash:
                changed_files.append(path)

    # 3. Rename detection
    deleted = repo.files_missing()
    added = repo.files_added()
    for a in deleted:
        for b in added:
            if sha(a) == sha(b):
                repo.mark_rename(a, b)

    # 4. Reindex changed files
    for f in changed_files:
        repo.reindex(f)

    repo.last_seen_version = (head_sha, index_stat)
```

## 🧭 Invariants

- All mutations are agent-initiated via MCP.
- No CodePlane daemon background threads mutate repo state.
- All reconcile logic is stateless, deterministic, and idempotent.
- Git is the sole truth for tracked file identity and content.
- CPL index is always derived from disk + Git, never canonical.

## 🔒 Summary

CodePlane reconciles repo state using:

- **HEAD + index stat fingerprinting**
- **Git-style stat + hash diff logic**
- **Selective per-file hashing for Git and CPL tracked files**
- **Rename inference via hash match only**
- **No watchers, no guesswork, no state mutation outside MCP**

This yields a safe, fast, and auditable foundation for deterministic execution across changing, multi-actor repositories.
