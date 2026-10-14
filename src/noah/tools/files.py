"""Reading the home folder. Writing files comes later, behind `confirm`."""

from typing import Annotated

from pydantic import Field

from noah.tools.registry import Args, ToolError, tool
from noah.tools.safety import Assessment, assess_home_path, resolve_user_path

MAX_ENTRIES = 200
MAX_READ_CHARS = 8000


def _path_risk(args: Args) -> Assessment:
    return assess_home_path(args["path"])


@tool(risk=_path_risk)
def list_files(
    path: Annotated[str, Field(description="Folder inside home, e.g. '~/Downloads'.")] = "~",
    show_hidden: bool = False,
) -> str:
    """List a folder's contents. Folders end with '/'."""
    folder = resolve_user_path(path)
    if not folder.is_dir():
        raise ToolError(f"{folder} is not a folder.")
    entries = sorted(
        (p for p in folder.iterdir() if show_hidden or not p.name.startswith(".")),
        key=lambda p: (not p.is_dir(), p.name.lower()),
    )
    if not entries:
        return f"{folder} is empty."
    names = [p.name + ("/" if p.is_dir() else "") for p in entries[:MAX_ENTRIES]]
    more = len(entries) - MAX_ENTRIES
    footer = f"\n...and {more} more" if more > 0 else ""
    return f"{folder}:\n" + "\n".join(names) + footer


@tool(risk=_path_risk)
def read_file(
    path: Annotated[str, Field(description="File inside home, e.g. '~/notes/todo.txt'.")],
) -> str:
    """Read a text file."""
    file = resolve_user_path(path)
    if not file.is_file():
        raise ToolError(f"{file} is not a file.")
    with file.open("rb") as f:
        data = f.read(MAX_READ_CHARS * 4)
    if b"\0" in data[:1024]:
        raise ToolError(f"{file} is a binary file.")
    text = data.decode(errors="replace")
    if len(text) > MAX_READ_CHARS or file.stat().st_size > len(data):
        return text[:MAX_READ_CHARS] + "\n[file continues]"
    return text
