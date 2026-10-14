"""The agent's checklist tools. They need the task manager, so they're built per manager."""

from typing import Annotated, Literal

from pydantic import Field

from noah.tasks.manager import TaskError, TaskFilter, TaskManager
from noah.tasks.models import ItemStatus, Task
from noah.tools.registry import Tool, ToolError, tool

CHECKLIST_TOOLS = frozenset(
    {"create_checklist", "update_item", "add_items", "get_tasks", "cancel_task"}
)

_STATUS_LABELS: dict[ItemStatus, str] = {
    "pending": "pending",
    "in_progress": "in progress",
    "done": "done",
    "failed": "failed",
    "waiting": "waiting",
    "skipped": "skipped",
}


def render_task(task: Task) -> str:
    """A task as text for the model and the system prompt."""
    lines = [f"Task {task.id}: {task.title} ({task.status}, {task.done_count}/{len(task.items)})"]
    for item in task.items:
        note = f" ({item.note})" if item.note else ""
        lines.append(f"  {item.number}. [{_STATUS_LABELS[item.status]}] {item.text}{note}")
    return "\n".join(lines)


def _next_hint(task: Task) -> str:
    if task.finished:
        return f"Task {task.id} is {task.status}. Now give the user a one-sentence summary."
    item = task.next_item()
    if item is None or item.status != "pending":
        return ""
    return f"Next: item {item.number}, {item.text}."


def checklist_tools(manager: TaskManager) -> list[Tool]:
    @tool(risk="safe")
    async def create_checklist(
        title: Annotated[str, Field(description="Short name for the task, e.g. 'Set up scraper'.")],
        items: Annotated[
            list[str],
            Field(min_length=1, description="The steps in order, each a short imperative phrase."),
        ],
    ) -> str:
        """Make a checklist before starting any request that takes more than one step. Then
        work through it, marking each item in_progress when you start it and done when it's
        finished."""
        try:
            task = await manager.create(title, items)
        except TaskError as e:
            raise ToolError(str(e)) from None
        return f"Created.\n{render_task(task)}\nStart with item 1."

    @tool(risk="safe")
    async def update_item(
        task_id: int,
        item_id: Annotated[int, Field(description="The item's number in the checklist.")],
        status: ItemStatus,
        note: Annotated[
            str | None, Field(description="Short reason, required when failed or skipped.")
        ] = None,
    ) -> str:
        """Change a checklist item's status as you work."""
        try:
            task = await manager.update_item(task_id, item_id, status, note)
        except TaskError as e:
            raise ToolError(str(e)) from None
        hint = _next_hint(task)
        return f"Item {item_id} is {_STATUS_LABELS[status]}." + (f" {hint}" if hint else "")

    @tool(risk="safe")
    async def add_items(
        task_id: int,
        items: Annotated[list[str], Field(min_length=1, description="Steps to add at the end.")],
    ) -> str:
        """Add steps you discover along the way to an existing checklist."""
        try:
            task = await manager.add_items(task_id, items)
        except TaskError as e:
            raise ToolError(str(e)) from None
        return f"Added.\n{render_task(task)}"

    @tool(risk="safe")
    async def get_tasks(
        which: Annotated[
            Literal["open", "finished", "all"],
            Field(description="open: not finished yet. finished: done or failed."),
        ] = "open",
    ) -> str:
        """List checklists and their items, to answer 'what are you working on?' or
        'what did you do earlier?'."""
        filt: TaskFilter = which
        tasks = await manager.get_tasks(filt)
        if not tasks:
            return f"No {which} tasks." if which != "all" else "No tasks yet."
        return "\n".join(render_task(t) for t in tasks)

    @tool(risk="safe")
    async def cancel_task(task_id: int) -> str:
        """Stop a checklist when the user says to cancel it, or when it was left unfinished and
        is no longer wanted. Unfinished items are marked skipped."""
        try:
            task = await manager.cancel(task_id)
        except TaskError as e:
            raise ToolError(str(e)) from None
        return f"Cancelled.\n{render_task(task)}"

    return [create_checklist, update_item, add_items, get_tasks, cancel_task]
