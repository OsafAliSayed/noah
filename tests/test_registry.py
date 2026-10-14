import json
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import Field

from noah.tools import default_tools
from noah.tools.registry import ToolError, ToolRegistry, tool
from noah.tools.safety import Assessment, AuditLog


@tool(risk="safe")
def greet(
    name: Annotated[str, Field(description="Who to greet.")],
    title: str = "",
    times: Annotated[int, Field(ge=1, le=3)] = 1,
) -> str:
    """Say hello.

    Second paragraph."""
    return " ".join([f"Hello {title} {name}".replace("  ", " ")] * times)


@tool(risk="confirm", describe=lambda a: f"delete {a['path']}")
async def delete(path: str) -> str:
    """Delete a file."""
    return f"deleted {path}"


@tool(risk=lambda a: Assessment("deny", "nope") if a["x"] < 0 else Assessment("safe"))
def signed(x: int) -> dict:
    """Dynamic risk."""
    return {"x": x}


@tool(risk="safe")
def broken() -> str:
    """Always fails."""
    raise ToolError("the speaker is unplugged")


@tool(risk="safe")
def crashes() -> str:
    """Bug."""
    raise KeyError("oops")


class Confirmer:
    def __init__(self, answer: bool):
        self.answer = answer
        self.asked: list[str] = []

    async def __call__(self, action: str) -> bool:
        self.asked.append(action)
        return self.answer


def _registry(tmp_path: Path, answer: bool = False) -> tuple[ToolRegistry, Confirmer]:
    confirmer = Confirmer(answer)
    registry = ToolRegistry(
        [greet, delete, signed, broken, crashes],
        confirm=confirmer,
        audit=AuditLog(tmp_path / "actions.log"),
    )
    return registry, confirmer


def _audit(tmp_path: Path) -> list[dict]:
    return [json.loads(line) for line in (tmp_path / "actions.log").read_text().splitlines()]


def test_schema_from_signature():
    schema = greet.schema()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "greet"
    assert fn["description"] == "Say hello.\n\nSecond paragraph."
    params = fn["parameters"]
    assert params["required"] == ["name"]
    assert params["additionalProperties"] is False
    assert params["properties"]["name"] == {"description": "Who to greet.", "type": "string"}
    # A parameter named "title" survives; pydantic's auto "title" keys don't.
    assert params["properties"]["title"] == {"default": "", "type": "string"}
    assert params["properties"]["times"]["maximum"] == 3
    assert "title" not in params


def test_untyped_parameter_is_rejected():
    with pytest.raises(TypeError, match="type hint"):

        @tool(risk="safe")
        def bad(x):  # type: ignore[no-untyped-def]
            return x


def test_every_default_tool_has_a_valid_schema():
    tools = default_tools()
    names = [t.name for t in tools]
    assert len(names) == len(set(names))
    for t in tools:
        fn = t.schema()["function"]
        assert fn["description"], t.name
        assert fn["parameters"]["type"] == "object"


async def test_safe_tool_runs(tmp_path: Path):
    registry, confirmer = _registry(tmp_path)
    assert (
        await registry.execute("greet", '{"name": "Noah", "times": 2}') == "Hello Noah Hello Noah"
    )
    assert confirmer.asked == []
    assert _audit(tmp_path)[0]["decision"] == "ran"


async def test_non_string_results_become_json(tmp_path: Path):
    registry, _ = _registry(tmp_path)
    assert await registry.execute("signed", '{"x": 5}') == '{"x": 5}'


async def test_invalid_arguments_go_back_to_the_model(tmp_path: Path):
    registry, _ = _registry(tmp_path)
    result = await registry.execute("greet", '{"times": 9}')
    assert result.startswith("Error: invalid arguments for greet:")
    assert "name: Field required" in result
    assert "times:" in result
    assert (await registry.execute("greet", "not json")).startswith("Error: invalid arguments")
    assert (await registry.execute("greet", '{"name": "a", "extra": 1}')).startswith("Error:")


async def test_unknown_tool(tmp_path: Path):
    registry, _ = _registry(tmp_path)
    assert await registry.execute("fly", "{}") == "Error: there is no tool called 'fly'."


async def test_confirm_tool_asks_and_runs_when_allowed(tmp_path: Path):
    registry, confirmer = _registry(tmp_path, answer=True)
    assert await registry.execute("delete", '{"path": "a.txt"}') == "deleted a.txt"
    assert confirmer.asked == ["delete a.txt"]


async def test_confirm_tool_declined(tmp_path: Path):
    registry, _ = _registry(tmp_path, answer=False)
    result = await registry.execute("delete", '{"path": "a.txt"}')
    assert result.startswith("The user declined")
    entry = _audit(tmp_path)[0]
    assert entry["decision"] == "declined"
    assert entry["args"] == {"path": "a.txt"}


async def test_deny_never_runs_or_asks(tmp_path: Path):
    registry, confirmer = _registry(tmp_path, answer=True)
    assert await registry.execute("signed", '{"x": -1}') == "Refused: nope"
    assert confirmer.asked == []
    assert _audit(tmp_path)[0]["decision"] == "refused"


async def test_tool_errors_are_reported(tmp_path: Path):
    registry, _ = _registry(tmp_path)
    assert await registry.execute("broken", "{}") == "Error: the speaker is unplugged"
    assert await registry.execute("crashes", "{}") == "Error: KeyError: 'oops'"


async def test_long_results_are_truncated(tmp_path: Path):
    @tool(risk="safe")
    def long() -> str:
        """Long."""
        return "x" * 5000

    registry = ToolRegistry([long])
    result = await registry.execute("long", "{}")
    assert result.endswith("[truncated, 1000 more characters]")
    assert len(result) < 4100


async def test_confirm_defaults_to_decline():
    registry = ToolRegistry([delete])
    assert (await registry.execute("delete", '{"path": "x"}')).startswith("The user declined")
