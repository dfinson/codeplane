"""CodePlane CLI - cpl command."""

import click

from codeplane.cli.clear import clear_command
from codeplane.cli.down import down_command
from codeplane.cli.init import init_command
from codeplane.cli.restart import restart_command
from codeplane.cli.status import status_command
from codeplane.cli.up import up_command
from codeplane.core.logging import configure_logging


@click.group()
@click.version_option(version="0.1.0", prog_name="cpl")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """CodePlane - Local repository control plane for AI coding agents."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    configure_logging(level="DEBUG" if verbose else "INFO")


cli.add_command(init_command, name="init")
cli.add_command(up_command, name="up")
cli.add_command(down_command, name="down")
cli.add_command(restart_command, name="restart")
cli.add_command(clear_command, name="clear")
cli.add_command(status_command, name="status")


if __name__ == "__main__":
    cli()
