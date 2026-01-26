# CLI Module — Design Spec

## Scope

The CLI module provides the human-facing operator interface (`cpl`). It is **not** agent-facing — agents use the MCP API.

### Responsibilities

- Parse command-line arguments
- Bootstrap daemon lifecycle (`init`, `up`, `down`)
- Report status and diagnostics (`status`, `doctor`)
- Manage LSP installations (`lsp list`, `lsp install`, `lsp remove`)
- Output formatting (human-readable vs `--json`)

### Commands (from SPEC.md §4.2)

| Command | Purpose |
|---------|---------|
| `cpl init` | One-time repo setup |
| `cpl up` | Start daemon |
| `cpl down` | Stop daemon |
| `cpl status` | Show daemon/index state |
| `cpl doctor` | Diagnostics and recovery hints |
| `cpl lsp *` | LSP management |

---

## Design Options

### Option A: Click-based

```python
import click

@click.group()
def cli():
    pass

@cli.command()
@click.option('--json', is_flag=True)
def status(json):
    ...
```

**Pros:** Mature, well-documented, auto-generates help
**Cons:** Dependency, slightly verbose

### Option B: Typer-based

```python
import typer

app = typer.Typer()

@app.command()
def status(json: bool = False):
    ...
```

**Pros:** Type hints, modern, less boilerplate
**Cons:** Dependency on Click anyway

### Option C: argparse (stdlib)

```python
import argparse

parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers()
status_parser = subparsers.add_parser('status')
status_parser.add_argument('--json', action='store_true')
```

**Pros:** No dependencies
**Cons:** Verbose, manual help formatting

---

## Recommended Approach

**Typer** — modern, minimal boilerplate, good `--help` generation, type-safe.

---

## File Plan

```
cli/
├── __init__.py
├── main.py          # Entry point, app = typer.Typer()
└── commands.py      # Command implementations (thin, call into daemon client)
```

## Dependencies

- `typer` — CLI framework
- `httpx` — HTTP client to talk to daemon
- `rich` — Pretty output (optional, Typer includes it)

## Open Questions

1. Should `cpl` work without daemon running (e.g., `cpl init`)?
   - **Yes** — `init` and `doctor` must work pre-daemon
2. How to handle daemon not running for commands that need it?
   - Return clear error with "run `cpl up` first"
