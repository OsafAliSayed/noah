import pytest
from typer.testing import CliRunner

from noah import __version__
from noah.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_tasks_lists_stored_checklists(monkeypatch: pytest.MonkeyPatch, tmp_path):
    import asyncio
    from datetime import UTC, datetime

    from noah.tasks.store import TaskStore

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = runner.invoke(app, ["tasks"])
    assert result.exit_code == 0
    assert "No checklists yet." in result.output

    async def seed() -> None:
        store = TaskStore(tmp_path / "noah" / "noah.db")
        task = await store.create("Set up demo", ["mkdir", "git init"], datetime.now(UTC))
        await store.set_item(task.id, 1, "done", None, datetime.now(UTC))
        await store.close()

    asyncio.run(seed())
    result = runner.invoke(app, ["tasks"])
    assert result.exit_code == 0
    assert "#1 Set up demo  running 1/2" in result.output
    assert "✓ 1. mkdir" in result.output
    assert "○ 2. git init" in result.output


def test_doctor_reports_missing_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "OpenRouter API key: not set" in result.output


@pytest.mark.parametrize("args", [["chat"], ["say", "hello"]])
def test_chat_and_say_need_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path, args: list[str]):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY is not set" in result.output
