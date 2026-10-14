"""Run Noah at login as a systemd user service, restarted if it crashes."""

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

UNIT_NAME = "noah.service"
Run = Callable[[list[str]], subprocess.CompletedProcess[str]]


def unit_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".config" / "systemd" / "user" / UNIT_NAME


def noah_executable() -> Path:
    """The `noah` script next to the running interpreter (the project's .venv)."""
    candidate = Path(sys.executable).parent / "noah"
    if candidate.is_file():
        return candidate
    found = shutil.which("noah")
    if found:
        return Path(found)
    raise FileNotFoundError("Couldn't find the noah command. Run `uv sync` in the project first.")


def unit_text(executable: Path, project_dir: Path) -> str:
    # WorkingDirectory is the project, so the project's .env (API key) is found.
    return f"""\
[Unit]
Description=Noah, a voice assistant for this computer
Documentation=file://{project_dir}/docs/architecture.md
After=graphical-session.target pipewire.service wireplumber.service
PartOf=graphical-session.target

[Service]
Type=simple
WorkingDirectory={project_dir}
ExecStart={executable} run
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
# Stay out of the way of foreground work, and stay small.
Nice=5
MemoryHigh=1500M

[Install]
WantedBy=graphical-session.target
"""


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def install(
    project_dir: Path, start: bool = True, run: Run = _run, home: Path | None = None
) -> Path:
    path = unit_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit_text(noah_executable(), project_dir))
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", *(["--now"] if start else []), UNIT_NAME])
    return path


def uninstall(run: Run = _run, home: Path | None = None) -> bool:
    path = unit_path(home)
    run(["systemctl", "--user", "disable", "--now", UNIT_NAME])
    existed = path.exists()
    path.unlink(missing_ok=True)
    run(["systemctl", "--user", "daemon-reload"])
    return existed


def status(run: Run = _run) -> str:
    result = run(["systemctl", "--user", "status", "--no-pager", "--lines=15", UNIT_NAME])
    return result.stdout or result.stderr
