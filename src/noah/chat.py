"""Text mode: `noah chat` (a REPL) and `noah say` (one turn), using the voice daemon's agent."""

import asyncio
import sys
from collections.abc import Callable

import typer

from noah.agent.llm import LLMError, TextDelta
from noah.agent.loop import Agent, AgentEvent, StepLimitReached, ToolFinished, ToolStarted
from noah.metrics import TurnTimer
from noah.session import Noah
from noah.speech import Speaker
from noah.tasks.models import Task
from noah.tools.checklist import CHECKLIST_TOOLS
from noah.tools.safety import CannotAsk, Confirm

Write = Callable[[str], None]

PROMPT = "you> "
RESULT_PREVIEW_CHARS = 200
HELP = "Type a request. /reset clears the conversation, /exit quits (or Ctrl-D)."


def write_stdout(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def terminal_confirm(
    read_line: Callable[[str], str] = input, interactive: bool | None = None
) -> Confirm:
    """Ask y/n in the terminal. With no terminal to ask in (piped input), raise `CannotAsk`."""
    if interactive is None:
        interactive = sys.stdin.isatty()

    async def confirm(action: str) -> bool:
        if not interactive:
            raise CannotAsk
        question = typer.style(f"  Allow Noah to {action}? [y/N] ", fg="yellow")
        try:
            answer = await asyncio.to_thread(read_line, question)
        except EOFError:
            return False
        return answer.strip().lower() in ("y", "yes")

    return confirm


def _dim(text: str) -> str:
    return typer.style(text, dim=True)


def _preview(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= RESULT_PREVIEW_CHARS else text[:RESULT_PREVIEW_CHARS] + "…"


class Renderer:
    """Prints agent events and progress announcements, keeping tool and progress lines on
    their own lines between streamed text. Checklist tool calls aren't shown: the
    announcements say the same thing in words."""

    def __init__(self, write: Write) -> None:
        self.write = write
        self.at_line_start = True

    def _line(self, text: str) -> None:
        if not self.at_line_start:
            self.write("\n")
        self.write(text + "\n")
        self.at_line_start = True

    def announce(self, line: str) -> None:
        self._line(typer.style(f"  ● {line}", fg="cyan"))

    def show(self, event: AgentEvent) -> None:
        match event:
            case TextDelta(text=text):
                if self.at_line_start:
                    text = text.lstrip("\n")
                if text:
                    self.write(text)
                    self.at_line_start = text.endswith("\n")
            case ToolStarted(call=call) if call.name not in CHECKLIST_TOOLS:
                self._line(_dim(f"  ↳ {call.name}({call.arguments})"))
            case ToolFinished(call=call, result=result):
                if call.name not in CHECKLIST_TOOLS or result.startswith("Error"):
                    self._line(_dim(f"    {_preview(result)}"))
            case StepLimitReached(max_steps=n):
                self._line(typer.style(f"Stopped after {n} steps without an answer.", fg="yellow"))
            case _:
                pass

    def finish(self) -> None:
        if not self.at_line_start:
            self.write("\n")
        self.at_line_start = True


async def run_turn(
    agent: Agent,
    text: str,
    renderer: Renderer,
    noah: Noah | None = None,
    speaker: Speaker | None = None,
) -> bool:
    """Run one user turn, print it, and speak it if there's a speaker. Returns False if the
    model couldn't be reached."""
    timer = TurnTimer(noah.settings.state_dir / "timing.log" if noah else None)
    if speaker:
        speaker.reset_timing()
    ok = True
    try:
        async for event in agent.run(text):
            if isinstance(event, TextDelta):
                timer.mark("llm_first_token")
                if speaker:
                    speaker.feed(event.text)
            renderer.show(event)
    except LLMError as e:
        renderer.finish()
        renderer.write(typer.style(f"✗ {e}", fg="red") + "\n")
        if speaker:
            speaker.say(str(e))
        ok = False
    finally:
        if noah:
            noah.announcer.flush()
    renderer.finish()
    if speaker:
        await speaker.finish()
        timing = speaker.timing
        if timing.first_sentence is not None:
            timer.mark("first_sentence", timing.first_sentence)
        if timing.first_audio is not None:
            timer.mark("first_audio", timing.first_audio)
    if noah:
        timer.record(mode="text+speech" if speaker else "text")
    return ok


def unfinished_summary(tasks: list[Task]) -> str | None:
    if not tasks:
        return None
    names = ", ".join(f"{t.title} ({t.done_count}/{len(t.items)})" for t in tasks)
    noun = "checklist" if len(tasks) == 1 else "checklists"
    return f"Unfinished {noun} from before: {names}. Ask to continue or cancel."


async def repl(
    noah: Noah,
    renderer: Renderer,
    read_line: Callable[[str], str] = input,
    speaker: Speaker | None = None,
) -> None:
    write = renderer.write
    write(_dim(HELP) + "\n")
    if summary := unfinished_summary(noah.tasks.open_tasks()):
        write(_dim(summary) + "\n")
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
            noah.agent.reset()
            write(_dim("Conversation cleared.") + "\n")
            continue
        write("noah> ")
        renderer.at_line_start = False
        await run_turn(noah.agent, line, renderer, noah, speaker)
