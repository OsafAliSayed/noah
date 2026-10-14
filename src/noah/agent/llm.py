"""The LLM client: streams a chat completion from OpenRouter as text deltas and tool calls.

Messages and tool schemas use the OpenAI chat format, which OpenRouter speaks for every model.
"""

import time
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


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    cost: float | None  # US dollars, as reported by OpenRouter


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
        reasoning_effort: str | None = None,
        provider: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        fallback_models: list[str] | None = None,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.provider = provider
        self.max_tokens = max_tokens
        self.fallback_models = fallback_models or []
        # Token counts and cost of the most recent call, if the server reported them.
        self.last_usage: Usage | None = None
        # Seconds from sending the most recent request to its first text or tool-call fragment.
        self.last_ttft: float | None = None
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers={"X-Title": "Noah"},  # app name on OpenRouter's dashboard
        )

    async def stream(
        self, messages: list[Message], tools: list[ToolSchema]
    ) -> AsyncIterator[StreamEvent]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens
        extra: dict[str, Any] = {}
        if self.reasoning_effort:
            extra["reasoning"] = {"effort": self.reasoning_effort}
        if self.provider:
            extra["provider"] = self.provider
        if self.fallback_models:
            extra["models"] = [self.model, *self.fallback_models]
        if extra:
            kwargs["extra_body"] = extra
        self.last_usage = self.last_ttft = None
        started = time.perf_counter()

        # Tool calls arrive in fragments keyed by index: id and name first, arguments in pieces.
        partial: dict[int, dict[str, str]] = {}
        try:
            response = await self._client.chat.completions.create(**kwargs)
            async for chunk in response:
                if chunk.usage:
                    cost = (chunk.usage.model_extra or {}).get("cost")
                    self.last_usage = Usage(
                        chunk.usage.prompt_tokens,
                        chunk.usage.completion_tokens,
                        float(cost) if cost is not None else None,
                    )
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if self.last_ttft is None and (delta.content or delta.tool_calls):
                    self.last_ttft = time.perf_counter() - started
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
