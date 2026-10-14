"""Desktop notifications that update in place, one per task.

Ubuntu 22.04's notify-send (libnotify 0.7.9) can't replace a notification, so this talks to
the notification server over D-Bus with `gdbus`, which returns the id to replace next time.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Protocol

from noah.tools import _proc
from noah.tools.registry import ToolError

log = logging.getLogger(__name__)

Run = Callable[[list[str]], Awaitable[str]]


class Notifier(Protocol):
    async def show(self, key: int, summary: str, body: str) -> None:
        """Show a notification, replacing the previous one shown with the same key."""
        ...


def _gvariant_string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return f"'{escaped}'"


def _markup_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class GdbusNotifier:
    def __init__(self, run: Run = _proc.run, expire_ms: int = 8000) -> None:
        self.run = run
        self.expire_ms = expire_ms
        self._ids: dict[int, int] = {}

    async def show(self, key: int, summary: str, body: str) -> None:
        cmd = [
            "gdbus", "call", "--session",
            "--dest", "org.freedesktop.Notifications",
            "--object-path", "/org/freedesktop/Notifications",
            "--method", "org.freedesktop.Notifications.Notify",
            "'Noah'", str(self._ids.get(key, 0)), "''",
            _gvariant_string(summary), _gvariant_string(_markup_escape(body)),
            "[]", "{}", str(self.expire_ms),
        ]  # fmt: skip
        try:
            output = await self.run(cmd)
        except ToolError as e:
            log.warning("notification failed: %s", e)
            return
        if match := re.search(r"uint32 (\d+)", output):
            self._ids[key] = int(match.group(1))
