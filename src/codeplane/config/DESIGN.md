# Config Module — Design Spec

## Scope

The config module handles configuration loading, validation, and precedence resolution.

### Responsibilities

- Load config from multiple sources (CLI, env, repo, global, defaults)
- Merge with precedence rules
- Validate against schema
- Provide typed config access
- Watch for changes (optional, if daemon supports hot reload)

### From SPEC.md

- §4.2: Config precedence
- §8.9: Refactor context configuration
- §11.5: Test runner configuration
- §7.11: LSP configuration

---

## Design Options

### Option A: Dict-based

```python
def load_config(repo_root: Path, overrides: dict | None = None) -> dict:
    config = DEFAULT_CONFIG.copy()
    config.update(load_global_config())
    config.update(load_repo_config(repo_root))
    if overrides:
        config.update(overrides)
    return config
```

**Pros:** Simple, flexible
**Cons:** No type safety, easy to typo keys

### Option B: Pydantic models

```python
class LSPConfig(BaseModel):
    python: LSPLanguageConfig | None = None
    typescript: LSPLanguageConfig | None = None
    startup_timeout_ms: int = 30000

class Config(BaseModel):
    lsp: LSPConfig = LSPConfig()
    tests: TestConfig = TestConfig()
    refactor: RefactorConfig = RefactorConfig()

def load_config(repo_root: Path, overrides: dict | None = None) -> Config:
    raw = merge_sources(...)
    return Config.model_validate(raw)
```

**Pros:** Type safety, validation, IDE autocomplete
**Cons:** More code, schema changes require model updates

### Option C: dataclasses + manual validation

```python
@dataclass
class Config:
    lsp: LSPConfig
    tests: TestConfig
    ...

def load_config(repo_root: Path) -> Config:
    raw = merge_sources(...)
    validate_schema(raw)
    return Config(**raw)
```

**Pros:** Type hints without Pydantic dependency
**Cons:** Manual validation code

---

## Recommended Approach

**Option B (Pydantic models)** — type safety, automatic validation, easy to extend, good error messages.

---

## File Plan

```
config/
├── __init__.py
├── loader.py        # Load, merge, precedence resolution
├── schema.py        # Pydantic models for all config sections
└── defaults.py      # Default values, .cplignore templates
```

## Dependencies

- `pydantic` — Config validation and models
- `pyyaml` — YAML parsing

## Key Interfaces

```python
# loader.py
def load_config(
    repo_root: Path,
    cli_overrides: dict | None = None,
    env_prefix: str = "CODEPLANE_"
) -> Config: ...

def get_config_sources(repo_root: Path) -> list[Path]:
    """Return list of config files that would be loaded (for debugging)."""

# schema.py
class LSPLanguageConfig(BaseModel):
    server: str
    version: str = "latest"
    args: list[str] = []
    env: dict[str, str] = {}

class LSPConfig(BaseModel):
    python: LSPLanguageConfig | None = None
    typescript: LSPLanguageConfig | None = None
    go: LSPLanguageConfig | None = None
    java: LSPLanguageConfig | None = None
    # ... etc
    exclude: list[str] = []
    startup_timeout_ms: int = 30000
    request_timeout_ms: int = 60000
    max_restart_attempts: int = 3

class TestRunnerConfig(BaseModel):
    python: str = "pytest"
    typescript: str = "jest"
    # ... etc

class TestConfig(BaseModel):
    runners: TestRunnerConfig = TestRunnerConfig()
    custom: list[CustomTestRunner] = []
    exclude: list[str] = []
    parallelism: int = 0  # 0 = auto
    timeout_sec: int = 30
    fail_fast: bool = False

class RefactorConfig(BaseModel):
    enabled: bool = True
    divergence_behavior: Literal["fail", "primary_wins"] = "fail"
    include_comments: bool = True
    max_parallel_contexts: int = 4

class TaskConfig(BaseModel):
    default_mutation_budget: int = 20
    default_test_budget: int = 10
    default_duration_sec: int = 3600
    session_timeout_sec: int = 1800

class DaemonConfig(BaseModel):
    host: str = "127.0.0.1"
    shutdown_timeout_sec: int = 5
    log_level: str = "info"
    log_rotation_mb: int = 10
    log_retention_count: int = 3

class Config(BaseModel):
    lsp: LSPConfig = LSPConfig()
    tests: TestConfig = TestConfig()
    refactor: RefactorConfig = RefactorConfig()
    task: TaskConfig = TaskConfig()
    daemon: DaemonConfig = DaemonConfig()
    contexts: list[ContextConfig] = []  # Multi-context refactor
```

## Precedence (from SPEC.md §4.2)

1. CLI overrides (`cpl up --set key=value`)
2. Environment variables (`CODEPLANE_LSP_STARTUP_TIMEOUT_MS=60000`)
3. Repo config (`.codeplane/config.yaml`)
4. Global config (`~/.config/codeplane/config.yaml`)
5. Built-in defaults

## Config File Locations

| Platform | Global Config |
|----------|---------------|
| Linux/macOS | `~/.config/codeplane/config.yaml` |
| Windows | `%APPDATA%\codeplane\config.yaml` |

Repo config: `.codeplane/config.yaml`

## Open Questions

1. Hot reload on config change?
   - **Recommendation:** Not in v1, requires `cpl down && cpl up`
2. Config validation errors: fail hard or warn?
   - **Recommendation:** Fail hard on invalid config, clear error message
3. Secrets in config (e.g., API keys for LSP download)?
   - **Recommendation:** Environment variables only, never in config files
