"""CodePlane CLI - cpl command."""

import click

from codeplane.cli.init import init_command


@click.group()
@click.version_option(version="0.1.0", prog_name="cpl")
def cli() -> None:
    """CodePlane - Local repository control plane for AI coding agents."""


cli.add_command(init_command, name="init")


if __name__ == "__main__":
    cli()
