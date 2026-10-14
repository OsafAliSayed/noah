import pytest
from typer.testing import CliRunner

from noah import __version__
from noah.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


@pytest.mark.parametrize("args", [["run"], ["tasks"]])
def test_unbuilt_commands_exit_nonzero(args: list[str]):
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "Not built yet" in result.output


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
