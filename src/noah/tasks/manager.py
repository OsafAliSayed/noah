"""Creates and updates checklists, and tells subscribers about every status change.

The manager keeps the unfinished tasks in memory so the system prompt (which is built
synchronously) can list them, and so the confirmation gate can find the item being worked on.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from noah.tasks.models import Item, ItemStatus, Task
from noah.tasks.store import TaskStore
from noah.tools.safety import Confirm

EventKind = Literal["created", "item_changed", "items_added"]
TaskFilter = Literal["open", "finished", "all"]
Clock = Callable[[], datetime]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskEvent:
    kind: EventKind
    task: Task  # the task after the change
    item: Item | None = None  # the item that changed, for item_changed
    previous: ItemStatus | None = None  # its status before
    asking: bool = False  # waiting because a confirmation prompt is about to ask the user


Subscriber = Callable[[TaskEvent], Awaitable[None]]


class TaskError(Exception):
    """A request that refers to a task or item that doesn't exist, or is otherwise invalid."""


def _now() -> datetime:
    return datetime.now().astimezone()


class TaskManager:
    def __init__(self, store: TaskStore, clock: Clock = _now) -> None:
        self.store = store
        self.clock = clock
        self.subscribers: list[Subscriber] = []
        self._open: dict[int, Task] = {}

    async def load(self, limit: int = 50) -> list[Task]:
        """Read unfinished tasks from the store, e.g. after a restart. Returns them."""
        self._open = {t.id: t for t in await self.store.recent(limit) if not t.finished}
        return self.open_tasks()

    def open_tasks(self) -> list[Task]:
        return sorted(self._open.values(), key=lambda t: t.id)

    def active_item(self) -> Item | None:
        """The item being worked on now: in progress, in the most recently updated open task."""
        for task in sorted(self._open.values(), key=lambda t: t.updated_at, reverse=True):
            for item in task.items:
                if item.status == "in_progress":
                    return item
        return None

    async def create(self, title: str, items: list[str]) -> Task:
        title, texts = title.strip(), [t.strip() for t in items if t.strip()]
        if not title:
            raise TaskError("A checklist needs a title.")
        if not texts:
            raise TaskError("A checklist needs at least one item.")
        task = await self.store.create(title, texts, self.clock())
        await self._changed(TaskEvent("created", task))
        return task

    async def add_items(self, task_id: int, items: list[str]) -> Task:
        await self._get(task_id)
        texts = [t.strip() for t in items if t.strip()]
        if not texts:
            raise TaskError("No items to add.")
        task = await self.store.add_items(task_id, texts, self.clock())
        await self._changed(TaskEvent("items_added", task))
        return task

    async def update_item(
        self,
        task_id: int,
        number: int,
        status: ItemStatus,
        note: str | None = None,
        asking: bool = False,
    ) -> Task:
        before = await self._get(task_id)
        item = before.item(number)
        if item is None:
            raise TaskError(
                f"Task {task_id} has no item {number}; it has 1 to {len(before.items)}."
            )
        task = await self.store.set_item(task_id, number, status, note, self.clock())
        if status != item.status:
            event = TaskEvent("item_changed", task, task.item(number), item.status, asking)
            await self._changed(event)
        else:
            self._track(task)
        return task

    async def cancel(self, task_id: int, note: str = "cancelled") -> Task:
        """Stop a task: every item that hasn't finished is marked skipped."""
        task = await self._get(task_id)
        for item in task.items:
            if item.status not in ("done", "failed", "skipped"):
                task = await self.update_item(task_id, item.number, "skipped", note)
        return task

    async def get_tasks(self, which: TaskFilter = "open", limit: int = 10) -> list[Task]:
        if which == "open":
            return self.open_tasks()[-limit:]
        tasks = await self.store.recent(limit if which == "all" else limit * 3)
        if which == "finished":
            tasks = [t for t in tasks if t.finished][:limit]
        return tasks

    def gate(self, confirm: Confirm) -> Confirm:
        """Wrap a confirmation prompt so the item being worked on shows as waiting meanwhile."""

        async def ask(action: str) -> bool:
            item = self.active_item()
            if item is None:
                return await confirm(action)
            await self.update_item(item.task_id, item.number, "waiting", action, asking=True)
            try:
                return await confirm(action)
            finally:
                await self.update_item(item.task_id, item.number, "in_progress")

        return ask

    async def _get(self, task_id: int) -> Task:
        task = self._open.get(task_id) or await self.store.get(task_id)
        if task is None:
            known = ", ".join(str(t.id) for t in self.open_tasks()) or "none"
            raise TaskError(f"There's no task {task_id}. Open tasks: {known}.")
        return task

    def _track(self, task: Task) -> None:
        if task.finished:
            self._open.pop(task.id, None)
        else:
            self._open[task.id] = task

    async def _changed(self, event: TaskEvent) -> None:
        self._track(event.task)
        for subscriber in list(self.subscribers):
            # A broken announcer or notifier must not fail the tool call that changed the task.
            try:
                await subscriber(event)
            except Exception:
                log.exception("task event subscriber failed")
