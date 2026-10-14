"""Opening apps, files and URLs."""

import difflib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import Field

from noah.tools import _proc
from noah.tools.registry import ToolError, tool
from noah.tools.safety import resolve_user_path


@dataclass(frozen=True)
class DesktopApp:
    id: str  # desktop file name without .desktop, what gtk-launch takes
    name: str


def application_dirs() -> list[Path]:
    """Where .desktop files live, user dirs first so they win over system ones."""
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [data_home, *(Path(d) for d in data_dirs.split(":") if d)]
    dirs += [
        Path.home() / ".local/share/flatpak/exports/share",
        Path("/var/lib/flatpak/exports/share"),
        Path("/var/lib/snapd/desktop"),
    ]
    return [d / "applications" for d in dict.fromkeys(dirs)]


def _parse_desktop_file(path: Path) -> DesktopApp | None:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    in_entry = False
    entry: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            in_entry = line == "[Desktop Entry]"
        elif in_entry and "=" in line:
            key, value = line.split("=", 1)
            entry.setdefault(key.strip(), value.strip())
    if entry.get("Type") != "Application" or "Name" not in entry:
        return None
    if entry.get("NoDisplay") == "true" or entry.get("Hidden") == "true":
        return None
    return DesktopApp(id=path.stem, name=entry["Name"])


def find_apps(dirs: list[Path] | None = None) -> list[DesktopApp]:
    apps: dict[str, DesktopApp] = {}
    for directory in dirs if dirs is not None else application_dirs():
        for path in sorted(directory.glob("*.desktop")):
            if path.stem not in apps and (app := _parse_desktop_file(path)):
                apps[path.stem] = app
    return list(apps.values())


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def match_app(query: str, apps: list[DesktopApp]) -> DesktopApp | list[str]:
    """Best match for a spoken app name, or a few suggestions if nothing matches well."""
    q = _norm(query)
    if not q:
        return []
    for test in (
        lambda a: _norm(a.name) == q,
        lambda a: _norm(a.id.rsplit(".", 1)[-1]) == q,
        lambda a: _norm(a.name).startswith(q),
        lambda a: q in _norm(a.name).split() or q in _norm(a.id).split(),
        # "vs code" → code.desktop: one spoken word is the app's id.
        lambda a: _norm(a.id.rsplit(".", 1)[-1]) in q.split(),
        lambda a: q in _norm(a.name),
    ):
        hits = [a for a in apps if test(a)]
        if hits:
            return min(hits, key=lambda a: len(a.name))
    names = [a.name for a in apps]
    return difflib.get_close_matches(query, names, n=3, cutoff=0.5)


@tool(risk="safe")
async def open_app(
    name: Annotated[str, Field(description="The app's name, e.g. 'Firefox' or 'VS Code'.")],
) -> str:
    """Open an installed desktop application by name."""
    match = match_app(name, find_apps())
    if isinstance(match, list):
        hint = f" Did you mean {', '.join(match)}?" if match else ""
        raise ToolError(f"No installed app matches {name!r}.{hint}")
    await _proc.launch(["gtk-launch", match.id])
    return f"Opened {match.name}."


@tool(risk="safe")
async def open_path_or_url(
    target: Annotated[str, Field(description="A web URL, or a file or folder path (~ allowed).")],
) -> str:
    """Open a URL in the browser, or a file or folder with its default app."""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", target) or target.startswith("mailto:"):
        await _proc.launch(["xdg-open", target])
        return f"Opened {target}."
    path = resolve_user_path(target)
    if not path.exists():
        raise ToolError(f"{path} doesn't exist.")
    await _proc.launch(["xdg-open", str(path)])
    return f"Opened {path}."
