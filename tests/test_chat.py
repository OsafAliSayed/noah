import dataclasses
import re
from pathlib import Path

import pytest

from noah.agent.llm import LLMError
from noah.agent.loop import Agent
from noah.chat import Renderer, repl, run_turn, terminal_confirm, unfinished_summary
from noah.config import load_settings
from noah.session import Noah, start
from noah.tools.registry import ToolRegistry
from noah.tools.safety import CannotAsk

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


async def _noah(
    tmp_path: Path, llm: FakeLLM, renderer: Renderer, answer: bool = True, max_steps: int = 8
) -> Noah:
    settings = load_settings(env={"XDG_DATA_HOME": str(tmp_path), "XDG_STATE_HOME": str(tmp_path)})
    settings.tasks.notifications = False
    settings.llm.max_steps = max_steps

    async def confirm(action: str) -> bool:
        return answer

    return await start(settings, confirm=confirm, say=renderer.announce, llm=llm)


async def test_turn_prints_tool_lines_on_their_own_lines():
    llm = FakeLLM([*text("Checking.\n\n"), call("get_time")], text("Noon."))
    out = _Out()
    ok = await run_turn(_agent(llm, FakeTools({"get_time": "12:00"})), "time?", Renderer(out))
    assert ok
    assert out.text == "Checking.\n\n  ↳ get_time({})\n    12:00\nNoon.\n"


async def test_leading_newlines_after_a_tool_line_are_dropped():
    llm = FakeLLM([call("get_time")], text("\n\nNoon."))
    out = _Out()
    await run_turn(_agent(llm, FakeTools({"get_time": "12:00"})), "time?", Renderer(out))
    assert out.text == "  ↳ get_time({})\n    12:00\nNoon.\n"


async def test_turn_reports_llm_error():
    out = _Out()
    agent = _agent(FakeLLM(LLMError("Couldn't reach OpenRouter.")))
    ok = await run_turn(agent, "hi", Renderer(out))
    assert not ok
    assert "✗ Couldn't reach OpenRouter." in out.text


async def test_repl_reset_and_exit(tmp_path: Path):
    out = _Out()
    renderer = Renderer(out)
    noah = await _noah(tmp_path, FakeLLM(text("Hi.")), renderer)
    lines = iter(["hello", "", "/reset", "/exit"])
    await repl(noah, renderer, read_line=lambda _: next(lines))
    assert "noah> Hi." in out.text
    assert "Conversation cleared." in out.text
    assert noah.agent.history == []
    await noah.close()


async def test_repl_exits_on_eof(tmp_path: Path):
    def eof(_):
        raise EOFError

    renderer = Renderer(_Out())
    noah = await _noah(tmp_path, FakeLLM(), renderer)
    await repl(noah, renderer, read_line=eof)
    await noah.close()


async def test_terminal_confirm_accepts_yes_only():
    assert await terminal_confirm(lambda _: "y", interactive=True)("run `x`")
    assert await terminal_confirm(lambda _: " YES ", interactive=True)("run `x`")
    assert not await terminal_confirm(lambda _: "", interactive=True)("run `x`")


async def test_terminal_confirm_without_a_terminal_cannot_ask():
    with pytest.raises(CannotAsk):
        await terminal_confirm(lambda _: "y", interactive=False)("run `x`")


PLAN = '{"title": "Set up demo", "items": ["Make the folder", "Init git"]}'


async def test_checklist_turn_announces_progress_and_hides_checklist_calls(tmp_path: Path):
    async def fake_shell(command: str) -> str:
        return "exit code 0\n(no output)"

    llm = FakeLLM(
        [call("create_checklist", PLAN)],
        [call("update_item", '{"task_id": 1, "item_id": 1, "status": "in_progress"}', id="a"),
         call("run_shell", '{"command": "mkdir -p ~/demo"}', id="b")],
        [call("update_item", '{"task_id": 1, "item_id": 1, "status": "done"}', id="c"),
         call("update_item", '{"task_id": 1, "item_id": 2, "status": "in_progress"}', id="d"),
         call("run_shell", '{"command": "git -C ~/demo init"}', id="e")],
        [call("update_item", '{"task_id": 1, "item_id": 2, "status": "done"}', id="f")],
        text("Done. The demo folder is ready with git."),
    )  # fmt: skip
    out = _Out()
    renderer = Renderer(out)
    noah = await _noah(tmp_path, llm, renderer, max_steps=2)
    registry = noah.agent.tools
    assert isinstance(registry, ToolRegistry)
    registry.tools["run_shell"] = dataclasses.replace(registry.tools["run_shell"], fn=fake_shell)
    assert await run_turn(noah.agent, "set up demo", renderer, noah)

    assert out.text.splitlines() == [
        "  ● Got it. Two steps: make the folder and init git.",
        '  ↳ run_shell({"command": "mkdir -p ~/demo"})',
        "    exit code 0 (no output)",
        '  ↳ run_shell({"command": "git -C ~/demo init"})',
        # Asking to run git flushes the grouped progress first.
        "  ● One of two done: make the folder.",
        "    exit code 0 (no output)",
        "  ● All done: Set up demo.",
        "Done. The demo folder is ready with git.",
    ]
    # The model kept going past the plain step limit, and the checklist is stored.
    assert len(llm.calls) == 5
    (task,) = await noah.tasks.get_tasks("all")
    assert task.status == "done"
    await noah.close()


async def test_unfinished_summary():
    assert unfinished_summary([]) is None


async def test_only_recent_checklists_raise_the_step_limit(tmp_path: Path):
    from datetime import timedelta

    out = _Out()
    noah = await _noah(tmp_path, FakeLLM(), Renderer(out))
    limit = noah.agent.max_steps
    assert callable(limit)
    assert limit() == 8
    now = noah.tasks.clock()
    noah.tasks.clock = lambda: now - timedelta(hours=13)
    await noah.tasks.create("Old", ["a"])
    noah.tasks.clock = lambda: now
    assert limit() == 8
    await noah.tasks.create("New", ["b"])
    assert limit() == 40
    await noah.close()
