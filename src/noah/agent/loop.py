"""The agent loop: ask the model, run the tools it calls, feed results back, until it replies.

`Agent.run` yields events as they happen so the caller can print or speak text while the
model is still writing, and show tool activity.
"""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Protocol

from noah.agent.llm import LLM, Message, TextDelta, ToolCall, ToolSchema


class ToolExecutor(Protocol):
    def schemas(self) -> list[ToolSchema]:
        """Tool definitions in OpenAI function-calling format."""
        ...

    async def execute(self, name: str, arguments: str) -> str:
        """Run a tool and return its result as text for the model."""
        ...


@dataclass(frozen=True)
class ToolStarted:
    call: ToolCall


@dataclass(frozen=True)
class ToolFinished:
    call: ToolCall
    result: str


@dataclass(frozen=True)
class StepLimitReached:
    max_steps: int


AgentEvent = TextDelta | ToolStarted | ToolFinished | StepLimitReached


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: ToolExecutor,
        system_prompt: Callable[[], str],
        max_steps: int | Callable[[], int] = 8,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.system_prompt = system_prompt
        # A callable limit is re-read every step, so it can grow once a checklist exists.
        self.max_steps = max_steps
        # The conversation without the system prompt, which is rebuilt on every call.
        self.history: list[Message] = []

    def reset(self) -> None:
        self.history.clear()

    async def run(self, user_text: str) -> AsyncIterator[AgentEvent]:
        """Handle one user turn. Raises LLMError if the model can't be reached; the turn is
        then removed from history so the conversation stays consistent."""
        turn_start = len(self.history)
        self.history.append({"role": "user", "content": user_text})
        try:
            steps = limit = 0
            while True:
                # Only ever raise the limit within a turn: finishing a checklist mustn't cut
                # off the summary that follows it.
                limit = max(limit, self._step_limit())
                if steps >= limit:
                    break
                steps += 1
                text, calls = [], []
                messages = [{"role": "system", "content": self.system_prompt()}, *self.history]
                async for event in self.llm.stream(messages, self.tools.schemas()):
                    if isinstance(event, TextDelta):
                        text.append(event.text)
                        yield event
                    else:
                        calls.append(event)

                self.history.append(_assistant_message("".join(text), calls))
                if not calls:
                    return

                for call in calls:
                    yield ToolStarted(call)
                    result = await self._execute(call)
                    self.history.append(
                        {"role": "tool", "tool_call_id": call.id, "content": result}
                    )
                    yield ToolFinished(call, result)

            yield StepLimitReached(steps)
        except BaseException:
            del self.history[turn_start:]
            raise

    def _step_limit(self) -> int:
        return self.max_steps() if callable(self.max_steps) else self.max_steps

    async def _execute(self, call: ToolCall) -> str:
        # A broken tool must not end the turn: report the error to the model and let it recover.
        try:
            return await self.tools.execute(call.name, call.arguments)
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"


def _assistant_message(text: str, calls: list[ToolCall]) -> Message:
    message: Message = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in calls
        ]
    return message
