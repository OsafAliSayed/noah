"""Text mode: `noah chat` (a REPL) and `noah say` (one turn), using the voice daemon's agent."""

import asyncio
import sys
from collections.abc import Callable

import typer

from noah.agent.llm import LLM, LLMError, OpenRouterLLM, TextDelta
from noah.agent.loop import Agent, AgentEvent, StepLimitReached, ToolFinished, ToolStarted
from noah.agent.prompts import current_system_prompt
from noah.config import Settings
from noah.tools import default_tools
from noah.tools.registry import ToolRegistry
from noah.tools.safety import AuditLog, Confirm

Write = Callable[[str], None]

PROMPT = "you> "
RESULT_PREVIEW_CHARS = 200
HELP = "Type a request. /reset clears the conversation, /exit quits (or Ctrl-D)."


class MissingAPIKey(Exception):
    pass


def write_stdout(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def terminal_confirm(
    read_line: Callable[[str], str] = input, interactive: bool | None = None
) -> Confirm:
    """Ask y/n in the terminal. With no terminal to ask in (piped input), always decline."""
    if interactive is None:
        interactive = sys.stdin.isatty()

    async def confirm(action: str) -> bool:
        if not interactive:
            return False
        question = typer.style(f"  Allow Noah to {action}? [y/N] ", fg="yellow")
        try:
            answer = await asyncio.to_thread(read_line, question)
        except EOFError:
            return False
        return answer.strip().lower() in ("y", "yes")

    return confirm


def build_agent(
    settings: Settings, llm: LLM | None = None, confirm: Confirm | None = None
) -> Agent:
    if llm is None:
        if settings.openrouter_api_key is None:
            raise MissingAPIKey
        llm = OpenRouterLLM(
            api_key=settings.openrouter_api_key.get_secret_value(),
            model=settings.llm.model,
            base_url=settings.llm.base_url,
        )
    tools = ToolRegistry(
        default_tools(),
        confirm=confirm or terminal_confirm(),
        audit=AuditLog(settings.state_dir / "actions.log"),
    )
    return Agent(
        llm=llm,
        tools=tools,
        system_prompt=current_system_prompt,
        max_steps=settings.llm.max_steps,
    )


def _dim(text: str) -> str:
    return typer.style(text, dim=True)


def _preview(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= RESULT_PREVIEW_CHARS else text[:RESULT_PREVIEW_CHARS] + "…"


class _Renderer:
    """Prints agent events, keeping tool lines on their own lines between streamed text."""

    def __init__(self, write: Write) -> None:
        self.write = write
        self.at_line_start = True

    def _line(self, text: str) -> None:
        if not self.at_line_start:
            self.write("\n")
        self.write(text + "\n")
        self.at_line_start = True

    def show(self, event: AgentEvent) -> None:
        match event:
            case TextDelta(text=text):
                self.write(text)
                self.at_line_start = text.endswith("\n")
            case ToolStarted(call=call):
                self._line(_dim(f"  ↳ {call.name}({call.arguments})"))
            case ToolFinished(result=result):
                self._line(_dim(f"    {_preview(result)}"))
            case StepLimitReached(max_steps=n):
                self._line(typer.style(f"Stopped after {n} steps without an answer.", fg="yellow"))

    def finish(self) -> None:
        if not self.at_line_start:
            self.write("\n")
        self.at_line_start = True


async def run_turn(agent: Agent, text: str, write: Write = write_stdout) -> bool:
    """Run one user turn and print it. Returns False if the model couldn't be reached."""
    renderer = _Renderer(write)
    try:
        async for event in agent.run(text):
            renderer.show(event)
    except LLMError as e:
        renderer.finish()
        write(typer.style(f"✗ {e}", fg="red") + "\n")
        return False
    renderer.finish()
    return True


async def repl(agent: Agent, read_line: Callable[[str], str] = input, write: Write = write_stdout):
    write(_dim(HELP) + "\n")
    while True:
        try:
            line = (await asyncio.to_thread(read_line, PROMPT)).strip()
        except EOFError:
            write("\n")
            return
        if not line:
            continue
        if line in ("/exit", "/quit"):
            return
        if line == "/reset":
            agent.reset()
            write(_dim("Conversation cleared.") + "\n")
            continue
        write("noah> ")
        await run_turn(agent, line, write)
