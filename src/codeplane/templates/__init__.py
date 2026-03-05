"""Template files for cpl init and other commands."""

from codeplane.core.excludes import generate_cplignore_template


def get_cplignore_template() -> str:
    """Get the default .cplignore template."""
    return generate_cplignore_template()


__all__ = ["get_cplignore_template"]
