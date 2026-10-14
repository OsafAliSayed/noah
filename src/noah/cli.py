"""Command-line entry point: `noah <command>`."""

import asyncio
import contextlib
import logging
import os

import typer

from noah import __version__
from noah.chat import Renderer, repl, run_turn, terminal_confirm, write_stdout
from noah.config import ConfigError, Settings, load_settings
from noah.doctor import Check, run_checks
from noah.session import MissingAPIKey, Noah, make_speaker, start
from noah.speech import Speaker
from noah.tasks.announcer import SYMBOLS, Say
from noah.tasks.models import Task
from noah.tasks.store import TaskStore

app = typer.Typer(help="Noah: a voice layer over your operating system.", no_args_is_help=True)

_SYMBOLS = {
    "ok": ("✓", typer.colors.GREEN),
    "warn": ("!", typer.colors.YELLOW),
    "fail": ("✗", typer.colors.RED),
}


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


def _say_to(renderer: Renderer, speaker: Speaker | None) -> Say:
    if speaker is None:
        return renderer.announce

    def say(line: str) -> None:
        renderer.announce(line)
        speaker.say(line)

    return say


async def _start(renderer: Renderer, speaker: Speaker | None = None) -> Noah:
    try:
        return await start(_settings(), confirm=terminal_confirm(), say=_say_to(renderer, speaker))
    except MissingAPIKey:
        typer.echo(
            "OPENROUTER_API_KEY is not set. Add it to .env or ~/.config/noah/.env "
            "(see .env.example), or run `noah doctor`.",
            err=True,
        )
        raise typer.Exit(1) from None


async def _speaker(settings: Settings) -> Speaker:
    typer.echo(typer.style("Loading the voice...", dim=True))
    try:
        return await make_speaker(settings)
    except Exception as e:
        typer.echo(f"Couldn't start speech: {e}", err=True)
        raise typer.Exit(1) from None


@app.command()
def chat(speak: bool = typer.Option(False, "--speak", help="Answer out loud too.")) -> None:
    """Talk to Noah by typing."""

    async def main() -> None:
        renderer = Renderer(write_stdout)
        speaker = await _speaker(_settings()) if speak else None
        noah = await _start(renderer, speaker)
        try:
            await repl(noah, renderer, speaker=speaker)
        finally:
            await noah.close()
            if speaker:
                await speaker.close()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())


@app.command()
def run(
    ptt: bool = typer.Option(False, "--ptt", help="Push-to-talk only: no wake word."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log debug details."),
) -> None:
    """Start the voice daemon: talk to Noah with the wake word or the hotkey."""
    from noah.voice import run_voice

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = _settings()
    if settings.openrouter_api_key is None:
        typer.echo("OPENROUTER_API_KEY is not set. Run `noah doctor`.", err=True)
        raise typer.Exit(1)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_voice(settings, mode="ptt" if ptt else None))


@app.command()
def say(
    text: str, speak: bool = typer.Option(False, "--speak", help="Answer out loud too.")
) -> None:
    """Run one command and exit."""

    async def main() -> bool:
        renderer = Renderer(write_stdout)
        speaker = await _speaker(_settings()) if speak else None
        noah = await _start(renderer, speaker)
        try:
            return await run_turn(noah.agent, text, renderer, noah, speaker)
        finally:
            await noah.close()
            if speaker:
                await speaker.close()

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


service_app = typer.Typer(help="Run Noah at login as a systemd user service.")
config_app = typer.Typer(help="Noah's config file.")
app.add_typer(service_app, name="service")
app.add_typer(config_app, name="config")


@service_app.command("install")
def service_install(
    start_now: bool = typer.Option(True, "--start/--no-start", help="Start it right away."),
) -> None:
    """Install and enable the service, so Noah starts when you log in."""
    from pathlib import Path

    from noah import service

    project = Path(__file__).resolve().parents[2]
    try:
        path = service.install(project, start=start_now)
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"Installed {path}.")
    typer.echo("Noah starts at login. Logs: journalctl --user -u noah -f")


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Stop and remove the service."""
    from noah import service

    removed = service.uninstall()
    typer.echo("Removed the service." if removed else "The service wasn't installed.")


@service_app.command("status")
def service_status() -> None:
    """Show whether the service is running, with its latest log lines."""
    from noah import service

    typer.echo(service.status())


@config_app.command("init")
def config_init(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config file."),
) -> None:
    """Write a commented config.toml with every setting at its default."""
    from noah.config import default_config_dir, example_config

    path = default_config_dir() / "config.toml"
    if path.exists() and not force:
        typer.echo(f"{path} already exists. Use --force to replace it.", err=True)
        raise typer.Exit(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(example_config())
    typer.echo(f"Wrote {path}.")


@config_app.command("path")
def config_path() -> None:
    """Print where Noah reads its config from."""
    typer.echo(_settings().config_path)


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
