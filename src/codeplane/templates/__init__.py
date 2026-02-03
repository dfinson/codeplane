"""Template files for cpl init and other commands.

NOTE: Templates are now generated from canonical definitions in core.excludes.
"""

from codeplane.core.excludes import generate_cplignore_template


def get_cplignore_template() -> str:
    """Get the default .cplignore template.

    Returns generated template from canonical exclude patterns.
    """
    return generate_cplignore_template()


__all__ = ["get_cplignore_template"]
