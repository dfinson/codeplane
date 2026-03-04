<p align="center">
  <img src="docs/images/logo.png" alt="CodePlane Logo" width="200">
</p>

<h1 align="center">CodePlane</h1>

<p align="center">
  <strong>Local repository control plane for AI coding agents</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/status-alpha-yellow" alt="Status: Alpha">
  <img src="https://img.shields.io/badge/python-≥3.12-blue" alt="Python ≥3.12">
  <a href="https://codecov.io/gh/dfinson/codeplane"><img src="https://codecov.io/gh/dfinson/codeplane/branch/main/graph/badge.svg" alt="Coverage"></a>
  <img src="https://img.shields.io/github/license/dfinson/codeplane" alt="License">
</p>

---

## The Problem

AI coding agents lose a **lot** of time and waste a lot of token consumption on tasks that should be much closer to instant.

- **Exploratory thrash** — repeated grep, file opens, and retries to build a mental model
- **Terminal mediation** — deterministic operations (git status, diff, run test) produce unstructured text and loops
- **Missing deterministic refactors** — renames that IDEs do in seconds take agents minutes via search-and-edit

The bottleneck is I/O and orchestration, not model capability.

## The Solution

CodePlane is an environment-agnostic code intelligence layer purpose-built for agents, not humans. It turns a repository into a **structured, queryable system** designed to reduce exploratory overhead for agents:

```
Agent plans and decides → CodePlane executes → Structured result → Next action
```

Operations return structured results with the relevant context needed for the next step, minimizing repeated probing and guesswork.


## Quick Start

```bash
pip install git+https://github.com/dfinson/codeplane.git
```

```bash
cd /path/to/your-repo
cpl up              # Start daemon on default port 7654 (foreground, Ctrl+C to stop)
cpl up --port 7655  # Or specify a port
```

That's it. `cpl up` automatically:
- Creates `.vscode/mcp.json` with the MCP server config
- Injects agent instructions into `.github/copilot-instructions.md`
- Syncs the port if you change it later (`cpl up --port 8000`)

### CLI Reference

| Command | Description |
|---------|-------------|
| `cpl up` | Start server (foreground) |
| `cpl up --port N` | Start on specific port |
| `cpl up --reindex` | Rebuild index from scratch |
| `cpl init` | Initialize without starting |
| `cpl status` | Check daemon status |
| `cpl clear` | Clear index and cache |

---

## Architecture

CodePlane provides a **full stacked index**:

- **Tier 0 — Tantivy Lexical Index**: Fast, deterministic lexical retrieval for candidate discovery
- **Tier 1 — Tree-sitter/SQLite Structural Facts**: Definitions, references, scopes, imports, exports

## IDE & Agent Integration

`cpl init` / `cpl up` automatically configures your IDE and agents:

| File | Purpose |
|------|---------|
| `.vscode/mcp.json` | VS Code MCP server config (native HTTP transport) |
| `.github/copilot-instructions.md` | Agent instructions (tool names, parameters, patterns) |

The server name follows the pattern `codeplane-{repo_name}`, so tools appear as `mcp_codeplane-myrepo_recon`, etc.

## MCP Tools

| Tool | Description |
|------|-------------|
| `recon` | Task-aware file discovery — returns scaffolds, lite descriptions, and repo map |
| `refactor_plan` | Declare edit targets, get SHA256-locked edit tickets |
| `refactor_edit` | Find-and-replace edits with SHA256 locking (batch multiple files) |
| `checkpoint` | Lint + test + commit + push in one call |
| `semantic_diff` | Structural change summary with blast-radius enrichment |
| `refactor_rename` | Index-based symbol rename with certainty-leveled preview |
| `refactor_move` | Move file/module with automatic import updates |
| `recon_impact` | Find all references to a symbol/file (read-only impact analysis) |
| `refactor_commit` | Apply or inspect a previewed refactoring |
| `refactor_cancel` | Cancel a pending refactoring preview |
| `describe` | Self-documenting tool schemas, errors, and workflows |

## Contributing

```bash
# Clone and install with dev dependencies
git clone https://github.com/dfinson/codeplane.git
cd codeplane
pip install -e ".[dev]"

# Start CodePlane on itself
cpl up
```

Then point your AI agent at the running MCP server.

## License

[MIT](LICENSE)
