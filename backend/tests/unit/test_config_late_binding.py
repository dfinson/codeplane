"""Regression tests for late-bound ``get_codeplane_dir()`` lookups.

Modules that did ``from backend.config import get_codeplane_dir`` bound the
symbol at *import* time. That has two consequences, and the second one is a
cross-test landmine:

1. ``patch("backend.config.get_codeplane_dir", ...)`` was silently ineffective
   for those modules — they kept calling the real function.
2. Worse: if such a module happened to be imported for the *first* time while
   that patch was active, it permanently captured the ``MagicMock``. The
   binding outlived the ``with patch(...)`` block, so every later
   ``get_codeplane_dir()`` call in the process returned the patcher's
   ``tmp_path``.

Concretely, that made these three ``test_lifespan.py`` cases fail whenever
``test_cli.py`` ran first in the same process, because
``TestUpLaunchProfilePublication`` patches ``backend.config.get_codeplane_dir``
and was the first thing to import ``restart_protocol``::

    pytest backend/tests/unit/test_cli.py backend/tests/unit/test_lifespan.py

The full suite hid it: collection imports ``test_restart_protocol.py``, which
imports the module before any test body runs, so the real function got bound
and the landmine never armed. Green there was luck of collection order, not
correctness.

The fix is to resolve through the module object (``backend_config.get_codeplane_dir()``)
so the lookup happens at call time. These tests pin that property.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# Every module that resolves the CodePlane home directory. Each must look the
# function up on ``backend.config`` at call time rather than binding it at
# import time.
LATE_BINDING_MODULES = [
    "backend.lifespan",
    "backend.persistence.database",
    "backend.services.credentials.encryption",
    "backend.services.dev_restart.launch_profile",
    "backend.services.dev_restart.restart_protocol",
    "backend.services.ingest.mapping_registry",
    "backend.services.job.retention_service",
    "backend.services.setup.checks",
    "backend.services.setup.service",
    "backend.services.setup.wizard",
]


def _resolver(module: str, attr: str) -> Callable[[], Path]:
    return getattr(importlib.import_module(module), attr)  # type: ignore[no-any-return]


@pytest.mark.parametrize(
    ("module", "attr", "suffix"),
    [
        ("backend.services.dev_restart.restart_protocol", "get_dev_restart_dir", "dev-restart"),
        ("backend.services.dev_restart.launch_profile", "active_launch_profile_path", "run.json"),
        ("backend.services.job.retention_service", "_artifacts_dir", "artifacts"),
    ],
)
def test_patching_backend_config_is_honored(module: str, attr: str, suffix: str, tmp_path: Path) -> None:
    """Patching the canonical target must actually redirect the path.

    Without late binding these resolvers keep calling the real function, so the
    result points at the developer's real ``~/.codeplane`` instead of ``tmp_path``.
    """
    resolve = _resolver(module, attr)
    with patch("backend.config.get_codeplane_dir", return_value=tmp_path):
        assert resolve() == tmp_path / suffix


@pytest.mark.parametrize("module", LATE_BINDING_MODULES)
def test_module_does_not_bind_get_codeplane_dir_at_import_time(module: str) -> None:
    """No module may hold its own ``get_codeplane_dir`` reference.

    This is the structural guard for the whole hazard class: a module-level
    binding is exactly what lets a ``MagicMock`` leak out of a ``with patch(...)``
    block and corrupt unrelated tests later in the process.
    """
    imported = importlib.import_module(module)
    assert not hasattr(imported, "get_codeplane_dir"), (
        f"{module} binds get_codeplane_dir at import time; import the module "
        f"(`from backend import config as backend_config`) and call "
        f"`backend_config.get_codeplane_dir()` instead so patching and "
        f"CODEPLANE_HOME stay authoritative."
    )
