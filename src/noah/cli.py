"""Command-line entry point: `noah <command>`."""

import asyncio
import contextlib
import os

import typer

from noah import __version__
from noah.chat import Renderer, repl, run_turn, terminal_confirm, write_stdout
from noah.config import ConfigError, Settings, load_settings
from noah.doctor import Check, run_checks
from noah.session import MissingAPIKey, Noah, start
from noah.tasks.announcer import SYMBOLS
from noah.tasks.models import Task
from noah.tasks.store import TaskStore

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


def _settings() -> Settings:
    try:
        return load_settings()
    except ConfigError as e:
        typer.echo(f"Config error: {e}", err=True)
        raise typer.Exit(1) from None


async def _start(renderer: Renderer) -> Noah:
    try:
        return await start(_settings(), confirm=terminal_confirm(), say=renderer.announce)
    except MissingAPIKey:
        typer.echo(
            "OPENROUTER_API_KEY is not set. Add it to .env or ~/.config/noah/.env "
            "(see .env.example), or run `noah doctor`.",
            err=True,
        )
        raise typer.Exit(1) from None


@app.command()
def chat() -> None:
    """Talk to Noah by typing."""

    async def main() -> None:
        renderer = Renderer(write_stdout)
        noah = await _start(renderer)
        try:
            await repl(noah, renderer)
        finally:
            await noah.close()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())


@app.command()
def run() -> None:
    """Start the voice daemon."""
    _not_built("M6")


@app.command()
def say(text: str) -> None:
    """Run one command and exit."""

    async def main() -> bool:
        renderer = Renderer(write_stdout)
        noah = await _start(renderer)
        try:
            return await run_turn(noah.agent, text, renderer, noah)
        finally:
            await noah.close()

    if not asyncio.run(main()):
        raise typer.Exit(1)


_TASK_COLORS = {
    "running": typer.colors.BLUE,
    "waiting": typer.colors.YELLOW,
    "done": typer.colors.GREEN,
    "failed": typer.colors.RED,
}


def format_task(task: Task) -> str:
    when = task.updated_at.strftime("%d %b %H:%M")
    status = typer.style(task.status, fg=_TASK_COLORS[task.status], bold=True)
    head = f"#{task.id} {typer.style(task.title, bold=True)}  {status} "
    lines = [f"{head}{task.done_count}/{len(task.items)}  {_dim_text(when)}"]
    for item in task.items:
        note = _dim_text(f"  ({item.note})") if item.note else ""
        lines.append(f"  {SYMBOLS[item.status]} {item.number}. {item.text}{note}")
    return "\n".join(lines)


def _dim_text(text: str) -> str:
    return typer.style(text, dim=True)


@app.command()
def tasks(
    limit: int = typer.Option(10, "--limit", "-n", help="How many checklists to show."),
) -> None:
    """Show current and recent checklists."""

    async def recent() -> list[Task]:
        store = TaskStore(_settings().data_dir / "noah.db")
        try:
            return await store.recent(limit)
        finally:
            await store.close()

    found = asyncio.run(recent())
    if not found:
        typer.echo("No checklists yet.")
        return
    typer.echo("\n\n".join(format_task(t) for t in found))


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
