# GitHub Copilot Instructions for Evee

This repository follows a strict **multi-backend, multi-venv architecture**. Copilot must adhere to these guidelines to ensure correct code generation, dependency management, and test execution.

## 1. Multi-Backend Architecture

The codebase is split into distinct backends, each serving a specific purpose and having its own isolated dependencies:

*   **Core**: Located in `src/` (and root `tests/`). This is the base package containing the CLI, core interfaces, and local execution logic.
*   **MLflow**: Located in `packages/evee-mlflow/`. Support for MLflow tracking.
*   **AzureML**: Located in `packages/evee-azureml/`. Support for Azure Machine Learning compute **and** tracking.
*   **Example**: Located in `example/`. A separate directory for building experiments using evee (not part of the framework development).

### Plugin System
Evee uses Python **entry points** to discover and load backends.
*   **Compute Backends**: Registered under the group `evee.compute_backends`.
*   **Tracking Backends**: Registered under the group `evee.tracking_backends`.

When implementing new backends, ensure they are correctly registered in the `pyproject.toml` of the respective package.

## 2. Virtual Environments (Strict Isolation)

**NEVER assume a single global virtual environment.**

Each backend must be developed, run, and tested inside its own dedicated virtual environment. You must infer the correct backend based on the file path you are working with and select the corresponding environment strategy.

| Context | File Path Pattern | Virtual Environment | Setup Command |
| :--- | :--- | :--- | :--- |
| **Core** | `src/`, `tests/` | `~/.venvs/evee-core` | `make setup-core` |
| **MLflow** | `packages/evee-mlflow/` | `~/.venvs/evee-mlflow` | `make setup-mlflow` |
| **AzureML** | `packages/evee-azureml/` | `~/.venvs/evee-azureml` | `make setup-azureml` |

> **Note:** The Makefile creates venvs in `~/.venvs/` by default. Running `./tools/environment/setup.sh` directly creates `.venv-{backend}` in the repo root instead.

*   **Do not** suggest installing packages globally or in a generic `.venv`.
*   **Do not** suggest imports that are not available in the current file's backend (e.g., do not import `mlflow` in `src/evee/core` files).

## 3. Preferred Command Execution (Makefile)

**ALWAYS prefer Makefile targets over ad-hoc shell commands.**

The `Makefile` abstracts the complexity of activating the correct virtual environment and setting up paths.

### Running Tests

#### Running All Tests for a Backend
Use these make targets to run the complete test suite for each backend:

*   **Core Tests**: `make test-core`
*   **MLflow Tests**: `make test-mlflow`
*   **AzureML Tests**: `make test-azureml`

#### Running Specific Tests
To run a specific test file or test function, activate the appropriate venv and use pytest directly:

```bash
# Core backend - specific test file
source ~/.venvs/evee-core/bin/activate
pytest tests/path/to/test_file.py -v

# Core backend - specific test function
pytest tests/path/to/test_file.py::test_function_name -v

# MLflow backend - specific test
source ~/.venvs/evee-mlflow/bin/activate
pytest packages/evee-mlflow/tests/test_file.py::TestClass::test_method -v

# AzureML backend - specific test
source ~/.venvs/evee-azureml/bin/activate
pytest packages/evee-azureml/tests/test_file.py -k "test_pattern" -v
```

### Environment Setup
To create or update dependencies:

*   `make setup-core`
*   `make setup-mlflow`
*   `make setup-azureml`
*   `make setup-all` — Setup all environments at once

### Other Tasks
*   **Linting**: `make lint`
*   **Run All Tests**: `make test-all` — Runs all backend tests with combined coverage
*   **Clean Artifacts**: `make clean` — Removes build artifacts and venvs

> **Note on Wheels**: Wheel building happens dynamically during `evee run --remote`. There is no need to manually build wheels locally.

### Integration Tests
Integration tests are **skipped by default**. To run them explicitly:
```bash
pytest -m integration
```

## 4. Coding Patterns & Standards

### CLI Development
*   Use **Click** for command definitions.
*   Use **Rich** (`rich.console.Console`) for all user output.
*   **Lazy Imports**: In `src/evee/__init__.py` and CLI commands, use lazy imports to keep startup time fast. Avoid top-level imports of heavy libraries (like `pandas`, `azure-ai-ml`) in CLI entry points.

### Configuration
*   Use `evee.config.Config` for loading `config.yaml`.
*   This class supports environment variable substitution using the `${VAR}` or `${VAR:-default}` syntax (POSIX-style).
*   **Do not** manually parse YAML or environment variables if `Config` can handle it.
*   **Empty string env vars**: When an optional non-string field (e.g., `int | None`) may receive an empty string from an environment variable, use the `EmptyStrToNoneInt` annotated type from `evee.config.models`. This ensures empty strings are coerced to `None` before Pydantic validation. Example:
    ```python
    from evee.config.models import EmptyStrToNoneInt
    max_workers: EmptyStrToNoneInt = None
    ```

### Environment Variables
| Variable | Purpose | Default |
| :--- | :--- | :--- |
| `LOG_LEVEL` | Sets logging level (DEBUG, INFO, WARNING, ERROR) | `INFO` |
| `EVEE_DISABLE_RICH_LOGGING` | Disables Rich console formatting when `true` | `false` |

### Logging
*   Always use the centralized logger:
    ```python
    from evee.logging.logger import setup_logger
    logger = setup_logger(__name__)
    ```
*   The logger is configured to use `RichHandler` for pretty console output, unless `EVEE_DISABLE_RICH_LOGGING=true`.

### Error Handling
*   Use specific exception types.
*   When dealing with optional backends (like AzureML), catch `ImportError` or check for entry point existence before usage to provide helpful error messages to the user.

## 5. Guiding Principles for Copilot

1.  **Context Awareness**: Before suggesting code or commands, infer the backend from the file path:
    *   Files in `src/` or root `tests/` → **Core** context
    *   Files in `packages/evee-{name}/` → **{name}** backend context (e.g., `packages/evee-mlflow/` → MLflow, `packages/evee-azureml/` → AzureML)
    *   Files in `example/` → **Example** project context (user of evee, not framework development)
2.  **Dependency Safety**: Ensure suggested imports exist in the active backend's `pyproject.toml`. Do not suggest cross-backend imports (e.g., don't import `mlflow` in core, don't import `azure-ai-ml` in mlflow backend).
3.  **Test Isolation**: When writing or running tests, ensure they are targeted to the correct backend to avoid cross-contamination of results or dependencies.

---

## Maintaining This Document

**Contributors are encouraged to keep this file up to date!**

If you make changes to the codebase that affect development workflows, coding patterns, or architectural decisions, please update this document accordingly. Examples of changes that warrant an update:

*   Adding a new backend package
*   Changing virtual environment paths or setup commands
*   Introducing new coding patterns or conventions
*   Updating Makefile targets
*   Adding new environment variables or configuration options

Keeping these instructions current helps all contributors (and Copilot!) work more effectively with the codebase.
