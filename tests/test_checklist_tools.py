import json
from pathlib import Path

from noah.tasks.manager import TaskManager
from noah.tasks.store import TaskStore
from noah.tools.checklist import checklist_tools
from noah.tools.registry import ToolRegistry


async def _registry(tmp_path: Path) -> tuple[ToolRegistry, TaskManager]:
    manager = TaskManager(TaskStore(tmp_path / "noah.db"))
    return ToolRegistry(checklist_tools(manager)), manager


def _args(**kw) -> str:
    return json.dumps(kw)


async def test_schemas_are_plain_and_safe(tmp_path: Path):
    registry, manager = await _registry(tmp_path)
    schemas = {s["function"]["name"]: s["function"]["parameters"] for s in registry.schemas()}
    assert set(schemas) == {
        "create_checklist",
        "update_item",
        "add_items",
        "get_tasks",
        "cancel_task",
    }
    assert schemas["update_item"]["properties"]["status"]["enum"] == [
        "pending", "in_progress", "done", "failed", "waiting", "skipped",
    ]  # fmt: skip
    assert all(t.assess({}).risk == "safe" for t in registry.tools.values())
    await manager.store.close()


async def test_checklist_flow_through_the_registry(tmp_path: Path):
    registry, manager = await _registry(tmp_path)
    result = await registry.execute(
        "create_checklist", _args(title="Demo", items=["Make folder", "Init git"])
    )
    assert "Task 1: Demo (running, 0/2)" in result
    assert "1. [pending] Make folder" in result

    result = await registry.execute("update_item", _args(task_id=1, item_id=1, status="done"))
    assert result == "Item 1 is done. Next: item 2, Init git."

    result = await registry.execute("add_items", _args(task_id=1, items=["Open VS Code"]))
    assert "3. [pending] Open VS Code" in result

    result = await registry.execute("get_tasks", "{}")
    assert result.startswith("Task 1: Demo (running, 1/3)")

    await registry.execute(
        "update_item", _args(task_id=1, item_id=2, status="failed", note="git missing")
    )
    result = await registry.execute("update_item", _args(task_id=1, item_id=3, status="skipped"))
    assert "Task 1 is failed. Now give the user a one-sentence summary." in result
    assert await registry.execute("get_tasks", "{}") == "No open tasks."
    assert "2. [failed] Init git (git missing)" in await registry.execute(
        "get_tasks", _args(which="finished")
    )
    await manager.store.close()


async def test_errors_go_back_to_the_model(tmp_path: Path):
    registry, manager = await _registry(tmp_path)
    result = await registry.execute("update_item", _args(task_id=9, item_id=1, status="done"))
    assert result == "Error: There's no task 9. Open tasks: none."
    result = await registry.execute("update_item", _args(task_id=1, item_id=1, status="finished"))
    assert result.startswith("Error: invalid arguments for update_item: status:")
    result = await registry.execute("create_checklist", _args(title="x", items=[]))
    assert "items" in result
    await manager.store.close()


async def test_cancel_task(tmp_path: Path):
    registry, manager = await _registry(tmp_path)
    await registry.execute("create_checklist", _args(title="Demo", items=["a", "b"]))
    result = await registry.execute("cancel_task", _args(task_id=1))
    assert "1. [skipped] a (cancelled)" in result
    await manager.store.close()
