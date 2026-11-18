"""Architectural boundary test: EventRepository import restrictions.

The trail subsystem owns transcript provenance projections. Explicit ingestion,
control-receipt and replay owners may use EventRepository directly. Other
transcript consumers must use TrailNodeRepository projections.
"""

from __future__ import annotations

import ast
import pathlib

# Modules that are ALLOWED to import EventRepository.
# Categorized per unified-trail-service.md §6:
#
# --- Provenance infrastructure (ingestion + rehydration) ---
ALLOWED_EVENT_REPO_CONSUMERS = {
    "backend/persistence/event_repo.py",  # self
    "backend/services/trail/service.py",  # rehydration on session_resumed
    "backend/services/trail/node_builder.py",  # rehydration on session_resumed
    "backend/services/runtime/service.py",  # hot-path event translation
    "backend/services/ingest/_base.py",  # native replay and checkpoint recovery
    # Exact migration/equivalence owner: validates retained history before explicit checkpoint conversion.
    "backend/services/ingest/checkpoint_conversion.py",
    "backend/services/ingest/record_service.py",  # observation ingestion/projection DTO
    # --- Durable control receipts and their recovery owners ---
    "backend/persistence/execution_admission_repo.py",
    "backend/persistence/execution_contract_repo.py",
    "backend/persistence/governance_provider_repo.py",  # validates retained accounting source receipts
    "backend/persistence/integration_repo.py",
    "backend/persistence/native_execution_repo.py",  # durable native receipts and recovery
    "backend/persistence/recipe_run_repo.py",
    "backend/persistence/review_repo.py",
    "backend/services/action_policy/production.py",  # governance ledger replay
    "backend/services/adapters/adapter_registry.py",  # utility completion receipts
    "backend/services/events/title_refinement.py",  # durable refinement claims
    "backend/services/recipe/control.py",  # attempt stop receipts
    "backend/services/recipe/review_service.py",  # review publication receipts
    "backend/services/runtime/verify.py",  # interrupted verification recovery
    # --- Infrastructure telemetry (not provenance — see §6.3) ---
    "backend/services/runtime/telemetry.py",  # log_line_emitted only
    # --- Application wiring (DI, lifecycle, API plumbing) ---
    "backend/di.py",
    "backend/lifespan.py",
    "backend/api/job_artifacts.py",
    "backend/api/integrations.py",  # observation event stream
    "backend/services/job/job_service.py",
    "backend/services/events/sse_manager.py",
    "backend/services/events/reenrich.py",
}


def test_event_repo_import_boundary():
    """No service outside the allowlist may import EventRepository."""
    violations: list[str] = []
    backend = pathlib.Path("backend")

    for py_file in backend.rglob("*.py"):
        rel = str(py_file).replace("\\", "/")
        if rel in ALLOWED_EVENT_REPO_CONSUMERS:
            continue
        # Skip test files — they can import whatever they need
        if "/tests/" in rel:
            continue

        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [a.name for a in node.names]
                if "event_repo" in module or "EventRepository" in names:
                    violations.append(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "event_repo" in alias.name:
                        violations.append(f"{rel}:{node.lineno}")

    assert not violations, "EventRepository imported outside allowlist:\n" + "\n".join(
        f"  {v}" for v in sorted(violations)
    )
