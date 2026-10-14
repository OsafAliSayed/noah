"""Command-line entry point: `noah <command>`."""

import asyncio
import contextlib
import os

import typer

from noah import __version__
from noah.agent.loop import Agent
from noah.chat import MissingAPIKey, build_agent, repl, run_turn
from noah.config import ConfigError, Settings, load_settings
from noah.doctor import Check, run_checks

app = typer.Typer(help="Noah: a voice layer over your operating system.", no_args_is_help=True)

_SYMBOLS = {
    "ok": ("✓", typer.colors.GREEN),
    "warn": ("!", typer.colors.YELLOW),
    "fail": ("✗", typer.colors.RED),
}


def _not_built(milestone: str) -> None:
    typer.echo(f"Not built yet (coming in {milestone}).", err=True)
    raise typer.Exit(1)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"noah {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show the version."
    ),
) -> None:
    pass


def _agent() -> Agent:
    try:
        return build_agent(load_settings())
    except ConfigError as e:
        typer.echo(f"Config error: {e}", err=True)
    except MissingAPIKey:
        typer.echo(
            "OPENROUTER_API_KEY is not set. Add it to .env or ~/.config/noah/.env "
            "(see .env.example), or run `noah doctor`.",
            err=True,
        )
    raise typer.Exit(1)


@app.command()
def chat() -> None:
    """Talk to Noah by typing."""
    agent = _agent()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(repl(agent))


@app.command()
def run() -> None:
    """Start the voice daemon."""
    _not_built("M6")


@app.command()
def say(text: str) -> None:
    """Run one command and exit."""
    agent = _agent()
    if not asyncio.run(run_turn(agent, text)):
        raise typer.Exit(1)


@app.command()
def tasks() -> None:
    """Show current and recent checklists."""
    _not_built("M3")


def _print_check(check: Check) -> None:
    symbol, color = _SYMBOLS[check.status]
    typer.echo(f"  {typer.style(symbol, fg=color, bold=True)} {check.name}: {check.detail}")
    if check.hint and check.status != "ok":
        typer.echo(f"      → {check.hint}")


@app.command()
def doctor() -> None:
    """Check this machine has everything Noah needs."""
    settings: Settings | None = None
    error: str | None = None
    try:
        settings = load_settings()
    except ConfigError as e:
        error = str(e)

    checks = run_checks(settings, error, os.environ)
    for check in checks:
        _print_check(check)

    fails = sum(c.status == "fail" for c in checks)
    warns = sum(c.status == "warn" for c in checks)
    typer.echo(f"\n{fails} problem(s), {warns} warning(s).")
    if fails:
        raise typer.Exit(1)
