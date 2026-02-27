"""Template files for cpl init and other commands.

NOTE: Templates are now generated from canonical definitions in core.excludes.
"""

from pathlib import Path

from codeplane.core.excludes import generate_cplignore_template


def get_cplignore_template() -> str:
    """Get the default .cplignore template.

    Returns generated template from canonical exclude patterns.
    """
    return generate_cplignore_template()


def get_cplcache_script() -> str:
    """Return the cplcache.py script source for injection into .codeplane/scripts/."""
    return (Path(__file__).parent / "cplcache_script.py").read_text(encoding="utf-8")


__all__ = ["get_cplcache_script", "get_cplignore_template"]
