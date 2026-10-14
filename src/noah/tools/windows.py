"""Windows, clipboard, media, brightness and screenshots, using X11 command-line tools."""

import asyncio
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from noah.tools import _proc
from noah.tools.registry import ToolError, tool

MAX_CLIPBOARD = 4000
# What playerctl says when there's no media player, or none that can do the action.
_NO_PLAYER = ("No players found", "No player could handle this command")


def parse_windows(output: str) -> list[tuple[str, str]]:
    """`wmctrl -l` lines → (window id, title), skipping the desktop and panels."""
    windows = []
    for line in output.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[1] != "-1":
            windows.append((parts[0], parts[3]))
    return windows


@tool(risk="safe")
async def list_windows() -> str:
    """List the open windows by title."""
    windows = parse_windows(await _proc.run(["wmctrl", "-l"]))
    if not windows:
        return "No windows are open."
    return "\n".join(title for _, title in windows)


@tool(risk="safe")
async def focus_window(
    name: Annotated[str, Field(description="Part of the window's title, e.g. 'Firefox'.")],
) -> str:
    """Bring a window to the front and switch to it."""
    windows = parse_windows(await _proc.run(["wmctrl", "-l"]))
    needle = name.lower()
    matches = [(wid, title) for wid, title in windows if needle in title.lower()]
    if not matches:
        titles = "; ".join(title for _, title in windows[:8]) or "none"
        raise ToolError(f"No window title contains {name!r}. Open windows: {titles}.")
    wid, title = matches[0]
    await _proc.run(["wmctrl", "-i", "-a", wid])
    return f"Switched to {title}."


@tool(risk="safe")
async def clipboard_get() -> str:
    """Read the text on the clipboard."""
    try:
        text = await _proc.run(["xclip", "-o", "-selection", "clipboard"])
    except ToolError as e:
        if "not available" in str(e):  # xclip's "target STRING not available"
            return "The clipboard is empty."
        raise
    if len(text) > MAX_CLIPBOARD:
        return text[:MAX_CLIPBOARD] + "\n[clipboard continues]"
    return text or "The clipboard is empty."


async def _clipboard_set(text: str) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "xclip", "-selection", "clipboard",
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            start_new_session=True,
        )  # fmt: skip
    except FileNotFoundError:
        raise ToolError("xclip isn't installed. Install it with: sudo apt install xclip") from None
    assert proc.stdin is not None
    proc.stdin.write(text.encode())
    await proc.stdin.drain()
    proc.stdin.close()
    # xclip stays running to serve the selection, so don't wait for it to exit.


@tool(risk="safe")
async def clipboard_set(text: str) -> str:
    """Put text on the clipboard."""
    await _clipboard_set(text)
    return "Copied to the clipboard."


MediaAction = Literal["play", "pause", "play-pause", "next", "previous", "stop"]


@tool(risk="safe")
async def media_control(action: MediaAction) -> str:
    """Control the music or video player (Spotify, the browser, and so on)."""
    try:
        await _proc.run(["playerctl", action])
    except ToolError as e:
        if any(m in str(e) for m in _NO_PLAYER):
            raise ToolError("Nothing is playing media right now.") from None
        raise
    return f"Media: {action}."


@tool(risk="safe")
async def media_status() -> str:
    """What's playing: player, status, artist and title."""
    try:
        out = await _proc.run(
            ["playerctl", "metadata", "--format", "{{playerName}}|{{status}}|{{artist}}|{{title}}"]
        )
    except ToolError as e:
        if any(m in str(e) for m in _NO_PLAYER):
            return "Nothing is playing."
        raise
    player, status, artist, title = [*out.strip().split("|"), "", "", ""][:4]
    by = f" by {artist}" if artist else ""
    return f"{player}: {status}, {title or 'unknown title'}{by}."


@tool(risk="safe")
async def get_brightness() -> str:
    """Get the screen brightness."""
    out = await _proc.run(["brightnessctl", "-m"])
    match = re.search(r",(\d+)%", out)
    if not match:
        raise ToolError(f"Couldn't read the brightness: {out.strip()!r}")
    return f"Brightness is {match.group(1)}%."


@tool(risk="safe")
async def set_brightness(
    percent: Annotated[int, Field(ge=5, le=100, description="Screen brightness, 5 to 100.")],
) -> str:
    """Set the screen brightness."""
    await _proc.run(["brightnessctl", "set", f"{percent}%"])
    return f"Brightness set to {percent}%."


def screenshot_path(now: datetime | None = None, home: Path | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H-%M-%S")
    return (home or Path.home()) / "Pictures" / "Screenshots" / f"Noah {stamp}.png"


@tool(risk="safe")
async def screenshot() -> str:
    """Take a screenshot of the whole screen and save it in ~/Pictures/Screenshots."""
    path = screenshot_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    size = (await _proc.run(["xdotool", "getdisplaygeometry"])).split()
    display = os.environ.get("DISPLAY", ":0")
    await _proc.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-video_size",
         f"{size[0]}x{size[1]}", "-i", display, "-frames:v", "1", str(path)],
        timeout=15,
    )  # fmt: skip
    return f"Saved the screenshot to {path}."
