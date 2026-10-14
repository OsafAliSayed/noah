"""Running shell commands. Risk depends on the command: see safety.assess_shell."""

import asyncio
import subprocess
from pathlib import Path
from typing import Annotated

from pydantic import Field

from noah.tools.registry import Args, ToolError, tool
from noah.tools.safety import Assessment, assess_shell

TIMEOUT_SECONDS = 30


def _risk(args: Args) -> Assessment:
    return assess_shell(args["command"])


def _describe(args: Args) -> str:
    return f"run `{args['command']}`"


@tool(risk=_risk, describe=_describe)
async def run_shell(
    command: Annotated[str, Field(description="A bash command. Runs in the home folder.")],
) -> str:
    """Run a shell command and return its output. Prefer a dedicated tool when one exists.
    Never use sudo. Commands that change things ask the user first."""
    proc = await asyncio.create_subprocess_exec(
        "/bin/bash",
        "-c",
        command,
        cwd=Path.home(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise ToolError(f"The command didn't finish within {TIMEOUT_SECONDS} seconds.") from None
    text = output.decode(errors="replace").strip() or "(no output)"
    return f"exit code {proc.returncode}\n{text}"
