"""Settings the user changes by voice: "keep it quiet", "do not disturb"."""

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from noah.tasks.announcer import Announcer
from noah.tools.registry import Tool, tool

if TYPE_CHECKING:
    from noah.orchestrator import Orchestrator

_VERBOSITY_REPLIES = {
    "quiet": "I'll only speak up when a task starts, finishes or needs you.",
    "normal": "I'll give you progress updates as steps finish.",
    "chatty": "I'll tell you about every step.",
}


class Controls:
    def __init__(self, announcer: Announcer) -> None:
        self.announcer = announcer
        self.orchestrator: Orchestrator | None = None

    def attach(self, orchestrator: "Orchestrator") -> None:
        self.orchestrator = orchestrator

    def tools(self) -> list[Tool]:
        @tool(risk="safe")
        def set_verbosity(
            level: Annotated[
                Literal["quiet", "normal", "chatty"],
                Field(description="quiet when asked to keep it down, chatty for more detail."),
            ],
        ) -> str:
            """Change how much Noah says about checklist progress."""
            self.announcer.verbosity = level
            return f"Verbosity is {level}. {_VERBOSITY_REPLIES[level]}"

        @tool(risk="safe")
        def do_not_disturb(on: bool) -> str:
            """Turn do-not-disturb on or off. While on, progress updates only go to desktop
            notifications; Noah still answers when spoken to."""
            if self.orchestrator is None:
                return "Do not disturb only applies to voice mode; text mode doesn't speak."
            self.orchestrator.do_not_disturb = on
            return "Do not disturb is on." if on else "Do not disturb is off."

        return [set_verbosity, do_not_disturb]
