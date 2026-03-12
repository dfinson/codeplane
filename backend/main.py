"""FastAPI application factory and CLI entry point."""

from __future__ import annotations

import click
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import approvals, artifacts, events, health, jobs, settings, voice, workspace


def create_app(*, dev: bool = False) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="Tower", version="0.1.0")

    if dev:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(health.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(events.router, prefix="/api")
    app.include_router(approvals.router, prefix="/api")
    app.include_router(artifacts.router, prefix="/api")
    app.include_router(workspace.router, prefix="/api")
    app.include_router(voice.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")

    return app


@click.group()
def cli() -> None:
    """Tower — control tower for coding agents."""


@cli.command()
@click.option("--host", default="127.0.0.1", help="Bind host")
@click.option("--port", default=8080, type=int, help="Bind port")
@click.option("--dev", is_flag=True, help="Enable development mode (CORS for localhost:5173)")
@click.option("--tunnel", is_flag=True, help="Start Dev Tunnel for remote access")
def up(host: str, port: int, dev: bool, tunnel: bool) -> None:
    """Start the Tower server."""
    app = create_app(dev=dev)
    uvicorn.run(app, host=host, port=port)


@cli.command()
def init() -> None:
    """Create default configuration at ~/.tower/config.yaml."""
    click.echo("tower init — not yet implemented")


@cli.command()
def version() -> None:
    """Print Tower version."""
    click.echo("tower 0.1.0")


if __name__ == "__main__":
    cli()
