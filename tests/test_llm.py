import httpx2 as httpx  # the HTTP client the openai SDK uses
import openai
import pytest
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import (
    Choice,
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)

from noah.agent.llm import LLMError, OpenRouterLLM, TextDelta, ToolCall


def _chunk(delta: ChoiceDelta) -> ChatCompletionChunk:
    return ChatCompletionChunk(
        id="x",
        created=0,
        model="m",
        object="chat.completion.chunk",
        choices=[Choice(index=0, delta=delta)],
    )


def _tool_fragment(index: int, id=None, name=None, arguments=None) -> ChoiceDelta:
    return ChoiceDelta(
        tool_calls=[
            ChoiceDeltaToolCall(
                index=index,
                id=id,
                function=ChoiceDeltaToolCallFunction(name=name, arguments=arguments),
            )
        ]
    )


class _FakeCompletions:
    def __init__(self, chunks=None, error: Exception | None = None):
        self.chunks = chunks or []
        self.error = error
        self.kwargs: dict = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error

        async def gen():
            for c in self.chunks:
                yield c

        return gen()


class _FakeClient:
    def __init__(self, completions: _FakeCompletions):
        self.chat = type("Chat", (), {"completions": completions})()


def _llm(completions: _FakeCompletions) -> OpenRouterLLM:
    return OpenRouterLLM(api_key="k", model="test/model", client=_FakeClient(completions))  # type: ignore[arg-type]


async def _events(llm: OpenRouterLLM, tools=None):
    return [e async for e in llm.stream([{"role": "user", "content": "hi"}], tools or [])]


async def test_streams_text_deltas():
    completions = _FakeCompletions(
        [_chunk(ChoiceDelta(content="Hel")), _chunk(ChoiceDelta(content="lo"))]
    )
    assert await _events(_llm(completions)) == [TextDelta("Hel"), TextDelta("lo")]
    assert completions.kwargs["model"] == "test/model"
    assert completions.kwargs["stream"] is True
    assert "tools" not in completions.kwargs


async def test_assembles_tool_call_fragments():
    completions = _FakeCompletions(
        [
            _chunk(_tool_fragment(0, id="c0", name="set_volume", arguments='{"perc')),
            _chunk(_tool_fragment(1, id="c1", name="notify", arguments="{}")),
            _chunk(_tool_fragment(0, arguments='ent": 30}')),
        ]
    )
    tools = [{"type": "function", "function": {"name": "set_volume"}}]
    events = await _events(_llm(completions), tools)
    assert events == [
        ToolCall(id="c0", name="set_volume", arguments='{"percent": 30}'),
        ToolCall(id="c1", name="notify", arguments="{}"),
    ]
    assert completions.kwargs["tools"] == tools


async def test_missing_id_and_arguments_get_defaults():
    completions = _FakeCompletions([_chunk(_tool_fragment(0, name="get_time"))])
    assert await _events(_llm(completions)) == [
        ToolCall(id="call_0", name="get_time", arguments="{}")
    ]


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            openai.AuthenticationError(
                "bad key",
                response=httpx.Response(401, request=httpx.Request("POST", "http://x")),
                body=None,
            ),
            "rejected the API key",
        ),
        (openai.APIConnectionError(request=httpx.Request("POST", "http://x")), "Couldn't reach"),
    ],
)
async def test_api_errors_become_llm_errors(error: Exception, message: str):
    with pytest.raises(LLMError, match=message):
        await _events(_llm(_FakeCompletions(error=error)))


async def test_records_usage_and_sends_options():
    from openai.types import CompletionUsage

    from noah.agent.llm import Usage

    final = ChatCompletionChunk(
        id="x",
        created=0,
        model="m",
        object="chat.completion.chunk",
        choices=[],
        usage=CompletionUsage.model_validate(
            {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "cost": 0.0003}
        ),
    )
    completions = _FakeCompletions([_chunk(ChoiceDelta(content="Hi")), final])
    llm = OpenRouterLLM(
        api_key="k",
        model="m",
        client=_FakeClient(completions),  # type: ignore[arg-type]
        reasoning_effort="low",
        provider={"data_collection": "deny"},
    )
    assert await _events(llm) == [TextDelta("Hi")]
    assert llm.last_usage == Usage(10, 2, 0.0003)
    assert completions.kwargs["extra_body"] == {
        "reasoning": {"effort": "low"},
        "provider": {"data_collection": "deny"},
    }
    assert completions.kwargs["stream_options"] == {"include_usage": True}
