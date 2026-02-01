"""CodePlane CLI - cpl command."""

import click

from codeplane.cli.down import down_command
from codeplane.cli.init import init_command
from codeplane.cli.status import status_command
from codeplane.cli.up import up_command


@click.group()
@click.version_option(version="0.1.0", prog_name="cpl")
def cli() -> None:
    """CodePlane - Local repository control plane for AI coding agents."""


cli.add_command(init_command, name="init")
cli.add_command(up_command, name="up")
cli.add_command(down_command, name="down")
cli.add_command(status_command, name="status")


if __name__ == "__main__":
    cli()
