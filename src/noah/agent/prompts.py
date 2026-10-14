"""The system prompt, rebuilt on every model call so the live context stays current."""

import getpass
import os
import platform
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from noah.power import read_charger


@dataclass(frozen=True)
class Context:
    now: datetime
    user: str
    home: Path
    os_name: str
    desktop: str
    power: str


def gather_context(env: Mapping[str, str] | None = None) -> Context:
    env = os.environ if env is None else env
    try:
        os_name = platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
    except OSError:
        os_name = platform.system()
    session = env.get("XDG_SESSION_TYPE", "unknown session")
    desktop = f"{env.get('XDG_CURRENT_DESKTOP', 'unknown desktop')} on {session}"
    charger = read_charger()
    power = "unknown" if charger is None else ("on charger" if charger.online else "on battery")
    return Context(
        now=datetime.now().astimezone(),
        user=getpass.getuser(),
        home=Path.home(),
        os_name=os_name,
        desktop=desktop,
        power=power,
    )


def build_system_prompt(context: Context) -> str:
    return f"""\
You are Noah, a voice assistant running on the user's own laptop. You help them get things \
done on their computer, like Jarvis.

How to reply:
- Your replies are spoken aloud. Keep them short: one to three plain sentences.
- No markdown, lists, headings, code blocks or emoji. Say file paths and commands only when \
the user needs them.
- Be direct and warm. Don't repeat the question back.

How to act:
- Use your tools to do things or to look things up. Never say you did something unless a tool \
result confirms it.
- If no tool can do what's asked, say so plainly and suggest what the user can do instead.
- If a request is ambiguous and acting on a guess could cause harm, ask one short question first.

Current context:
- Time: {context.now:%A %d %B %Y, %H:%M %Z}
- User: {context.user} (home: {context.home})
- System: {context.os_name}, {context.desktop}
- Power: {context.power}
"""


def current_system_prompt() -> str:
    return build_system_prompt(gather_context())
