"""Test doubles shared across tests."""

from collections.abc import AsyncIterator

from noah.agent.llm import LLMError, Message, StreamEvent, TextDelta, ToolCall, ToolSchema


class FakeLLM:
    """Replays scripted responses, one per model call, and records what it was sent.

    Each response is a list of events, or an LLMError to raise instead.
    """

    def __init__(self, *responses: list[StreamEvent] | LLMError) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[list[Message], list[ToolSchema]]] = []

    async def stream(
        self, messages: list[Message], tools: list[ToolSchema]
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(([dict(m) for m in messages], tools))
        if not self.responses:
            raise AssertionError("FakeLLM ran out of scripted responses")
        response = self.responses.pop(0)
        if isinstance(response, LLMError):
            raise response
        for event in response:
            yield event


def text(*parts: str) -> list[StreamEvent]:
    return [TextDelta(p) for p in parts]


def call(name: str, arguments: str = "{}", id: str = "call_1") -> ToolCall:
    return ToolCall(id=id, name=name, arguments=arguments)


class FakeTools:
    def __init__(self, results: dict[str, str] | None = None, fail: bool = False) -> None:
        self.results = results or {}
        self.fail = fail
        self.executed: list[tuple[str, str]] = []

    def schemas(self) -> list[ToolSchema]:
        return [
            {"type": "function", "function": {"name": n, "parameters": {}}} for n in self.results
        ]

    async def execute(self, name: str, arguments: str) -> str:
        self.executed.append((name, arguments))
        if self.fail:
            raise RuntimeError("tool exploded")
        return self.results[name]
