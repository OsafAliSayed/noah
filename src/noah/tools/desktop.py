"""Volume, notifications, time and system status."""

import re
from datetime import datetime
from pathlib import Path
from typing import Annotated

import psutil
from pydantic import Field

from noah.tools import _proc
from noah.tools.registry import ToolError, tool

SINK = "@DEFAULT_AUDIO_SINK@"


def parse_volume(output: str) -> tuple[int, bool]:
    """Parse `wpctl get-volume` output like 'Volume: 0.45 [MUTED]'."""
    match = re.search(r"Volume:\s*([\d.]+)", output)
    if not match:
        raise ToolError(f"Couldn't read the volume from wpctl: {output.strip()!r}")
    return round(float(match.group(1)) * 100), "[MUTED]" in output


@tool(risk="safe")
async def get_volume() -> str:
    """Get the current output volume and whether it is muted."""
    percent, muted = parse_volume(await _proc.run(["wpctl", "get-volume", SINK]))
    return f"Volume is {percent}%{', muted' if muted else ''}."


@tool(risk="safe")
async def set_volume(
    percent: Annotated[int, Field(ge=0, le=100, description="Volume from 0 to 100.")],
) -> str:
    """Set the output volume. Also unmutes."""
    await _proc.run(["wpctl", "set-volume", "-l", "1.0", SINK, f"{percent}%"])
    await _proc.run(["wpctl", "set-mute", SINK, "0"])
    return f"Volume set to {percent}%."


@tool(risk="safe")
async def notify(
    title: Annotated[str, Field(description="Short heading.")],
    body: Annotated[str, Field(description="Message text.")] = "",
) -> str:
    """Show a desktop notification."""
    await _proc.run(["notify-send", "--app-name=Noah", title, body])
    return "Notification shown."


@tool(risk="safe")
def get_time() -> str:
    """Get the current local date and time."""
    return datetime.now().astimezone().strftime("%A %d %B %Y, %H:%M %Z")


def _gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB"


@tool(risk="safe")
def system_info() -> str:
    """Get battery, CPU, memory and disk status."""
    lines = []
    battery = psutil.sensors_battery()
    if battery is not None:
        state = "charging" if battery.power_plugged else "on battery"
        lines.append(f"Battery: {battery.percent:.0f}%, {state}")
    lines.append(f"CPU: {psutil.cpu_percent(interval=0.3):.0f}% busy, {psutil.cpu_count()} threads")
    mem = psutil.virtual_memory()
    lines.append(f"Memory: {_gb(mem.available)} free of {_gb(mem.total)}")
    disk = psutil.disk_usage(str(Path.home()))
    lines.append(f"Disk (home): {_gb(disk.free)} free of {_gb(disk.total)}")
    return "\n".join(lines)
