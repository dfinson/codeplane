"""Template files for cpl init and other commands."""

from importlib import resources


def get_cplignore_template() -> str:
    """Load the default .cplignore template."""
    return resources.files(__package__).joinpath("cplignore.template").read_text()
