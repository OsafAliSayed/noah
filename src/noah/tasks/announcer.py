"""Turns task events into short progress lines, from templates rather than an extra LLM call.

Lines go to `say` (printed in `noah chat`, spoken by the voice daemon). Every event also
updates the task's desktop notification. Verbosity decides which events get a line:

- quiet: a task starting, finishing, failing or needing the user.
- normal: also finished items, grouped when several finish within a few seconds.
- chatty: every status change, ungrouped.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from noah.tasks.manager import TaskEvent
from noah.tasks.models import Item, ItemStatus, Task
from noah.tasks.notifier import Notifier

Verbosity = Literal["quiet", "normal", "chatty"]
Say = Callable[[str], None]
CallLater = Callable[[float, Callable[[], None]], object]

GROUP_SECONDS = 4.0

_WORDS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty",
)  # fmt: skip

SYMBOLS: dict[ItemStatus, str] = {
    "pending": "○",
    "in_progress": "◐",
    "done": "✓",
    "failed": "✗",
    "waiting": "?",
    "skipped": "↷",
}


def number_word(n: int, capital: bool = False) -> str:
    word = _WORDS[n] if 0 <= n < len(_WORDS) else str(n)
    return word.capitalize() if capital else word


def _lower_first(text: str) -> str:
    # "Create the folder" → "create the folder", but leave "README" and "VS Code" alone.
    if len(text) > 1 and text[0].isupper() and text[1].islower():
        return text[0].lower() + text[1:]
    return text


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def join_words(parts: list[str]) -> str:
    if len(parts) <= 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def plan_line(task: Task) -> str:
    n = len(task.items)
    steps = join_words([_lower_first(i.text) for i in task.items])
    if n == 1:
        return _sentence(f"Got it. One step: {steps}")
    return _sentence(f"Got it. {number_word(n, capital=True)} steps: {steps}")


def progress_line(task: Task, items: list[Item]) -> str:
    texts = join_words([_lower_first(i.text) for i in items])
    total = number_word(len(task.items))
    return _sentence(f"{number_word(task.done_count, capital=True)} of {total} done: {texts}")


def finish_line(task: Task) -> str:
    failed = sum(i.status == "failed" for i in task.items)
    skipped = sum(i.status == "skipped" for i in task.items)
    if failed:
        steps = "step" if failed == 1 else "steps"
        return f"Finished {task.title}, but {number_word(failed)} {steps} failed."
    if task.done_count == 0:
        return _sentence(f"Stopped {task.title}")
    if skipped:
        return f"Done with {task.title}, skipping {number_word(skipped)}."
    return _sentence(f"All done: {task.title}")


def waiting_line(item: Item) -> str:
    if item.note:
        return _sentence(f"I need your OK to {item.note}")
    return _sentence(f"Step {item.number} needs you: {_lower_first(item.text)}")


def failed_line(item: Item) -> str:
    line = _sentence(f"Couldn't {_lower_first(item.text)}")
    if not item.note:
        return line
    return f"{line} {_sentence(item.note[:1].upper() + item.note[1:])}"


def checklist_text(task: Task) -> str:
    return "\n".join(f"{SYMBOLS[i.status]} {i.text}" for i in task.items)


@dataclass
class _Group:
    task: Task
    started: float
    items: list[Item] = field(default_factory=list)


def _default_call_later(delay: float, fn: Callable[[], None]) -> object:
    try:
        return asyncio.get_running_loop().call_later(delay, fn)
    except RuntimeError:
        return None  # no loop: the group is flushed by the next event or flush()


class Announcer:
    def __init__(
        self,
        say: Say,
        notifier: Notifier | None = None,
        verbosity: Verbosity = "normal",
        group_seconds: float = GROUP_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        call_later: CallLater = _default_call_later,
    ) -> None:
        self.say = say
        self.notifier = notifier
        self.verbosity: Verbosity = verbosity
        self.group_seconds = group_seconds
        self.clock = clock
        self.call_later = call_later
        self._group: _Group | None = None

    async def __call__(self, event: TaskEvent) -> None:
        self.handle(event)
        if self.notifier:
            task = event.task
            summary = f"{task.title} · {task.done_count}/{len(task.items)}"
            await self.notifier.show(task.id, summary, checklist_text(task))

    def handle(self, event: TaskEvent) -> None:
        task = event.task
        if self._group and (
            self._group.task.id != task.id
            or self.clock() - self._group.started >= self.group_seconds
        ):
            self.flush()

        if event.kind == "created":
            self.say(plan_line(task))
        elif event.kind == "items_added":
            if self.verbosity != "quiet":
                self.say(f"Added more steps, that makes {number_word(len(task.items))}.")
        elif event.item is not None:
            self._item_changed(task, event.item, event.previous, event.asking)

    def _item_changed(
        self, task: Task, item: Item, previous: ItemStatus | None, asking: bool
    ) -> None:
        if task.finished:
            self._group = None  # the finish line covers whatever was waiting to be said
            self.say(finish_line(task))
            return
        match item.status:
            case "waiting":
                self.flush()
                if not asking:  # a confirmation prompt asks for itself
                    self.say(waiting_line(item))
            case "failed":
                self.flush()
                self.say(failed_line(item))
            case "done" if self.verbosity == "chatty":
                self.say(progress_line(task, [item]))
            case "done" if self.verbosity == "normal":
                self._add_to_group(task, item)
            case "in_progress" if self.verbosity == "chatty" and previous != "waiting":
                self.say(_sentence(f"Working on: {_lower_first(item.text)}"))
            case "skipped" if self.verbosity == "chatty":
                self.say(_sentence(f"Skipping: {_lower_first(item.text)}"))
            case _:
                pass

    def _add_to_group(self, task: Task, item: Item) -> None:
        if self._group is None:
            self._group = _Group(task, self.clock())
            self.call_later(self.group_seconds, self.flush_due)
        self._group.task = task
        self._group.items.append(item)

    def flush_due(self) -> None:
        """Say the grouped update if its time window has passed."""
        if self._group and self.clock() - self._group.started >= self.group_seconds:
            self.flush()

    def flush(self) -> None:
        """Say the grouped update now, e.g. at the end of a turn or before asking the user."""
        group, self._group = self._group, None
        if group and group.items:
            self.say(progress_line(group.task, group.items))
