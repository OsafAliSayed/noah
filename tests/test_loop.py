import pytest

from noah.agent.llm import LLMError, TextDelta
from noah.agent.loop import Agent, StepLimitReached, ToolFinished, ToolStarted

from .fakes import FakeLLM, FakeTools, call, text


def _agent(llm: FakeLLM, tools: FakeTools | None = None, max_steps: int = 8) -> Agent:
    return Agent(llm, tools or FakeTools(), system_prompt=lambda: "SYSTEM", max_steps=max_steps)


async def _collect(agent: Agent, user_text: str) -> list:
    return [event async for event in agent.run(user_text)]


async def test_plain_reply_streams_text_and_records_history():
    llm = FakeLLM(text("Hi ", "there."))
    agent = _agent(llm)

    events = await _collect(agent, "hello")

    assert events == [TextDelta("Hi "), TextDelta("there.")]
    assert agent.history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hi there."},
    ]
    messages, _ = llm.calls[0]
    assert messages[0] == {"role": "system", "content": "SYSTEM"}


async def test_tool_call_then_answer():
    llm = FakeLLM([call("get_time")], text("It's noon."))
    tools = FakeTools({"get_time": "12:00"})
    agent = _agent(llm, tools)

    events = await _collect(agent, "what time is it")

    assert tools.executed == [("get_time", "{}")]
    assert [type(e) for e in events] == [ToolStarted, ToolFinished, TextDelta]
    assert agent.history[1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_time", "arguments": "{}"},
            }
        ],
    }
    assert agent.history[2] == {"role": "tool", "tool_call_id": "call_1", "content": "12:00"}
    # The second model call sees the tool result.
    second_messages, tools_sent = llm.calls[1]
    assert second_messages[-1]["role"] == "tool"
    assert tools_sent[0]["function"]["name"] == "get_time"


async def test_several_tool_calls_in_one_step_all_run():
    llm = FakeLLM([call("a", id="1"), call("b", id="2")], text("Done."))
    tools = FakeTools({"a": "A", "b": "B"})
    await _collect(_agent(llm, tools), "do both")
    assert [name for name, _ in tools.executed] == ["a", "b"]


async def test_tool_exception_is_reported_to_model_not_raised():
    llm = FakeLLM([call("get_time")], text("Sorry, that failed."))
    agent = _agent(llm, FakeTools({"get_time": ""}, fail=True))

    events = await _collect(agent, "time?")

    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.result == "Error: RuntimeError: tool exploded"
    assert agent.history[-1]["content"] == "Sorry, that failed."


async def test_step_limit_stops_a_looping_model():
    llm = FakeLLM([call("a")], [call("a")], [call("a")])
    agent = _agent(llm, FakeTools({"a": "again"}), max_steps=3)

    events = await _collect(agent, "loop forever")

    assert events[-1] == StepLimitReached(3)
    assert len(llm.calls) == 3


async def test_llm_error_rolls_back_the_turn():
    llm = FakeLLM(text("First answer."), [call("a")], LLMError("network down"))
    agent = _agent(llm, FakeTools({"a": "x"}))
    await _collect(agent, "first")
    history_before = list(agent.history)

    with pytest.raises(LLMError):
        await _collect(agent, "second")

    assert agent.history == history_before


async def test_system_prompt_is_rebuilt_every_call():
    prompts = iter(["one", "two"])
    llm = FakeLLM([call("a")], text("ok"))
    agent = Agent(llm, FakeTools({"a": "x"}), system_prompt=lambda: next(prompts))
    await _collect(agent, "go")
    assert [c[0][0]["content"] for c in llm.calls] == ["one", "two"]


async def test_reset_clears_history():
    agent = _agent(FakeLLM(text("hi")))
    await _collect(agent, "hello")
    agent.reset()
    assert agent.history == []
