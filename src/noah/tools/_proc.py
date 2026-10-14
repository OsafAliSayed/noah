"""Running system commands from tools."""

import asyncio
import subprocess

from noah.doctor import SYSTEM_COMMANDS
from noah.tools.registry import ToolError


def _missing(program: str) -> ToolError:
    package = SYSTEM_COMMANDS.get(program, (program, ""))[0]
    return ToolError(f"{program} isn't installed. Install it with: sudo apt install {package}")


async def run(cmd: list[str], timeout: float = 10) -> str:
    """Run a command to completion and return stdout. Raises ToolError on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        raise _missing(cmd[0]) from None
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise ToolError(f"{cmd[0]} didn't finish within {timeout:g} seconds.") from None
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip() or f"exit code {proc.returncode}"
        raise ToolError(f"{cmd[0]} failed: {detail}")
    return stdout.decode(errors="replace")


async def launch(cmd: list[str], settle: float = 2) -> None:
    """Start a program that may keep running (an app, a file opener) without waiting for it.

    Waits up to `settle` seconds so an immediate failure is still reported.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,  # survives Noah restarting
        )
    except FileNotFoundError:
        raise _missing(cmd[0]) from None
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), settle)
    except TimeoutError:
        return  # Still running, so it started.
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip() or f"exit code {proc.returncode}"
        raise ToolError(f"{cmd[0]} failed: {detail}")
