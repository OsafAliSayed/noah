"""The LLM client: streams a chat completion from OpenRouter as text deltas and tool calls.

Messages and tool schemas use the OpenAI chat format, which OpenRouter speaks for every model.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

import openai
from openai import AsyncOpenAI

Message = dict[str, Any]
ToolSchema = dict[str, Any]


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON, as the model wrote it


StreamEvent = TextDelta | ToolCall


class LLMError(Exception):
    """The request to the model failed (network, auth, rate limit, bad response)."""


class LLM(Protocol):
    def stream(
        self, messages: list[Message], tools: list[ToolSchema]
    ) -> AsyncIterator[StreamEvent]:
        """Yield text as it arrives, then each complete tool call once the stream ends."""
        ...


class OpenRouterLLM:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers={"X-Title": "Noah"},  # app name on OpenRouter's dashboard
        )

    async def stream(
        self, messages: list[Message], tools: list[ToolSchema]
    ) -> AsyncIterator[StreamEvent]:
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "stream": True}
        if tools:
            kwargs["tools"] = tools

        # Tool calls arrive in fragments keyed by index: id and name first, arguments in pieces.
        partial: dict[int, dict[str, str]] = {}
        try:
            response = await self._client.chat.completions.create(**kwargs)
            async for chunk in response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    yield TextDelta(delta.content)
                for fragment in delta.tool_calls or []:
                    call = partial.setdefault(
                        fragment.index, {"id": "", "name": "", "arguments": ""}
                    )
                    if fragment.id:
                        call["id"] = fragment.id
                    if fragment.function:
                        call["name"] += fragment.function.name or ""
                        call["arguments"] += fragment.function.arguments or ""
        except openai.APIError as e:
            raise LLMError(_describe(e)) from e

        for index, call in sorted(partial.items()):
            yield ToolCall(
                id=call["id"] or f"call_{index}",
                name=call["name"],
                arguments=call["arguments"] or "{}",
            )


def _describe(error: openai.APIError) -> str:
    if isinstance(error, openai.AuthenticationError):
        return "OpenRouter rejected the API key. Check OPENROUTER_API_KEY."
    if isinstance(error, openai.RateLimitError):
        return "OpenRouter rate limit or credit limit reached. Try again shortly."
    if isinstance(error, openai.APIConnectionError):
        return "Couldn't reach OpenRouter. Check the internet connection."
    if isinstance(error, openai.APIStatusError):
        return f"OpenRouter returned {error.status_code}: {error.message}"
    return f"OpenRouter error: {error.message}"
