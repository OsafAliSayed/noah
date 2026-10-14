from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from noah.tasks.manager import TaskError, TaskEvent, TaskManager
from noah.tasks.models import Item, task_status
from noah.tasks.store import TaskStore


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


async def _manager(tmp_path: Path) -> tuple[TaskManager, list[TaskEvent]]:
    manager = TaskManager(TaskStore(tmp_path / "noah.db"), clock=Clock())
    events: list[TaskEvent] = []

    async def record(event: TaskEvent) -> None:
        events.append(event)

    manager.subscribers.append(record)
    return manager, events


def _items(*statuses: str) -> tuple[Item, ...]:
    return tuple(Item(1, n, f"step {n}", s) for n, s in enumerate(statuses, 1))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("pending", "pending"), "running"),
        (("done", "in_progress"), "running"),
        (("done", "waiting", "pending"), "waiting"),
        (("done", "failed", "pending"), "running"),
        (("done", "done"), "done"),
        (("done", "skipped"), "done"),
        (("done", "failed", "skipped"), "failed"),
    ],
)
def test_task_status_is_derived_from_items(statuses, expected):
    assert task_status(_items(*statuses)) == expected


async def test_store_round_trip(tmp_path: Path):
    store = TaskStore(tmp_path / "sub" / "noah.db")
    now = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    task = await store.create("Set up", ["mkdir", "git init"], now)
    await store.set_item(task.id, 1, "done", "made it", now + timedelta(seconds=5))
    task = await store.add_items(task.id, ["open VS Code"], now + timedelta(seconds=9))
    await store.close()

    reopened = TaskStore(tmp_path / "sub" / "noah.db")
    loaded = await reopened.get(task.id)
    assert loaded is not None
    assert loaded == task
    assert [(i.number, i.text, i.status, i.note) for i in loaded.items] == [
        (1, "mkdir", "done", "made it"),
        (2, "git init", "pending", None),
        (3, "open VS Code", "pending", None),
    ]
    assert loaded.updated_at == now + timedelta(seconds=9)
    assert await reopened.get(999) is None
    await reopened.close()


async def test_store_recent_is_newest_first(tmp_path: Path):
    store = TaskStore(tmp_path / "noah.db")
    now = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    a = await store.create("a", ["x"], now)
    b = await store.create("b", ["x"], now + timedelta(seconds=1))
    await store.set_item(a.id, 1, "done", None, now + timedelta(seconds=2))
    assert [t.title for t in await store.recent()] == ["a", "b"]
    assert [t.id for t in await store.recent(limit=1)] == [a.id]
    assert b.id != a.id
    await store.close()


async def test_manager_emits_events_and_tracks_open_tasks(tmp_path: Path):
    manager, events = await _manager(tmp_path)
    task = await manager.create("Project", ["one", " ", "two"])
    assert [i.text for i in task.items] == ["one", "two"]
    assert manager.open_tasks() == [task]

    await manager.update_item(task.id, 1, "in_progress")
    assert manager.active_item() == Item(task.id, 1, "one", "in_progress")
    await manager.update_item(task.id, 1, "in_progress", "same status, no event")
    await manager.update_item(task.id, 1, "done")
    await manager.update_item(task.id, 2, "done")

    assert [(e.kind, e.previous) for e in events] == [
        ("created", None),
        ("item_changed", "pending"),
        ("item_changed", "in_progress"),
        ("item_changed", "pending"),
    ]
    assert events[-1].task.status == "done"
    assert manager.open_tasks() == []
    await manager.store.close()


async def test_manager_rejects_unknown_tasks_and_items(tmp_path: Path):
    manager, _ = await _manager(tmp_path)
    with pytest.raises(TaskError, match="no task 5"):
        await manager.update_item(5, 1, "done")
    task = await manager.create("t", ["a"])
    with pytest.raises(TaskError, match="no item 3"):
        await manager.update_item(task.id, 3, "done")
    with pytest.raises(TaskError, match="at least one item"):
        await manager.create("t", ["  "])
    await manager.store.close()


async def test_open_tasks_survive_a_restart(tmp_path: Path):
    manager, _ = await _manager(tmp_path)
    open_task = await manager.create("open", ["a", "b"])
    done_task = await manager.create("done", ["a"])
    await manager.update_item(done_task.id, 1, "done")
    await manager.store.close()

    restarted = TaskManager(TaskStore(tmp_path / "noah.db"))
    assert [t.id for t in await restarted.load()] == [open_task.id]
    assert [t.id for t in await restarted.get_tasks("finished")] == [done_task.id]
    assert len(await restarted.get_tasks("all")) == 2
    await restarted.store.close()


async def test_cancel_skips_unfinished_items(tmp_path: Path):
    manager, _ = await _manager(tmp_path)
    task = await manager.create("t", ["a", "b", "c"])
    await manager.update_item(task.id, 1, "done")
    task = await manager.cancel(task.id)
    assert [i.status for i in task.items] == ["done", "skipped", "skipped"]
    assert task.status == "done"
    assert manager.open_tasks() == []
    await manager.store.close()


async def test_gate_marks_the_active_item_waiting_while_asking(tmp_path: Path):
    manager, events = await _manager(tmp_path)
    task = await manager.create("t", ["a"])
    seen = []

    async def confirm(action: str) -> bool:
        current = manager.open_tasks()[0].item(1)
        assert current is not None
        seen.append((action, current.status, current.note))
        return True

    gated = manager.gate(confirm)
    assert await gated("run `ls`")  # nothing in progress: just asks
    await manager.update_item(task.id, 1, "in_progress")
    assert await gated("run `mkdir x`")

    assert seen == [("run `ls`", "pending", None), ("run `mkdir x`", "waiting", "run `mkdir x`")]
    assert manager.open_tasks()[0].items[0].status == "in_progress"
    assert any(e.asking for e in events)
    await manager.store.close()


async def test_broken_subscriber_does_not_break_updates(tmp_path: Path):
    manager, _ = await _manager(tmp_path)

    async def broken(event: TaskEvent) -> None:
        raise RuntimeError("speaker on fire")

    manager.subscribers.insert(0, broken)
    task = await manager.create("t", ["a"])
    assert task.id
    await manager.store.close()


def test_turn_timer_logs_marks(tmp_path: Path):
    import json

    from noah.metrics import TurnTimer

    now = [10.0]
    timer = TurnTimer(tmp_path / "timing.log", clock=lambda: now[0], memory=lambda: 321.0)
    now[0] = 10.4
    timer.mark("llm_first_token")
    timer.mark("llm_first_token", at=99)  # only the first mark counts
    timer.mark("first_audio", at=10.9)
    entry = timer.record(mode="text")
    assert entry["llm_first_token"] == 0.4
    assert entry["first_audio"] == 0.9
    assert json.loads((tmp_path / "timing.log").read_text())["rss_mb"] == 321.0
