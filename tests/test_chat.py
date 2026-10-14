import re

from noah.agent.llm import LLMError
from noah.agent.loop import Agent
from noah.chat import repl, run_turn

from .fakes import FakeLLM, FakeTools, call, text

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _agent(llm: FakeLLM, tools: FakeTools | None = None) -> Agent:
    return Agent(llm, tools or FakeTools(), system_prompt=lambda: "S")


class _Out:
    def __init__(self):
        self.parts: list[str] = []

    def __call__(self, s: str) -> None:
        self.parts.append(s)

    @property
    def text(self) -> str:
        return ANSI.sub("", "".join(self.parts))


async def test_turn_prints_tool_lines_on_their_own_lines():
    llm = FakeLLM([*text("Checking."), call("get_time")], text("Noon."))
    out = _Out()
    ok = await run_turn(_agent(llm, FakeTools({"get_time": "12:00"})), "time?", out)
    assert ok
    assert out.text == "Checking.\n  ↳ get_time({})\n    12:00\nNoon.\n"


async def test_turn_reports_llm_error():
    out = _Out()
    ok = await run_turn(_agent(FakeLLM(LLMError("Couldn't reach OpenRouter."))), "hi", out)
    assert not ok
    assert "✗ Couldn't reach OpenRouter." in out.text


async def test_repl_reset_and_exit():
    llm = FakeLLM(text("Hi."))
    agent = _agent(llm)
    lines = iter(["hello", "", "/reset", "/exit"])
    out = _Out()
    await repl(agent, read_line=lambda _: next(lines), write=out)
    assert "noah> Hi." in out.text
    assert "Conversation cleared." in out.text
    assert agent.history == []


async def test_repl_exits_on_eof():
    def eof(_):
        raise EOFError

    await repl(_agent(FakeLLM()), read_line=eof, write=_Out())


async def test_terminal_confirm_accepts_yes_only():
    from noah.chat import terminal_confirm

    assert await terminal_confirm(lambda _: "y", interactive=True)("run `x`")
    assert await terminal_confirm(lambda _: " YES ", interactive=True)("run `x`")
    assert not await terminal_confirm(lambda _: "", interactive=True)("run `x`")
    assert not await terminal_confirm(lambda _: "y", interactive=False)("run `x`")
