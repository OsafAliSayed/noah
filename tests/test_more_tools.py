from pathlib import Path

import pytest

from noah.tools import _proc, windows, write
from noah.tools.registry import ToolError, ToolRegistry
from noah.tools.web import web_tools

WMCTRL = """\
0x01e00003 -1 pop-os Desktop
0x02800007  0 pop-os Noah - Visual Studio Code
0x0300003d  0 pop-os Inbox - Google Chrome
"""


def _fake_run(
    monkeypatch: pytest.MonkeyPatch, outputs: dict[str, str | Exception]
) -> list[list[str]]:
    ran: list[list[str]] = []

    async def fake_run(cmd: list[str], timeout: float = 10) -> str:
        ran.append(cmd)
        result = outputs.get(cmd[0], "")
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(_proc, "run", fake_run)
    return ran


def test_parse_windows_skips_the_desktop():
    assert [t for _, t in windows.parse_windows(WMCTRL)] == [
        "Noah - Visual Studio Code",
        "Inbox - Google Chrome",
    ]


async def test_focus_window_by_title(monkeypatch: pytest.MonkeyPatch):
    ran = _fake_run(monkeypatch, {"wmctrl": WMCTRL})
    assert await windows.focus_window.fn(name="chrome") == "Switched to Inbox - Google Chrome."
    assert ran[-1] == ["wmctrl", "-i", "-a", "0x0300003d"]
    with pytest.raises(ToolError, match="No window title contains 'slack'"):
        await windows.focus_window.fn(name="slack")


async def test_media_without_a_player(monkeypatch: pytest.MonkeyPatch):
    _fake_run(monkeypatch, {"playerctl": ToolError("playerctl failed: No players found")})
    assert await windows.media_status.fn() == "Nothing is playing."
    with pytest.raises(ToolError, match="Nothing is playing"):
        await windows.media_control.fn(action="pause")


async def test_brightness(monkeypatch: pytest.MonkeyPatch):
    ran = _fake_run(monkeypatch, {"brightnessctl": "amdgpu_bl0,backlight,18778,31%,61166\n"})
    assert await windows.get_brightness.fn() == "Brightness is 31%."
    await windows.set_brightness.fn(percent=60)
    assert ran[-1] == ["brightnessctl", "set", "60%"]


async def test_empty_clipboard(monkeypatch: pytest.MonkeyPatch):
    _fake_run(monkeypatch, {"xclip": ToolError("xclip failed: Error: target STRING not available")})
    assert await windows.clipboard_get.fn() == "The clipboard is empty."


def test_screenshot_path():
    from datetime import datetime

    path = windows.screenshot_path(datetime(2026, 10, 14, 9, 5, 7), Path("/home/u"))
    assert path == Path("/home/u/Pictures/Screenshots/Noah 2026-10-14 09-05-07.png")


def test_write_risk_asks_only_to_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "old.txt").write_text("x")
    assert write.write_file.assess({"path": "~/new.txt"}).risk == "safe"
    assert write.write_file.assess({"path": "~/old.txt"}).risk == "confirm"
    assert write.write_file.assess({"path": "~/.ssh/config"}).risk == "deny"
    assert write.write_file.assess({"path": "/etc/hosts"}).risk == "deny"
    assert write.move_file.assess({"source": "~/old.txt", "destination": "~/b"}).risk == "confirm"
    assert write.move_file.assess({"source": "~/old.txt", "destination": "/tmp/x"}).risk == "deny"


def test_write_make_and_move(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert write.make_folder.fn(path="~/codes/demo") == f"Created {tmp_path}/codes/demo."
    assert "already exists" in write.make_folder.fn(path="~/codes/demo")
    write.write_file.fn(path="~/codes/demo/README.md", content="# demo\n")
    write.write_file.fn(path="~/codes/demo/README.md", content="more\n", append=True)
    assert (tmp_path / "codes/demo/README.md").read_text() == "# demo\nmore\n"
    (tmp_path / "archive").mkdir()
    write.move_file.fn(source="~/codes/demo/README.md", destination="~/archive")
    assert (tmp_path / "archive/README.md").exists()
    with pytest.raises(ToolError, match="doesn't exist"):
        write.move_file.fn(source="~/nope", destination="~/x")
    (tmp_path / "x.txt").write_text("")
    with pytest.raises(ToolError, match="already exists"):
        write.move_file.fn(source="~/archive/README.md", destination="~/x.txt")


async def test_web_search_tool():
    queries: list[str] = []

    async def search(query: str) -> str:
        queries.append(query)
        return "It's sunny.\nhttps://weather.example"

    registry = ToolRegistry(web_tools(search))
    result = await registry.execute("web_search", '{"query": "weather in Pune today"}')
    assert result.startswith("It's sunny.")
    assert queries == ["weather in Pune today"]
