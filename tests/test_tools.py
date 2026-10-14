from pathlib import Path

import pytest

from noah.tools import _proc, apps, desktop, files, shell
from noah.tools.apps import DesktopApp, find_apps, match_app
from noah.tools.registry import ToolError


def _desktop(directory: Path, file_id: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{file_id}.desktop").write_text(body)


def test_find_apps_parses_and_filters(tmp_path: Path):
    user, system = tmp_path / "user", tmp_path / "system"
    _desktop(system, "firefox", "[Desktop Entry]\nType=Application\nName=Firefox Web Browser\n"
             "[Desktop Action new]\nName=New Window\n")  # fmt: skip
    _desktop(system, "code", "[Desktop Entry]\nType=Application\nName=Visual Studio Code\n")
    _desktop(system, "hidden", "[Desktop Entry]\nType=Application\nName=Hidden\nNoDisplay=true\n")
    _desktop(system, "link", "[Desktop Entry]\nType=Link\nName=A Link\n")
    _desktop(user, "code", "[Desktop Entry]\nType=Application\nName=VS Code (user)\n")

    found = {a.id: a.name for a in find_apps([user, system])}
    assert found == {"code": "VS Code (user)", "firefox": "Firefox Web Browser"}


APPS = [
    DesktopApp("firefox", "Firefox Web Browser"),
    DesktopApp("code", "Visual Studio Code"),
    DesktopApp("org.gnome.Nautilus", "Files"),
    DesktopApp("org.gnome.Terminal", "Terminal"),
    DesktopApp("gnome-terminal-preferences", "Terminal Preferences"),
    DesktopApp("com.slack.Slack", "Slack"),
]


@pytest.mark.parametrize(
    ("query", "app_id"),
    [
        ("firefox", "firefox"),
        ("Firefox", "firefox"),
        ("files", "org.gnome.Nautilus"),
        ("nautilus", "org.gnome.Nautilus"),
        ("terminal", "org.gnome.Terminal"),  # exact name beats the longer one
        ("visual studio", "code"),
        ("code", "code"),
        ("slack", "com.slack.Slack"),
    ],
)
def test_match_app(query: str, app_id: str):
    match = match_app(query, APPS)
    assert isinstance(match, DesktopApp)
    assert match.id == app_id


def test_no_match_suggests_close_names():
    assert match_app("Slak", APPS) == ["Slack"]
    assert match_app("photoshop", APPS) == []


async def test_open_app_launches_with_gtk_launch(monkeypatch: pytest.MonkeyPatch):
    launched: list[list[str]] = []

    async def fake_launch(cmd: list[str], settle: float = 2) -> None:
        launched.append(cmd)

    monkeypatch.setattr(apps, "find_apps", lambda: APPS)
    monkeypatch.setattr(_proc, "launch", fake_launch)
    assert await apps.open_app.fn(name="vs code") == "Opened Visual Studio Code."
    assert launched == [["gtk-launch", "code"]]
    with pytest.raises(ToolError, match="Did you mean Slack"):
        await apps.open_app.fn(name="slak")


async def test_open_path_checks_existence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    launched: list[list[str]] = []

    async def fake_launch(cmd: list[str], settle: float = 2) -> None:
        launched.append(cmd)

    monkeypatch.setattr(_proc, "launch", fake_launch)
    await apps.open_path_or_url.fn(target="https://example.com")
    await apps.open_path_or_url.fn(target=str(tmp_path))
    assert launched == [["xdg-open", "https://example.com"], ["xdg-open", str(tmp_path)]]
    with pytest.raises(ToolError, match="doesn't exist"):
        await apps.open_path_or_url.fn(target=str(tmp_path / "nope"))


@pytest.mark.parametrize(
    ("output", "expected"),
    [("Volume: 0.57\n", (57, False)), ("Volume: 0.30 [MUTED]\n", (30, True))],
)
def test_parse_volume(output: str, expected: tuple[int, bool]):
    assert desktop.parse_volume(output) == expected


async def test_set_volume_calls_wpctl(monkeypatch: pytest.MonkeyPatch):
    ran: list[list[str]] = []

    async def fake_run(cmd: list[str], timeout: float = 10) -> str:
        ran.append(cmd)
        return ""

    monkeypatch.setattr(_proc, "run", fake_run)
    assert await desktop.set_volume.fn(percent=30) == "Volume set to 30%."
    assert ran[0] == ["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", "30%"]


def test_system_info_reports_memory_and_disk():
    info = desktop.system_info.fn()
    assert "Memory:" in info
    assert "Disk (home):" in info


def test_list_and_read_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "docs").mkdir()
    (tmp_path / "notes.txt").write_text("buy milk")
    (tmp_path / ".hidden").write_text("")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01")

    listing = files.list_files.fn(path="~")
    assert listing.splitlines()[1:] == ["docs/", "blob.bin", "notes.txt"]
    assert ".hidden" in files.list_files.fn(path="~", show_hidden=True)
    assert files.read_file.fn(path="notes.txt") == "buy milk"
    with pytest.raises(ToolError, match="binary"):
        files.read_file.fn(path="blob.bin")
    with pytest.raises(ToolError, match="not a folder"):
        files.list_files.fn(path="notes.txt")


def test_read_file_truncates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "big.txt").write_text("a" * 50_000)
    assert files.read_file.fn(path="big.txt").endswith("[file continues]")


async def test_run_shell_captures_output_and_exit_code(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert await shell.run_shell.fn(command="echo hi; echo err >&2") == "exit code 0\nhi\nerr"
    assert (await shell.run_shell.fn(command="exit 3")).startswith("exit code 3")
    assert await shell.run_shell.fn(command="pwd") == f"exit code 0\n{tmp_path}"


async def test_run_shell_times_out(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(shell, "TIMEOUT_SECONDS", 0.2)
    with pytest.raises(ToolError, match="didn't finish"):
        await shell.run_shell.fn(command="sleep 5")


async def test_proc_run_reports_missing_program_with_apt_hint():
    with pytest.raises(ToolError, match="sudo apt install"):
        await _proc.run(["definitely-not-a-real-program-xyz"])


async def test_proc_run_reports_failures():
    with pytest.raises(ToolError, match="failed: boom"):
        await _proc.run(["bash", "-c", "echo boom >&2; exit 1"])
    assert await _proc.run(["echo", "ok"]) == "ok\n"


async def test_proc_launch_returns_for_long_running_programs():
    await _proc.launch(["sleep", "5"], settle=0.1)
    with pytest.raises(ToolError, match="failed"):
        await _proc.launch(["false"], settle=1)
