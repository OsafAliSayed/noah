"""Tasks and their checklist items. A task's status is derived from its items."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, get_args

ItemStatus = Literal["pending", "in_progress", "done", "failed", "waiting", "skipped"]
TaskStatus = Literal["running", "waiting", "done", "failed"]

ITEM_STATUSES: tuple[ItemStatus, ...] = get_args(ItemStatus)
FINISHED: frozenset[ItemStatus] = frozenset({"done", "failed", "skipped"})


@dataclass(frozen=True)
class Item:
    task_id: int
    number: int  # 1-based position in the checklist; what the model calls item_id
    text: str
    status: ItemStatus = "pending"
    note: str | None = None


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    items: tuple[Item, ...] = field(default_factory=tuple)

    @property
    def status(self) -> TaskStatus:
        return task_status(self.items)

    @property
    def finished(self) -> bool:
        return self.status in ("done", "failed")

    @property
    def done_count(self) -> int:
        return sum(i.status == "done" for i in self.items)

    def item(self, number: int) -> Item | None:
        return next((i for i in self.items if i.number == number), None)

    def next_item(self) -> Item | None:
        return next((i for i in self.items if i.status not in FINISHED), None)


def task_status(items: tuple[Item, ...]) -> TaskStatus:
    """Waiting beats running: a task that needs the user says so. Once every item is finished,
    any failure makes the task failed; skipped items count as finished, not as failures."""
    statuses = {i.status for i in items}
    if "waiting" in statuses:
        return "waiting"
    if statuses - FINISHED:
        return "running"
    return "failed" if "failed" in statuses else "done"
