# CodePlane Security Posture and Ignore Strategy  
**Author: Mark Dunphy**

## Overview

This document defines CodePlane’s security model with emphasis on artifact safety, secret non-leakage, and enforced ignore policies. It also defines the default `.gitignore` and `.cplignore` strategies for secure and efficient operation.

---

## 1. Security Guarantees

- **No shared artifact includes secrets.** Only Git-tracked files are eligible.
- **Local overlay index may include sensitive files** (`.env`, `.pem`), but it is never uploaded, shared, or included in CI artifacts.
- **All indexing and mutation actions are scoped, audited, and deterministic.**
- **Reconciliation is stateless and pull-based**, with no background mutation.

---

## 2. Threat Assumptions

- Runs under trusted OS user account.
- Does not defend against compromised OS or user session.
- Assumes Git is the canonical source of tracked file truth.

---

## 3. Indexing Model

| Tier              | Contents                       | Shared? | Indexed? | Example Files         |
|-------------------|--------------------------------|---------|----------|------------------------|
| Git-tracked       | Tracked source files           | Yes     | Yes      | `src/main.py`          |
| CPL overlay       | Git-ignored but whitelisted    | No      | Yes      | `.env.local`           |
| Ignored (CPL)     | Blocked via `.cplignore`       | No      | No       | `secrets/`, `*.pem`    |

Shared artifact = Git-tracked only. Overlay = local-only. Ignored = excluded.

---

## 4. Shared Artifact Safety

- **Inclusion rule:** Only files explicitly tracked by Git are considered.
- **Build rule:** CI artifact construction must begin from a clean Git clone.
- **Validation rule:** Enterprises can hash-check artifacts and run secret scanners.

---

## 5. `.gitignore` Defaults (Security-Relevant)

Recommended baseline for all CodePlane-enabled repos:

```
# Secrets and tokens
.env
*.pem
*.key
*.p12
*.crt
*.aws

# Build and runtime artifacts
node_modules/
dist/
build/
.venv/
__pycache__/
*.pyc

# IDE and OS junk
.vscode/
.idea/
.DS_Store
*.log
*.lock
```

---

## 6. `.cplignore` Defaults

CPL overlay ignore file (superset of `.gitignore`) blocks noisy, unsafe, and irrelevant paths:

```
# Always ignored for indexing
.env
*.pem
*.key
*.p12
*.crt
*.aws
node_modules/
dist/
build/
.venv/
__pycache__/
*.pyc
*.log
coverage/
pytest_cache/
```

These files are never indexed—even locally.

---

## 7. Failure Modes and Protections

| Misconfig | Result | Mitigation |
|----------|--------|------------|
| Secrets committed to Git | Artifact leaks secret | Prevent via pre-commit hooks, Git scanning |
| Missing `.cplignore` | Sensitive files indexed locally | Defaults applied automatically |
| Lax Git hygiene | Build includes unintended files | Clean clone + hash match required |

---

## 8. Auditability

- All MCP mutations emit structured deltas.
- Overlay and shared indexes are deterministic and reproducible.
- Mutation history is append-only (SQLite-backed).
- No automatic retries or implicit mutations.

---

## Summary

The default posture is safe. Only explicit overrides create risk. Secret non-leakage is enforced structurally at the index level, not just by policy.
