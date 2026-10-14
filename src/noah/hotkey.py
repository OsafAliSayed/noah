"""Push-to-talk: hold a key to talk, release to send. Uses pynput, which works on X11.

X11 auto-repeat turns a held key into a stream of release+press pairs, so a release only
counts once no press follows within a few tens of milliseconds.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

AUTOREPEAT_GAP = 0.06


def parse_key(name: str) -> Any:
    """'ctrl_r', 'f9', 'pause' → a pynput Key; a single character → that character."""
    from pynput import keyboard

    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    try:
        return keyboard.Key[name]
    except KeyError:
        raise ValueError(f"Unknown key {name!r}; try ctrl_r, alt_r, f9 or pause.") from None


class PushToTalk:
    def __init__(
        self,
        key: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        listener_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.key_name = key
        self.on_press = on_press
        self.on_release = on_release
        self.listener_factory = listener_factory
        self.held = False
        self._pending_release: asyncio.TimerHandle | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._listener: Any = None

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        target = parse_key(self.key_name) if self.listener_factory is None else self.key_name
        if self.listener_factory is None:
            from pynput import keyboard

            factory: Callable[..., Any] = keyboard.Listener
        else:
            factory = self.listener_factory

        def matches(key: Any) -> bool:
            return key == target

        def pressed(key: Any) -> None:
            if matches(key) and self._loop:
                self._loop.call_soon_threadsafe(self.key_down)

        def released(key: Any) -> None:
            if matches(key) and self._loop:
                self._loop.call_soon_threadsafe(self.key_up)

        self._listener = factory(on_press=pressed, on_release=released)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def key_down(self) -> None:
        if self._pending_release is not None:  # auto-repeat: still held
            self._pending_release.cancel()
            self._pending_release = None
            return
        if not self.held:
            self.held = True
            self.on_press()

    def key_up(self) -> None:
        if not self.held or self._loop is None:
            return
        if self._pending_release is not None:
            self._pending_release.cancel()
        self._pending_release = self._loop.call_later(AUTOREPEAT_GAP, self._released)

    def _released(self) -> None:
        self._pending_release = None
        self.held = False
        self.on_release()
