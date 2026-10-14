from datetime import UTC, datetime

from noah.tasks.announcer import Announcer, Verbosity, checklist_text, join_words, number_word
from noah.tasks.manager import TaskEvent
from noah.tasks.models import Item, ItemStatus, Task
from noah.tasks.notifier import GdbusNotifier

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
TEXTS = ["Create the folder", "Write the README", "Init git"]


def _task(*statuses: ItemStatus, note: str | None = None) -> Task:
    items = tuple(
        Item(1, n, TEXTS[n - 1], s, note if s in ("failed", "waiting") else None)
        for n, s in enumerate(statuses, 1)
    )
    return Task(1, "Set up test-noah", T0, T0, items)


def _changed(task: Task, number: int, previous: ItemStatus = "in_progress", **kw) -> TaskEvent:
    return TaskEvent("item_changed", task, task.item(number), previous, **kw)


class Harness:
    def __init__(self, verbosity: Verbosity = "normal"):
        self.lines: list[str] = []
        self.now = 0.0
        self.timers: list[float] = []
        self.announcer = Announcer(
            self.lines.append,
            verbosity=verbosity,
            group_seconds=4,
            clock=lambda: self.now,
            call_later=lambda delay, fn: self.timers.append(delay),
        )


def test_words():
    assert number_word(3, capital=True) == "Three"
    assert number_word(42) == "42"
    assert join_words(["a"]) == "a"
    assert join_words(["a", "b"]) == "a and b"
    assert join_words(["a", "b", "c"]) == "a, b, and c"


def test_plan_line_lists_the_steps():
    h = Harness()
    h.announcer.handle(TaskEvent("created", _task("pending", "pending", "pending")))
    assert h.lines == ["Got it. Three steps: create the folder, write the README, and init git."]


def test_normal_groups_items_that_finish_close_together():
    h = Harness()
    h.announcer.handle(_changed(_task("done", "pending", "pending"), 1))
    h.now = 2
    h.announcer.handle(_changed(_task("done", "in_progress", "pending"), 2, "pending"))
    h.announcer.handle(_changed(_task("done", "done", "pending"), 2))
    assert h.lines == []
    assert h.timers == [4]
    h.now = 4.5
    h.announcer.flush_due()
    assert h.lines == ["Two of three done: create the folder and write the README."]


def test_a_late_item_starts_a_new_group():
    h = Harness()
    h.announcer.handle(_changed(_task("done", "pending", "pending"), 1))
    h.now = 10
    h.announcer.handle(_changed(_task("done", "done", "pending"), 2))
    assert h.lines == ["One of three done: create the folder."]
    h.announcer.flush()
    assert h.lines[-1] == "Two of three done: write the README."


def test_finishing_replaces_the_pending_group():
    h = Harness()
    h.announcer.handle(_changed(_task("done", "done", "pending"), 2))
    h.announcer.handle(_changed(_task("done", "done", "done"), 3))
    assert h.lines == ["All done: Set up test-noah."]


def test_failure_and_waiting_flush_first():
    h = Harness()
    h.announcer.handle(_changed(_task("done", "pending", "pending"), 1))
    h.announcer.handle(_changed(_task("done", "failed", "pending", note="disk full"), 2))
    h.announcer.handle(_changed(_task("done", "failed", "waiting", note="pick a name"), 3))
    assert h.lines == [
        "One of three done: create the folder.",
        "Couldn't write the README. Disk full.",
        "I need your OK to pick a name.",
    ]


def test_confirmation_prompts_ask_for_themselves():
    h = Harness()
    task = _task("waiting", note="run `mkdir x`")
    h.announcer.handle(_changed(task, 1, asking=True))
    assert h.lines == []


def test_quiet_only_speaks_about_start_end_and_problems():
    h = Harness("quiet")
    h.announcer.handle(TaskEvent("created", _task("pending", "pending")))
    h.announcer.handle(_changed(_task("done", "pending"), 1))
    h.announcer.flush()
    h.announcer.handle(_changed(_task("done", "failed", note="no network"), 2))
    assert h.lines == [
        "Got it. Two steps: create the folder and write the README.",
        "Finished Set up test-noah, but one step failed.",
    ]


def test_chatty_speaks_every_change():
    h = Harness("chatty")
    h.announcer.handle(_changed(_task("in_progress", "pending"), 1, "pending"))
    h.announcer.handle(_changed(_task("done", "pending"), 1))
    h.announcer.handle(_changed(_task("done", "skipped", "pending"), 2, "pending"))
    assert h.lines == [
        "Working on: create the folder.",
        "One of two done: create the folder.",
        "Skipping: write the README.",
    ]


def test_finish_lines():
    h = Harness()
    h.announcer.handle(_changed(_task("skipped", "skipped"), 2))
    h.announcer.handle(_changed(_task("done", "skipped"), 2))
    assert h.lines == ["Stopped Set up test-noah.", "Done with Set up test-noah, skipping one."]


def test_checklist_text():
    assert checklist_text(_task("done", "in_progress", "pending")) == (
        "✓ Create the folder\n◐ Write the README\n○ Init git"
    )


async def test_notifications_update_in_place():
    calls: list[list[str]] = []

    async def run(cmd: list[str]) -> str:
        calls.append(cmd)
        return "(uint32 42,)\n"

    notifier = GdbusNotifier(run=run)
    h = Harness()
    h.announcer.notifier = notifier
    await h.announcer(TaskEvent("created", _task("pending", "pending")))
    await h.announcer(_changed(_task("done", "pending"), 1))

    replace_ids = [cmd[cmd.index("'Noah'") + 1] for cmd in calls]
    assert replace_ids == ["0", "42"]
    assert calls[1][cmd_index(calls[1], "summary")] == "'Set up test-noah · 1/2'"
    assert calls[1][cmd_index(calls[1], "body")] == "'✓ Create the folder\\n○ Write the README'"


def cmd_index(cmd: list[str], field: str) -> int:
    base = cmd.index("'Noah'")
    return base + {"summary": 3, "body": 4}[field]


async def test_notifier_failure_is_swallowed():
    from noah.tools.registry import ToolError

    async def run(cmd: list[str]) -> str:
        raise ToolError("no session bus")

    await GdbusNotifier(run=run).show(1, "t", "b")
