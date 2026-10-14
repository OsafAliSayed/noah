"""Changing files in the home folder. Overwriting and moving ask first."""

import shutil
from typing import Annotated

from pydantic import Field

from noah.tools.registry import Args, ToolError, tool
from noah.tools.safety import CONFIRM, Assessment, assess_home_path, resolve_user_path

MAX_WRITE_CHARS = 100_000


def _write_risk(args: Args) -> Assessment:
    assessment = assess_home_path(args["path"])
    if assessment.risk == "deny":
        return assessment
    path = resolve_user_path(args["path"])
    # A new file is harmless; replacing or appending to one that exists asks first.
    return CONFIRM if path.exists() else assessment


def _describe_write(args: Args) -> str:
    verb = "append to" if args.get("append") else "write"
    return f"{verb} {args['path']}"


@tool(risk=_write_risk, describe=_describe_write)
def write_file(
    path: Annotated[str, Field(description="File inside home, e.g. '~/codes/demo/README.md'.")],
    content: str,
    append: bool = False,
) -> str:
    """Create a text file, or replace or append to one. Missing folders are created."""
    if len(content) > MAX_WRITE_CHARS:
        raise ToolError(f"That's over {MAX_WRITE_CHARS} characters; write it in parts.")
    file = resolve_user_path(path)
    if file.is_dir():
        raise ToolError(f"{file} is a folder.")
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a" if append else "w") as f:
        f.write(content)
    return f"{'Appended to' if append else 'Wrote'} {file} ({len(content)} characters)."


def _folder_risk(args: Args) -> Assessment:
    return assess_home_path(args["path"])


@tool(risk=_folder_risk)
def make_folder(
    path: Annotated[str, Field(description="Folder inside home, e.g. '~/codes/demo'.")],
) -> str:
    """Create a folder, and any missing parent folders."""
    folder = resolve_user_path(path)
    if folder.is_file():
        raise ToolError(f"{folder} is a file.")
    existed = folder.is_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return f"{folder} already exists." if existed else f"Created {folder}."


def _move_risk(args: Args) -> Assessment:
    for key in ("source", "destination"):
        assessment = assess_home_path(args[key])
        if assessment.risk == "deny":
            return assessment
    return CONFIRM


def _describe_move(args: Args) -> str:
    return f"move {args['source']} to {args['destination']}"


@tool(risk=_move_risk, describe=_describe_move)
def move_file(
    source: Annotated[str, Field(description="File or folder inside home.")],
    destination: Annotated[str, Field(description="New path, or an existing folder to move into.")],
) -> str:
    """Move or rename a file or folder. Won't overwrite anything."""
    src, dst = resolve_user_path(source), resolve_user_path(destination)
    if not src.exists():
        raise ToolError(f"{src} doesn't exist.")
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        raise ToolError(f"{dst} already exists; pick another name.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(src, dst)
    return f"Moved {src} to {dst}."
